"""
Unit tests for backend/app/services/ocr.py and parse_with_ocr() in parser.py.

Run from the backend/ directory:
    python -m pytest tests/test_ocr.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the backend package is importable when running from the backend/ dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ===========================================================================
# Test 1: check_ocr_deps returns a valid tuple
# ===========================================================================

def test_check_ocr_deps_returns_tuple():
    """
    check_ocr_deps() must always return (bool, list[str]).
    If available=True, missing list must be empty.
    If available=False, missing list must have >= 1 entry.
    """
    from app.services.ocr import check_ocr_deps

    available, missing = check_ocr_deps()

    assert isinstance(available, bool), "available must be a bool"
    assert isinstance(missing, list), "missing must be a list"
    assert all(
        isinstance(m, str) and m for m in missing
    ), "Each missing entry must be a non-empty string"

    if available:
        assert missing == [], "When available=True, missing list must be empty"
    else:
        assert len(missing) >= 1, "When available=False, missing list must have >= 1 entry"


# ===========================================================================
# Test 2: parse_with_ocr returns OCR_UNAVAILABLE when deps missing
# ===========================================================================

def test_parse_with_ocr_returns_ocr_unavailable_when_deps_missing(tmp_path: Path):
    """
    When OCR deps report unavailable, parse_with_ocr() must return a payload
    with error_code='OCR_UNAVAILABLE' and empty summary fields / line items.
    """
    from app.services.parser import parse_with_ocr

    # Create a dummy file (content irrelevant — deps check happens before file read)
    dummy = tmp_path / "dummy.jpg"
    dummy.write_bytes(b"not a real image")

    with patch(
        "app.services.ocr.check_ocr_deps",
        return_value=(False, ["heb traineddata (run: brew install tesseract-lang)"]),
    ):
        payload = parse_with_ocr(dummy, "image/jpeg", answers=None)

    assert payload.error_code == "OCR_UNAVAILABLE", (
        f"Expected OCR_UNAVAILABLE, got {payload.error_code}"
    )
    assert payload.parse_source == "ocr_unavailable", (
        f"Expected ocr_unavailable, got {payload.parse_source}"
    )
    assert payload.summary.gross is None, "gross should be None when OCR unavailable"
    assert payload.summary.net is None, "net should be None when OCR unavailable"
    assert len(payload.line_items) == 0, "line_items should be empty when OCR unavailable"
    assert len(payload.anomalies) == 0, "anomalies should be empty when OCR unavailable"
    assert payload.answers_applied is False


# ===========================================================================
# Test 3: hardened OCR patterns extract net and pay_month from real OCR noise
# ===========================================================================

# Synthetic OCR text that mirrors the real OCR debug output:
#   - "?נואר 2026" (OCR garbled ינואר first letter)
#   - "5,370.20 ... שכר" (net value on same line as שכר, with נטו missing)
#   - "מס הכנסו" (income_tax with garbled final letter)
#   - Normal numbers for national insurance and health
_REAL_OCR_SAMPLE = """\
תלוש שכר לחודש ?נואר 2026
חברה לדוגמה בע"מ
ח"פ 123456789

שכר בסיס  15,000.00  ברוטו
ניכויים:
מס הכנסו 2,500.00
ביטוח לאומי 900.00
מס בריאות 250.00

5,370.20 שכר
סה"כ לתשלום
"""


def test_ocr_hardened_patterns_extract_net_and_month():
    """
    Verifies that the hardened OCR patterns can extract net pay and pay_month
    from text that resembles real Tesseract output with typical OCR noise.

    Specifically tests:
      - pay_month = "2026-01" extracted from "?נואר 2026" (garbled ינואר)
      - net >= 5370 extracted from a line "5,370.20 שכר" (net label missing)
      - income_tax extracted from "מס הכנסו" (garbled final letter)
    """
    from app.services.parser import (
        FIELD_PATTERNS_OCR,
        extract_field,
        extract_pay_month_ocr,
        has_text_layer,
    )

    pages_text = {0: _REAL_OCR_SAMPLE}

    # Sanity: text is long enough for has_text_layer
    assert has_text_layer(pages_text), "Sample text should pass has_text_layer check"

    # --- pay_month ---
    month_result = extract_pay_month_ocr(pages_text)
    assert month_result is not None, "extract_pay_month_ocr should find a month"
    pay_month, month_conf = month_result
    assert pay_month == "2026-01", f"Expected 2026-01, got {pay_month}"
    assert month_conf > 0, "Month confidence should be positive"

    # --- net pay (heuristic from שכר line) ---
    net_field = extract_field(pages_text, "net_pay", FIELD_PATTERNS_OCR["net_pay"])
    assert net_field is not None, "Hardened net_pay patterns should find a value"
    assert abs(net_field.value - 5370.20) < 1.0, (
        f"Expected net ≈ 5370.20, got {net_field.value}"
    )

    # --- income_tax (garbled last letter) ---
    tax_field = extract_field(pages_text, "income_tax", FIELD_PATTERNS_OCR["income_tax"])
    assert tax_field is not None, "Hardened income_tax patterns should match 'מס הכנסו'"
    assert abs(tax_field.value - 2500.0) < 1.0, (
        f"Expected income_tax ≈ 2500, got {tax_field.value}"
    )
