"""
test_phase16.py — Tests for Phase 16: Universal Israeli Payslip Engine (Hilan Fix).

Regression tests for four bugs found in Hilan-format payslips:
  1. Gross picking ברוטו למס (29,885) instead of סה"כ תשלומים (25,089.32)
  2. Net picking שכר נטו (15,940.32) instead of נטו לתשלום (14,339.60)
  3. Income tax (6,119) and health insurance (1,387) missed (summary table only)
  4. Credit points (2.25) confused with gross_ni (monetary field)

Also tests the new Phase 16 accounting guardrail (_validate_accounting).

Test list:
  1.  test_hilan_gross_comes_from_total_payments
  2.  test_hilan_net_is_final_bank_amount
  3.  test_hilan_income_tax_from_summary_table
  4.  test_credit_points_not_confused_with_gross_ni
  5.  test_accounting_guardrail_fires_on_discrepancy
  6.  test_accounting_guardrail_silent_when_net_matches
  7.  test_accounting_guardrail_silent_when_inputs_missing
  8.  test_privacy_llm_payload_has_no_name_fields
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures & helpers — same pattern as test_phase14.py
# ---------------------------------------------------------------------------

def _mock_genai(response_text: str):
    """
    Returns a mock for groq.Groq whose chat.completions.create() returns a
    ChatCompletion-like object with choices[0].message.content == response_text.

    Named _mock_genai to keep call sites unchanged (Phase 16.6 migration from Gemini).
    """
    mock_message = MagicMock()
    mock_message.content = response_text

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_completion

    mock_groq_cls = MagicMock(return_value=mock_client)
    return mock_groq_cls


def _make_hilan_llm_response(
    gross_pay: float = 29_885.00,          # ברוטו למס — must NOT become summary.gross
    total_payments_other: float = 25_089.32,  # סה"כ תשלומים — must become summary.gross
    net_pay: float = 14_339.60,            # נטו לתשלום — must become summary.net
    income_tax: float = 6_119.00,          # from summary/taxes table
    national_insurance: float = 1_500.00,
    health_insurance: float = 1_387.00,    # from summary/taxes table
    gross_taxable: float = 29_885.00,      # ברוטו למס הכנסה (tax-calc field only)
    gross_ni: float = 24_000.00,           # ברוטו לביטוח לאומי (ss-calc field only)
    credit_points: float = 2.25,           # must NOT be confused with gross_ni
    pay_month: str = "2024-03",
    line_items: "list | None" = None,
) -> str:
    """
    Return a JSON string mimicking a Hilan-format LLM response.
    The line_items table intentionally omits income_tax and health_insurance
    rows (they appear only in Hilan's separate summary/taxes table at the
    top-level JSON fields) to simulate the cross-table extraction scenario.
    """
    if line_items is None:
        line_items = [
            # Earnings table — no income_tax / health rows here (they are in the summary)
            {"description_hebrew": "שכר בסיס", "category": "earning", "value": 25_089.32},
            # Only NI visible in line items; income_tax and health come from summary table
            {"description_hebrew": "ביטוח לאומי", "category": "deduction", "value": national_insurance},
            {"description_hebrew": "פנסיה עובד", "category": "deduction", "value": 1_504.00},
            {"description_hebrew": "קרן השתלמות", "category": "deduction", "value": 627.23},
        ]
    return json.dumps({
        "gross_pay": gross_pay,
        "total_payments_other": total_payments_other,
        "net_pay": net_pay,
        "income_tax": income_tax,
        "national_insurance": national_insurance,
        "health_insurance": health_insurance,
        "gross_taxable": gross_taxable,
        "gross_ni": gross_ni,
        "credit_points": credit_points,
        "pay_month": pay_month,
        "line_items": line_items,
    })


def _run_llm_extract(response_json: str):
    """Run llm_extract() with a mocked Groq client and return the ParsedSlipPayload."""
    from app.services.llm_parser import llm_extract

    mock_groq_cls = _mock_genai(response_json)
    with patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        with patch("app.services.llm_parser._GROQ_API_KEY", "test-key"):
            with patch.dict("sys.modules", {"groq": MagicMock(Groq=mock_groq_cls)}):
                return llm_extract("תלוש שכר הילן לבדיקה")


# ---------------------------------------------------------------------------
# 1. Hilan: gross must come from total_payments_other, NOT gross_pay/ברוטו למס
# ---------------------------------------------------------------------------

def test_hilan_gross_comes_from_total_payments():
    """
    Hilan returns both gross_pay=29885 (ברוטו למס) and total_payments_other=25089.32
    (סה"כ תשלומים). summary.gross MUST be 25089.32.

    Regression for Phase 14 bug: LLM was picking ברוטו למס instead of סה"כ תשלומים.
    """
    response = _make_hilan_llm_response(
        gross_pay=29_885.00,
        total_payments_other=25_089.32,
    )
    result = _run_llm_extract(response)

    assert result.summary.gross == 25_089.32, (
        f"Expected summary.gross=25089.32 (סה\"כ תשלומים), got {result.summary.gross}"
    )
    assert result.summary.gross != 29_885.00, (
        "summary.gross must not be 29885 (ברוטו למס — tax-calculation base, not actual pay)"
    )
    # gross_taxable may correctly store 29885 (as a separate field)
    assert result.summary.gross_taxable == 29_885.00


# ---------------------------------------------------------------------------
# 2. Hilan: net must be the final bank transfer (נטו לתשלום), not שכר נטו
# ---------------------------------------------------------------------------

def test_hilan_net_is_final_bank_amount():
    """
    LLM returns net_pay=14339.60 (נטו לתשלום — final bank transfer).
    summary.net MUST be 14339.60, never 15940.32 (שכר נטו).

    Regression for Phase 14 bug: LLM was returning the pre-voluntary-deduction net.
    """
    result = _run_llm_extract(_make_hilan_llm_response(net_pay=14_339.60))

    assert result.summary.net == 14_339.60, (
        f"Expected summary.net=14339.60 (נטו לתשלום), got {result.summary.net}"
    )
    assert result.summary.net != 15_940.32, (
        "summary.net must not be 15940.32 (שכר נטו — pre-voluntary-deduction intermediate)"
    )


# ---------------------------------------------------------------------------
# 3. Hilan: income_tax and health_insurance extracted from summary table
# ---------------------------------------------------------------------------

def test_hilan_income_tax_from_summary_table():
    """
    In Hilan layout, income_tax (6119) and health_insurance (1387) appear only
    in the summary/taxes table, not in the line-items table. The LLM must still
    capture them at the top-level scalar fields.

    Verifies: summary.income_tax == 6119 and summary.health_insurance == 1387.
    Regression for Phase 14 bug: both fields were missed entirely.
    """
    # Line items intentionally have NO income_tax or health_insurance rows
    response = _make_hilan_llm_response(
        income_tax=6_119.00,
        health_insurance=1_387.00,
        line_items=[
            {"description_hebrew": "שכר בסיס", "category": "earning", "value": 25_089.32},
            {"description_hebrew": "ביטוח לאומי", "category": "deduction", "value": 1_500.00},
            # income_tax and health are absent from line items — only in summary table
        ],
    )
    result = _run_llm_extract(response)

    assert result.summary.income_tax == 6_119.00, (
        f"Expected income_tax=6119 from summary table, got {result.summary.income_tax}"
    )
    assert result.summary.health_insurance == 1_387.00, (
        f"Expected health_insurance=1387 from summary table, got {result.summary.health_insurance}"
    )


# ---------------------------------------------------------------------------
# 4. Credit points (2.25) must not be confused with gross_ni (24000)
# ---------------------------------------------------------------------------

def test_credit_points_not_confused_with_gross_ni():
    """
    LLM returns credit_points=2.25 and gross_ni=24000.
    summary.credit_points MUST be 2.25; summary.gross_ni MUST be 24000.

    Regression for Phase 14 bug: credit_points value was being assigned to gross_ni.
    """
    response = _make_hilan_llm_response(
        credit_points=2.25,
        gross_ni=24_000.00,
    )
    result = _run_llm_extract(response)

    assert result.summary.credit_points == 2.25, (
        f"Expected credit_points=2.25, got {result.summary.credit_points}"
    )
    assert result.summary.gross_ni == 24_000.00, (
        f"Expected gross_ni=24000, got {result.summary.gross_ni}"
    )
    assert result.summary.gross_ni != 2.25, (
        "gross_ni must not be 2.25 — that is the credit_points value, not a monetary gross"
    )


# ---------------------------------------------------------------------------
# 5. Accounting guardrail fires when net discrepancy > 1 ILS
# ---------------------------------------------------------------------------

def test_accounting_guardrail_fires_on_discrepancy():
    """
    _validate_accounting must return (False, message) when the extracted net
    differs from gross - total_deductions by more than 1 ILS.

    Simulates the Hilan bug: gross=25089.32, deductions sum from line items=8000,
    but extracted_net=14339.60 — the LLM missed ~2749 ILS of deductions.
    """
    from app.services.llm_parser import _validate_accounting

    gross = 25_089.32
    total_deductions = 8_000.00      # incomplete: LLM missed some deductions in line items
    net_pay = 14_339.60              # correct actual bank amount

    # computed_net = 25089.32 - 8000 = 17089.32
    # delta = |14339.60 - 17089.32| = 2749.72 >> 1.0 — guardrail must fire
    ok, msg = _validate_accounting(
        gross=gross,
        total_deductions=total_deductions,
        net_pay=net_pay,
    )

    assert ok is False, "Guardrail must fire (return False) when net discrepancy > 1 ILS"
    assert len(msg) > 20, "Warning message must be non-trivial and descriptive"
    # Message must contain numeric evidence
    assert any(s in msg for s in ["14339", "17089", "2749", "discrepancy"]), (
        f"Warning message should contain key numbers or 'discrepancy', got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# 6. Accounting guardrail is silent when net matches
# ---------------------------------------------------------------------------

def test_accounting_guardrail_silent_when_net_matches():
    """
    _validate_accounting must return (True, "") when gross - deductions == net
    (within 1 ILS tolerance). No false warnings on correct payslips.
    """
    from app.services.llm_parser import _validate_accounting

    # Exact match: 6223.70 - 869.50 = 5354.20
    ok, msg = _validate_accounting(
        gross=6_223.70,
        total_deductions=869.50,
        net_pay=5_354.20,
    )

    assert ok is True, f"Guardrail must be silent (True) when net is consistent, got ok={ok}"
    assert msg == "", f"Message must be empty when guardrail is silent, got: {msg!r}"


# ---------------------------------------------------------------------------
# 7. Accounting guardrail is silent when any input is None
# ---------------------------------------------------------------------------

def test_accounting_guardrail_silent_when_inputs_missing():
    """
    _validate_accounting must return (True, "") when any input is None.
    Cannot validate math without all three values — no false positives.
    """
    from app.services.llm_parser import _validate_accounting

    # Missing gross
    ok, msg = _validate_accounting(gross=None, total_deductions=500.0, net_pay=5_000.0)
    assert ok is True and msg == "", "Must be silent when gross is None"

    # Missing total_deductions
    ok, msg = _validate_accounting(gross=6_000.0, total_deductions=None, net_pay=5_000.0)
    assert ok is True and msg == "", "Must be silent when total_deductions is None"

    # Missing net_pay
    ok, msg = _validate_accounting(gross=6_000.0, total_deductions=500.0, net_pay=None)
    assert ok is True and msg == "", "Must be silent when net_pay is None"

    # All None
    ok, msg = _validate_accounting(gross=None, total_deductions=None, net_pay=None)
    assert ok is True and msg == "", "Must be silent when all inputs are None"


# ---------------------------------------------------------------------------
# 8. Privacy: LLMExtractedPayload has no employer/employee name fields
# ---------------------------------------------------------------------------

def test_privacy_llm_payload_has_no_name_fields():
    """
    LLMExtractedPayload must not declare employer_name or employee_name fields.
    Privacy is enforced by schema omission — the LLM cannot return what the
    schema doesn't accept, regardless of what the system instruction says.

    Also verifies that Pydantic v2 silently ignores extra PII fields in the
    raw dict returned by the LLM (extra="ignore" is the default behavior).
    """
    from app.services.llm_parser import LLMExtractedPayload

    model_fields = LLMExtractedPayload.model_fields

    assert "employer_name" not in model_fields, (
        "LLMExtractedPayload must NOT declare employer_name (privacy by omission)"
    )
    assert "employee_name" not in model_fields, (
        "LLMExtractedPayload must NOT declare employee_name (privacy by omission)"
    )

    # Verify that a raw dict containing PII fields is silently accepted by Pydantic
    # (extra fields are dropped, not raised) — this is the correct behavior for privacy.
    raw_with_pii = {
        "gross_pay": 6_000.0,
        "net_pay": 5_000.0,
        "employer_name": 'חברה בע"מ',      # should be silently dropped
        "employee_name": "ישראל ישראלי",   # should be silently dropped
        "line_items": [],
    }
    payload = LLMExtractedPayload.model_validate(raw_with_pii)

    assert not hasattr(payload, "employer_name"), (
        "Parsed payload must not expose employer_name attribute"
    )
    assert not hasattr(payload, "employee_name"), (
        "Parsed payload must not expose employee_name attribute"
    )
    # Core fields should parse correctly
    assert payload.gross_pay == 6_000.0
    assert payload.net_pay == 5_000.0
