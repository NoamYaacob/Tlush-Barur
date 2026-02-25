"""
test_phase14.py — Tests for Phase 14: LLM Intelligence Layer (Gemini OCR Path).

Test list:
  1.  test_llm_extract_raises_when_no_api_key
  2.  test_llm_extract_returns_payload_on_success
  3.  test_llm_extract_gross_prefers_total_payments_other
  4.  test_llm_extract_privacy_provider_is_generic
  5.  test_llm_extract_deduction_values_positive
  6.  test_llm_extract_raises_on_invalid_json
  7.  test_llm_extract_truncates_to_12000_chars
  8.  test_llm_line_item_coerces_negative_value_to_positive
  9.  test_llm_line_item_coerces_unknown_category
  10. test_parse_source_is_ocr_llm
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def _make_valid_llm_response(
    gross_pay: float = 6223.70,
    total_payments_other: float = 6223.70,
    net_pay: float = 5354.20,
    income_tax: float = 420.00,
    national_insurance: float = 310.00,
    health_insurance: float = 139.50,
    pay_month: str = "2024-01",
    line_items: list | None = None,
) -> str:
    """Return a JSON string matching LLMExtractedPayload schema."""
    if line_items is None:
        line_items = [
            {"description_hebrew": "שכר בסיס", "category": "earning", "value": 6223.70},
            {"description_hebrew": "מס הכנסה", "category": "deduction", "value": income_tax},
            {"description_hebrew": "ביטוח לאומי", "category": "deduction", "value": national_insurance},
            {"description_hebrew": "מס בריאות", "category": "deduction", "value": health_insurance},
        ]
    return json.dumps({
        "gross_pay": gross_pay,
        "total_payments_other": total_payments_other,
        "net_pay": net_pay,
        "income_tax": income_tax,
        "national_insurance": national_insurance,
        "health_insurance": health_insurance,
        "pay_month": pay_month,
        "line_items": line_items,
    })


def _mock_genai(response_text: str):
    """
    Returns a mock for google.generativeai that makes generate_content()
    return a response with the given text.
    """
    mock_response = MagicMock()
    mock_response.text = response_text

    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response

    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    return mock_genai


# ---------------------------------------------------------------------------
# 1. Raises RuntimeError when GEMINI_API_KEY is not set
# ---------------------------------------------------------------------------

def test_llm_extract_raises_when_no_api_key():
    """
    llm_extract() must raise RuntimeError when GEMINI_API_KEY is absent,
    so the caller can silently fall back to the regex pipeline.
    """
    with patch.dict(os.environ, {}, clear=True):
        # Ensure any module-level cached key is also absent
        with patch("app.services.llm_parser._GEMINI_API_KEY", None):
            from app.services.llm_parser import llm_extract
            with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                llm_extract("ברוטו 6000 נטו 5000")


# ---------------------------------------------------------------------------
# 2. Returns a ParsedSlipPayload on success
# ---------------------------------------------------------------------------

def test_llm_extract_returns_payload_on_success():
    """
    With a mocked Gemini response, llm_extract() must return a ParsedSlipPayload.
    """
    from app.models.schemas import ParsedSlipPayload
    from app.services.llm_parser import llm_extract

    mock_genai = _mock_genai(_make_valid_llm_response())

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        with patch("app.services.llm_parser._GEMINI_API_KEY", "test-key"):
            with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
                result = llm_extract("ברוטו 6223 נטו 5354")

    assert isinstance(result, ParsedSlipPayload), (
        f"Expected ParsedSlipPayload, got {type(result)}"
    )


# ---------------------------------------------------------------------------
# 3. Gross comes from total_payments_other (not gross_taxable)
# ---------------------------------------------------------------------------

def test_llm_extract_gross_prefers_total_payments_other():
    """
    When both total_payments_other (6223.70) and gross_pay (6463.00) differ,
    summary.gross must equal total_payments_other — the actual employee pay.
    """
    from app.services.llm_parser import llm_extract

    response = json.dumps({
        "gross_pay": 6463.00,           # tax-gross (should NOT win)
        "total_payments_other": 6223.70, # actual pay (SHOULD win)
        "net_pay": 5354.20,
        "income_tax": 420.00,
        "national_insurance": 310.00,
        "health_insurance": 139.50,
        "pay_month": "2024-01",
        "line_items": [
            {"description_hebrew": "שכר בסיס", "category": "earning", "value": 6223.70},
        ],
    })
    mock_genai = _mock_genai(response)

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        with patch("app.services.llm_parser._GEMINI_API_KEY", "test-key"):
            with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
                result = llm_extract("ברוטו 6223 נטו 5354")

    assert result.summary.gross == 6223.70, (
        f"summary.gross should be 6223.70 (total_payments_other), got {result.summary.gross}"
    )


# ---------------------------------------------------------------------------
# 4. Privacy — provider_guess is always generic
# ---------------------------------------------------------------------------

def test_llm_extract_privacy_provider_is_generic():
    """
    slip_meta.provider_guess must ALWAYS be 'ספק שכר' (generic) — never the
    actual company name, regardless of what's in the OCR text.
    """
    from app.services.llm_parser import llm_extract

    # Include provider name in OCR text — LLM should NOT echo it back
    ocr_with_provider = "חברת הר-גל בע\"מ\nברוטו 6000 נטו 5000"
    mock_genai = _mock_genai(_make_valid_llm_response())

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        with patch("app.services.llm_parser._GEMINI_API_KEY", "test-key"):
            with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
                result = llm_extract(ocr_with_provider)

    assert result.slip_meta.provider_guess == "ספק שכר", (
        f"provider_guess must be 'ספק שכר', got {result.slip_meta.provider_guess!r}"
    )
    assert result.slip_meta.employer_name is None, (
        f"employer_name must be None, got {result.slip_meta.employer_name!r}"
    )
    assert result.slip_meta.employee_name_redacted is True, (
        "employee_name_redacted must always be True"
    )


# ---------------------------------------------------------------------------
# 5. Deduction line item values are always positive
# ---------------------------------------------------------------------------

def test_llm_extract_deduction_values_positive():
    """
    All line items with category='deduction' must have positive values
    (the LLM validator coerces negatives via abs()).
    """
    from app.services.llm_parser import llm_extract
    from app.models.schemas import LineItemCategory

    # Simulate LLM returning negative deduction values (instruction violation)
    response = json.dumps({
        "gross_pay": 6000.00,
        "net_pay": 5000.00,
        "income_tax": 420.00,
        "pay_month": "2024-01",
        "line_items": [
            {"description_hebrew": "מס הכנסה", "category": "deduction", "value": -420.00},
            {"description_hebrew": "שכר בסיס", "category": "earning", "value": 6000.00},
        ],
    })
    mock_genai = _mock_genai(response)

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        with patch("app.services.llm_parser._GEMINI_API_KEY", "test-key"):
            with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
                result = llm_extract("ברוטו 6000 נטו 5000")

    deduction_items = [
        li for li in result.line_items
        if li.category == LineItemCategory.DEDUCTION
    ]
    assert deduction_items, "Expected at least one deduction line item"
    for li in deduction_items:
        assert li.value is not None and li.value > 0, (
            f"Deduction '{li.description_hebrew}' has non-positive value: {li.value}"
        )


# ---------------------------------------------------------------------------
# 6. Raises on invalid JSON (so caller can fall back)
# ---------------------------------------------------------------------------

def test_llm_extract_raises_on_invalid_json():
    """
    When Gemini returns non-JSON text (e.g. a plain explanation), llm_extract()
    must raise json.JSONDecodeError so the caller falls back to regex.
    """
    import json as json_module
    from app.services.llm_parser import llm_extract

    mock_genai = _mock_genai("Sorry, I cannot extract data from this text.")

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        with patch("app.services.llm_parser._GEMINI_API_KEY", "test-key"):
            with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
                with pytest.raises(json_module.JSONDecodeError):
                    llm_extract("ברוטו 6000 נטו 5000")


# ---------------------------------------------------------------------------
# 7. Input text is truncated to _MAX_INPUT_CHARS
# ---------------------------------------------------------------------------

def test_llm_extract_truncates_to_12000_chars():
    """
    llm_extract() must truncate the OCR text to _MAX_INPUT_CHARS (12,000)
    before building the prompt, to control API cost and latency.
    """
    from app.services.llm_parser import llm_extract, _MAX_INPUT_CHARS

    captured_prompts: list[str] = []
    mock_response = MagicMock()
    mock_response.text = _make_valid_llm_response()

    mock_model = MagicMock()
    def capture_prompt(prompt, **kwargs):
        captured_prompts.append(prompt)
        return mock_response
    mock_model.generate_content.side_effect = capture_prompt

    mock_genai = MagicMock()
    mock_genai.GenerativeModel.return_value = mock_model

    # Build input text that exceeds the limit
    long_text = "שכר " * 5000   # ~20,000 chars

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        with patch("app.services.llm_parser._GEMINI_API_KEY", "test-key"):
            with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
                llm_extract(long_text)

    assert captured_prompts, "generate_content() was never called"
    prompt_used = captured_prompts[0]

    # The OCR TEXT block in the prompt must not exceed _MAX_INPUT_CHARS
    # (the system instruction adds overhead, so we check the OCR section only)
    ocr_start = prompt_used.find("---OCR TEXT START---")
    ocr_end = prompt_used.find("---OCR TEXT END---")
    assert ocr_start != -1 and ocr_end != -1, "OCR section markers not found in prompt"
    ocr_section = prompt_used[ocr_start:ocr_end]
    assert len(ocr_section) <= _MAX_INPUT_CHARS + 100, (  # +100 for markers
        f"OCR section in prompt exceeds {_MAX_INPUT_CHARS} chars: {len(ocr_section)}"
    )


# ---------------------------------------------------------------------------
# 8. LLMLineItem coerces negative values to positive
# ---------------------------------------------------------------------------

def test_llm_line_item_coerces_negative_value_to_positive():
    """
    LLMLineItem.value validator must convert negative floats to positive (abs).
    """
    from app.services.llm_parser import LLMLineItem

    item = LLMLineItem(description_hebrew="מס הכנסה", category="deduction", value=-420.0)
    assert item.value == 420.0, (
        f"Expected value coerced to 420.0, got {item.value}"
    )


# ---------------------------------------------------------------------------
# 9. LLMLineItem coerces unknown category to "earning"
# ---------------------------------------------------------------------------

def test_llm_line_item_coerces_unknown_category():
    """
    LLMLineItem.category validator must coerce unrecognised category strings
    to "earning" rather than raising a validation error.
    """
    from app.services.llm_parser import LLMLineItem

    item = LLMLineItem(
        description_hebrew="רכיב לא מוכר",
        category="unknown_category",
        value=100.0,
    )
    assert item.category == "earning", (
        f"Expected 'earning' after coercion, got {item.category!r}"
    )


# ---------------------------------------------------------------------------
# 10. parse_source is "ocr_llm"
# ---------------------------------------------------------------------------

def test_parse_source_is_ocr_llm():
    """
    ParsedSlipPayload returned by llm_extract() must have parse_source='ocr_llm'.
    """
    from app.services.llm_parser import llm_extract

    mock_genai = _mock_genai(_make_valid_llm_response())

    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        with patch("app.services.llm_parser._GEMINI_API_KEY", "test-key"):
            with patch.dict("sys.modules", {"google.generativeai": mock_genai}):
                result = llm_extract("ברוטו 6223 נטו 5354")

    assert result.parse_source == "ocr_llm", (
        f"Expected parse_source='ocr_llm', got {result.parse_source!r}"
    )
