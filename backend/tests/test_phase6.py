"""
Phase 6 tests: Corrections + Recompute + Smart Income-Tax Rule.

All tests use synthetic ParsedSlipPayload objects — no real files, no DB, no HTTP.

Test inventory (10 tests):
  1.  apply_corrections_to_payload — summary.gross corrected, audit entry appended
  2.  apply_corrections_to_payload — line_items[<id>].value corrected, audit entry appended
  3.  apply_corrections_to_payload — re-correction of same field appends new audit entry
  4.  apply_corrections_to_payload — invalid summary field raises ValueError
  5.  apply_corrections_to_payload — non-existent line item id raises ValueError
  6.  recompute_anomalies — correcting income_tax from None to 1200 clears income-tax anomaly
  7.  recompute_anomalies — correcting gross upward triggers integrity failure
  8.  _check_income_tax_rule — gross=2400, no credits → estimated=240 > 100 → Warning
  9.  _check_income_tax_rule — gross=2400, credit_points=10 → estimated≤0 → Info below-threshold
  10. _check_income_tax_rule — income_tax present → returns None (no anomaly)

Smart tax rule constants (from parser.py):
    estimated = max(0, gross × 0.10 − credit_points × 242)
    estimated > 100  → Warning 'ano_missing_income_tax'
    estimated ≤ 0    → Info   'ano_below_tax_threshold'
    0 < estimated ≤ 100 → None  (borderline, skip)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers — build a minimal ParsedSlipPayload for testing
# ---------------------------------------------------------------------------

def _make_payload(
    *,
    gross: float | None = 12_000.0,
    net: float | None = 9_800.0,
    income_tax: float | None = 1_200.0,
    national_ins: float | None = 600.0,
    health: float | None = 400.0,
    credit_points: float | None = 2.25,
    line_items: list | None = None,
    corrections: list | None = None,
):
    """Build a minimal ParsedSlipPayload with sensible defaults."""
    from app.models.schemas import (
        ParsedSlipPayload,
        SlipMeta,
        SummaryTotals,
        LineItem,
        LineItemCategory,
        CorrectionEntry,
    )

    summary = SummaryTotals(
        gross=gross,
        net=net,
        income_tax=income_tax,
        national_insurance=national_ins,
        health_insurance=health,
        credit_points=credit_points,
        integrity_ok=True,
        integrity_notes=[],
    )

    slip_meta = SlipMeta(
        pay_month="2024-11",
        provider_guess="test",
        confidence=0.9,
        employer_name="TestCorp",
        employee_name_redacted=True,
    )

    default_line_items = [
        LineItem(
            id="li_gross",
            category=LineItemCategory.EARNING,
            description_hebrew="שכר בסיס",
            explanation_hebrew="שכר בסיס חודשי",
            value=12_000.0,
            raw_text="12000",
            confidence=0.95,
            page_index=0,
            is_unknown=False,
            unknown_guesses=[],
            unknown_question=None,
        ),
        LineItem(
            id="li_tax",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="מס הכנסה",
            explanation_hebrew="ניכוי מס הכנסה",
            value=1_200.0,
            raw_text="1200",
            confidence=0.90,
            page_index=0,
            is_unknown=False,
            unknown_guesses=[],
            unknown_question=None,
        ),
    ]

    return ParsedSlipPayload(
        slip_meta=slip_meta,
        summary=summary,
        line_items=line_items if line_items is not None else default_line_items,
        anomalies=[],
        blocks=[],
        tax_credits_detected=None,
        answers_applied=False,
        error_code=None,
        parse_source="mock",
        ocr_debug_preview=None,
        ytd=None,
        balances=[],
        corrections=corrections or [],
    )


# ---------------------------------------------------------------------------
# Test 1: Correcting summary.gross — audit entry appended
# ---------------------------------------------------------------------------

def test_apply_correction_summary_gross():
    """Correcting summary.gross updates the value and appends a CorrectionEntry."""
    from app.services.corrections import apply_corrections_to_payload

    payload = _make_payload(gross=12_000.0)
    raw = [{"field_path": "summary.gross", "corrected_value": 13_500.0}]

    updated = apply_corrections_to_payload(payload, raw)

    assert updated.summary.gross == 13_500.0, "Gross should be updated to 13_500"
    assert len(updated.corrections) == 1, "One correction entry should be appended"
    entry = updated.corrections[0]
    assert entry.field_path == "summary.gross"
    assert entry.original_value == 12_000.0
    assert entry.corrected_value == 13_500.0
    assert "T" in entry.corrected_at, "corrected_at should be an ISO timestamp"
    # Original payload unchanged (immutable)
    assert payload.summary.gross == 12_000.0, "Original payload must not be mutated"


# ---------------------------------------------------------------------------
# Test 2: Correcting a line item value — audit entry appended
# ---------------------------------------------------------------------------

def test_apply_correction_line_item_value():
    """Correcting line_items[li_tax].value updates that item and appends a CorrectionEntry."""
    from app.services.corrections import apply_corrections_to_payload

    payload = _make_payload()  # li_tax value = 1_200.0
    raw = [{"field_path": "line_items[li_tax].value", "corrected_value": 950.0}]

    updated = apply_corrections_to_payload(payload, raw)

    # Find updated li_tax
    li_tax = next(li for li in updated.line_items if li.id == "li_tax")
    assert li_tax.value == 950.0, "Line item value should be 950"
    assert len(updated.corrections) == 1
    entry = updated.corrections[0]
    assert entry.field_path == "line_items[li_tax].value"
    assert entry.original_value == 1_200.0
    assert entry.corrected_value == 950.0


# ---------------------------------------------------------------------------
# Test 3: Re-correcting same field appends a new audit entry
# ---------------------------------------------------------------------------

def test_apply_correction_appends_to_existing_trail():
    """Re-correcting an already-corrected field appends a new CorrectionEntry (audit trail grows)."""
    from app.services.corrections import apply_corrections_to_payload

    # Start with one existing correction in the trail
    from app.models.schemas import CorrectionEntry
    existing = CorrectionEntry(
        field_path="summary.gross",
        original_value=12_000.0,
        corrected_value=13_000.0,
        corrected_at="2024-11-01T10:00:00+00:00",
    )
    payload = _make_payload(gross=13_000.0, corrections=[existing])

    # Apply a second correction
    raw = [{"field_path": "summary.gross", "corrected_value": 14_000.0}]
    updated = apply_corrections_to_payload(payload, raw)

    assert updated.summary.gross == 14_000.0
    assert len(updated.corrections) == 2, "Both the old and new audit entries must be present"
    assert updated.corrections[0].corrected_value == 13_000.0, "First entry preserved"
    assert updated.corrections[1].corrected_value == 14_000.0, "New entry appended"


# ---------------------------------------------------------------------------
# Test 4: Invalid summary field raises ValueError
# ---------------------------------------------------------------------------

def test_invalid_summary_field_raises():
    """Attempting to correct a non-whitelisted summary field raises ValueError."""
    from app.services.corrections import apply_corrections_to_payload

    payload = _make_payload()
    raw = [{"field_path": "summary.integrity_ok", "corrected_value": 0}]

    with pytest.raises(ValueError, match="not correctable"):
        apply_corrections_to_payload(payload, raw)


# ---------------------------------------------------------------------------
# Test 5: Non-existent line item id raises ValueError
# ---------------------------------------------------------------------------

def test_nonexistent_line_item_raises():
    """Attempting to correct a line item that doesn't exist raises ValueError."""
    from app.services.corrections import apply_corrections_to_payload

    payload = _make_payload()
    raw = [{"field_path": "line_items[li_does_not_exist].value", "corrected_value": 500.0}]

    with pytest.raises(ValueError, match="not found"):
        apply_corrections_to_payload(payload, raw)


# ---------------------------------------------------------------------------
# Test 6: recompute_anomalies — income_tax corrected → anomaly clears
# ---------------------------------------------------------------------------

def test_recompute_income_tax_correction_clears_anomaly():
    """
    Payload with income_tax=None (→ Warning).
    After correcting income_tax to 1_200 and recomputing, the income-tax anomaly disappears.
    """
    from app.services.corrections import apply_corrections_to_payload, recompute_anomalies

    # Build payload where income_tax is missing → will emit ano_missing_income_tax
    payload = _make_payload(
        gross=12_000.0,
        income_tax=None,
        national_ins=600.0,
        health=400.0,
    )

    # Correct income_tax
    corrected = apply_corrections_to_payload(
        payload, [{"field_path": "summary.income_tax", "corrected_value": 1_200.0}]
    )
    assert corrected.summary.income_tax == 1_200.0

    # Recompute anomalies
    result = recompute_anomalies(corrected)
    anomaly_ids = {a.id for a in result.anomalies}
    assert "ano_missing_income_tax" not in anomaly_ids, \
        f"Income tax anomaly should be gone after correction; got {anomaly_ids}"


# ---------------------------------------------------------------------------
# Test 7: recompute_anomalies — gross corrected → integrity may update
# ---------------------------------------------------------------------------

def test_recompute_updates_integrity_notes():
    """
    Payload with gross=12_000, net=9_800, deductions summing to ~2_200 → integrity ok.
    After correcting gross to 50_000 the integrity check sees a big gap → integrity_ok=False.
    """
    from app.services.corrections import apply_corrections_to_payload, recompute_anomalies

    payload = _make_payload(
        gross=12_000.0,
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=600.0,
        health=400.0,
    )

    # Correct gross to a wildly different value to break integrity
    corrected = apply_corrections_to_payload(
        payload, [{"field_path": "summary.gross", "corrected_value": 50_000.0}]
    )
    result = recompute_anomalies(corrected)

    # With gross=50_000 and net=9_800 the computed gap is huge → integrity should fail
    assert result.summary.integrity_ok is False, \
        "Integrity check should fail after gross corrected to 50_000 with net=9_800"
    assert len(result.summary.integrity_notes) > 0, "Integrity notes should be non-empty"
    # Corrections audit trail must still be present
    assert len(result.corrections) == 1


# ---------------------------------------------------------------------------
# Test 8: Smart tax rule — gross above threshold, no credits → Warning
# ---------------------------------------------------------------------------

def test_income_tax_rule_emits_warning_when_above_threshold():
    """
    gross=5_000, credit_points=None → estimated = 5000×0.10 = 500 > 100
    With income_tax=None → Warning 'ano_missing_income_tax'.
    """
    from app.services.parser import _check_income_tax_rule
    from app.models.schemas import AnomalySeverity

    result = _check_income_tax_rule(
        gross=5_000.0,
        income_tax=None,
        credit_points=None,
    )
    assert result is not None, "Should return an Anomaly, not None"
    assert result.id == "ano_missing_income_tax"
    assert result.severity == AnomalySeverity.WARNING


# ---------------------------------------------------------------------------
# Test 9: Smart tax rule — gross + many credits → estimated≤0 → Info
# ---------------------------------------------------------------------------

def test_income_tax_rule_emits_info_when_below_threshold():
    """
    gross=2_000, credit_points=10 → estimated = max(0, 200 − 2420) = 0 ≤ 0
    With income_tax=None → Info 'ano_below_tax_threshold'.
    """
    from app.services.parser import _check_income_tax_rule
    from app.models.schemas import AnomalySeverity

    result = _check_income_tax_rule(
        gross=2_000.0,
        income_tax=None,
        credit_points=10.0,
    )
    assert result is not None, "Should return an Info Anomaly"
    assert result.id == "ano_below_tax_threshold"
    assert result.severity == AnomalySeverity.INFO
    assert "תקין" in result.what_we_found or "סף" in result.what_we_found, \
        "what_we_found should explain the below-threshold situation"


# ---------------------------------------------------------------------------
# Test 10: Smart tax rule — income_tax present → no anomaly
# ---------------------------------------------------------------------------

def test_income_tax_rule_no_anomaly_when_tax_detected():
    """
    When income_tax is present (regardless of gross / credit_points), no anomaly is emitted.
    """
    from app.services.parser import _check_income_tax_rule

    result = _check_income_tax_rule(
        gross=15_000.0,
        income_tax=2_500.0,
        credit_points=2.25,
    )
    assert result is None, "Should return None when income_tax is already detected"
