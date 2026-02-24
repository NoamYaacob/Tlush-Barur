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


# ===========================================================================
# Test 4: summary-box fields extracted from tlush1-like OCR text
#         AND income_tax false positive suppressed for table rows
# ===========================================================================

# Synthetic OCR text that mirrors the tlush1.jpg real OCR debug output.
# Key properties:
#   - "מס הכנסו 34" appears on a line with "שעות" → must NOT be extracted as income_tax
#   - All 9 summary-box fields present as in the actual payslip footer
_TLUSH1_OCR_SAMPLE = """\
תלוש שכר לחודש ?נואר 2026
חברה לדוגמה בע"מ

שעות עבודה  קוד  תיאור  שכר  מס הכנסו 34  שעות
ניכויים:
ביטוח לאומי 253.00
מס בריאות 81.00

סיכום תלוש:
ברוטו למס הכנסה 6,463.00
ברוטו לב.ל 6,463.70
סה"כ תשלומים אחרים 6,223.70
ניכויי חובה-מסים 334.00
ניכוי קופות גמל 519.50
ניכויים שונים 16.00
שכר נטו 5,370.20
נטלתשלום 5,354.20
נקודות זיכוי 2.25
"""


def test_tlush1_summary_boxes_extracted_and_income_tax_false_positive_rejected():
    """
    Verifies:
    1. All 9 OCR summary-box fields are extracted correctly from a tlush1-like OCR snapshot.
    2. income_tax is NOT extracted from a table row that also contains 'שעות'
       (the "מס הכנסו 34 שעות" line — false positive suppressed by extract_field_filtered).
    3. pay_month = "2026-01" from "?נואר 2026".
    """
    from app.services.parser import (
        FIELD_PATTERNS_OCR,
        _INCOME_TAX_REJECT,
        _TABLE_ROW_TOKENS,
        extract_field,
        extract_field_filtered,
        extract_pay_month_ocr,
        has_text_layer,
    )

    pages_text = {0: _TLUSH1_OCR_SAMPLE}

    assert has_text_layer(pages_text), "Sample text should pass has_text_layer check"

    # --- pay_month ---
    month_result = extract_pay_month_ocr(pages_text)
    assert month_result is not None, "Should find pay_month"
    pay_month, _conf = month_result
    assert pay_month == "2026-01", f"Expected 2026-01, got {pay_month}"

    # --- income_tax FALSE POSITIVE: plain extract_field picks up the table row value ---
    tax_naive = extract_field(pages_text, "income_tax", FIELD_PATTERNS_OCR["income_tax"])
    # The naive extractor finds 34 (table row) or 6463 ("ברוטו למס הכנסה" line);
    # the filtered one must NOT (_INCOME_TAX_REJECT blocks both sources).
    tax_filtered = extract_field_filtered(
        pages_text, "income_tax", FIELD_PATTERNS_OCR["income_tax"],
        reject_tokens=_INCOME_TAX_REJECT,
    )
    # Filtered result must be None (no clean income_tax line exists in this sample)
    assert tax_filtered is None, (
        f"Filtered income_tax should be None (no clean line), got {tax_filtered}"
    )
    # Confirm the naive extractor does find something (false positive exists in the sample)
    assert tax_naive is not None, (
        "Naive extractor should have found a false positive in the sample"
    )

    # --- ברוטו למס הכנסה ---
    gross_taxable = extract_field(pages_text, "gross_taxable", FIELD_PATTERNS_OCR.get("gross_taxable", []))
    assert gross_taxable is not None, "gross_taxable should be found"
    assert abs(gross_taxable.value - 6463.00) < 1.0, f"Expected 6463.00, got {gross_taxable.value}"

    # --- ברוטו לב.ל ---
    gross_ni = extract_field(pages_text, "gross_ni", FIELD_PATTERNS_OCR.get("gross_ni", []))
    assert gross_ni is not None, "gross_ni should be found"
    assert abs(gross_ni.value - 6463.70) < 1.0, f"Expected 6463.70, got {gross_ni.value}"

    # --- סה"כ תשלומים אחרים ---
    total_payments_other = extract_field(pages_text, "total_payments_other", FIELD_PATTERNS_OCR.get("total_payments_other", []))
    assert total_payments_other is not None, "total_payments_other should be found"
    assert abs(total_payments_other.value - 6223.70) < 1.0, f"Expected 6223.70, got {total_payments_other.value}"

    # --- ניכויי חובה-מסים ---
    mandatory_taxes = extract_field(pages_text, "mandatory_taxes_total", FIELD_PATTERNS_OCR.get("mandatory_taxes_total", []))
    assert mandatory_taxes is not None, "mandatory_taxes_total should be found"
    assert abs(mandatory_taxes.value - 334.00) < 1.0, f"Expected 334.00, got {mandatory_taxes.value}"

    # --- ניכוי קופות גמל ---
    provident = extract_field(pages_text, "provident_funds_deduction", FIELD_PATTERNS_OCR.get("provident_funds_deduction", []))
    assert provident is not None, "provident_funds_deduction should be found"
    assert abs(provident.value - 519.50) < 1.0, f"Expected 519.50, got {provident.value}"

    # --- ניכויים שונים ---
    other_ded = extract_field(pages_text, "other_deductions", FIELD_PATTERNS_OCR.get("other_deductions", []))
    assert other_ded is not None, "other_deductions should be found"
    assert abs(other_ded.value - 16.00) < 1.0, f"Expected 16.00, got {other_ded.value}"

    # --- שכר נטו ---
    net_salary = extract_field(pages_text, "net_salary", FIELD_PATTERNS_OCR.get("net_salary", []))
    assert net_salary is not None, "net_salary should be found"
    assert abs(net_salary.value - 5370.20) < 1.0, f"Expected 5370.20, got {net_salary.value}"

    # --- נטלתשלום ---
    net_to_pay = extract_field(pages_text, "net_to_pay", FIELD_PATTERNS_OCR.get("net_to_pay", []))
    assert net_to_pay is not None, "net_to_pay should be found"
    assert abs(net_to_pay.value - 5354.20) < 1.0, f"Expected 5354.20, got {net_to_pay.value}"

    # --- נקודות זיכוי ---
    credits = extract_field(pages_text, "tax_credits", FIELD_PATTERNS_OCR.get("tax_credits", []))
    assert credits is not None, "tax_credits should be found"
    assert abs(credits.value - 2.25) < 0.1, f"Expected 2.25, got {credits.value}"


# ===========================================================================
# Test 5: find_amount_near_label — label and amount on different lines
#         (mirrors the real tlush1.jpg Tesseract output structure)
# ===========================================================================

# Synthetic snippet where the three hard fields have label and amount split
# across adjacent lines, matching the Tesseract line-order observed for tlush1.jpg:
#   - ניכויי תחובה.- מסים  (label, line N) / (334.00) (2 lines later)
#   - ניכויים שונים        (label, line N) / 16.00    (1 line later)
#   - נטלתשלום             (label, line N) / 5,354.20 (1 line later)
_TLUSH1_SPLIT_LINES_SAMPLE = """\
תלוש שכר לחודש ?נואר 2026
חברה לדוגמה בע"מ

= 291250 VI TS). ניכויי תחובה.- מסים
|
_ wan סכומים מצטברים לשנת (334.00)

6 שונים סו BHD טהב
16.00

\\ פיוך בונך 13 נטלתשלום
5,354.20
"""


def test_find_amount_near_label_line_split():
    """
    Verifies that find_amount_near_label_pages() and extract_ocr_near_label()
    correctly bridge the gap when Tesseract splits a Hebrew label and its
    monetary amount across adjacent OCR lines.

    Specifically:
      - mandatory_taxes_total: label "ניכויי תחובה.- מסים" 2 lines before (334.00)
      - other_deductions: label "שונים" 1 line before 16.00
      - net_to_pay: label "נטלתשלום" 1 line before 5,354.20
    """
    from app.services.parser import (
        FIELD_PATTERNS_OCR,
        _LABEL_RE_MANDATORY_TAXES,
        _LABEL_RE_NET_TO_PAY,
        _LABEL_RE_OTHER_DEDUCTIONS,
        _NEAR_CONF_ADJACENT,
        _NEAR_CONF_NEAR,
        extract_field,
        extract_ocr_near_label,
        find_amount_near_label_pages,
    )

    pages_text = {0: _TLUSH1_SPLIT_LINES_SAMPLE}

    # --- mandatory_taxes_total: value is 2 lines after label ---
    mtt_direct = extract_field(pages_text, "mandatory_taxes_total",
                               FIELD_PATTERNS_OCR.get("mandatory_taxes_total", []))
    mtt = extract_ocr_near_label(pages_text, _LABEL_RE_MANDATORY_TAXES, mtt_direct)
    assert mtt is not None, "mandatory_taxes_total should be found via near-label search"
    assert abs(mtt.value - 334.00) < 1.0, f"Expected 334.00, got {mtt.value}"
    assert mtt.confidence <= _NEAR_CONF_NEAR + 0.01, (
        f"Confidence for delta-2 should be <= {_NEAR_CONF_NEAR}, got {mtt.confidence}"
    )

    # --- other_deductions: value is 1 line after label ---
    oded_direct = extract_field(pages_text, "other_deductions",
                                FIELD_PATTERNS_OCR.get("other_deductions", []))
    oded = extract_ocr_near_label(pages_text, _LABEL_RE_OTHER_DEDUCTIONS, oded_direct)
    assert oded is not None, "other_deductions should be found via near-label search"
    assert abs(oded.value - 16.00) < 1.0, f"Expected 16.00, got {oded.value}"
    assert oded.confidence <= _NEAR_CONF_ADJACENT + 0.01, (
        f"Confidence for delta-1 should be <= {_NEAR_CONF_ADJACENT}, got {oded.confidence}"
    )

    # --- net_to_pay: value is 1 line after label ---
    ntp_direct = extract_field(pages_text, "net_to_pay",
                               FIELD_PATTERNS_OCR.get("net_to_pay", []))
    ntp = extract_ocr_near_label(pages_text, _LABEL_RE_NET_TO_PAY, ntp_direct)
    assert ntp is not None, "net_to_pay should be found via near-label search"
    assert abs(ntp.value - 5354.20) < 1.0, f"Expected 5354.20, got {ntp.value}"
    assert ntp.confidence <= _NEAR_CONF_ADJACENT + 0.01, (
        f"Confidence for delta-1 should be <= {_NEAR_CONF_ADJACENT}, got {ntp.confidence}"
    )

    # Also verify that find_amount_near_label_pages() is directly accessible
    raw_mtt = find_amount_near_label_pages(pages_text, _LABEL_RE_MANDATORY_TAXES)
    assert raw_mtt is not None
    assert abs(raw_mtt.value - 334.00) < 1.0


# ===========================================================================
# Test 6: extract_net_to_pay_ocr — computed fallback (net_salary - other_deductions)
# ===========================================================================

def test_net_to_pay_computed_fallback():
    """
    When the proximity search for 'נטלתשלום' returns net_salary (same value),
    and other_deductions > 0, extract_net_to_pay_ocr() should compute
    net_to_pay = net_salary - other_deductions and add an integrity note.

    Scenario mirrors the real tlush1.jpg OCR:
      net_salary = 5370.20, other_deductions = 16.00 → net_to_pay = 5354.20
    """
    from app.services.parser import (
        ExtractedField, extract_net_to_pay_ocr,
        _NEAR_CONF_ADJACENT,
    )

    # Minimal pages_text with the net-to-pay label present but no adjacent amount
    # (the proximity scan finds nothing useful).
    pages_text = {0: "שכר נטו 5,370.20\nפיוך 13 נטלתשלום\n"}

    net_salary_f = ExtractedField(value=5370.20, raw_text="שכר נטו 5,370.20",
                                  confidence=0.64, source_page=0)
    other_ded_f  = ExtractedField(value=16.00, raw_text="ניכויים 16.00",
                                  confidence=_NEAR_CONF_ADJACENT, source_page=0)

    result, notes = extract_net_to_pay_ocr(pages_text, None, net_salary_f, other_ded_f)

    assert result is not None, "Should produce a computed net_to_pay"
    assert abs(result.value - 5354.20) < 0.01, (
        f"Expected 5354.20 (5370.20 - 16.00), got {result.value}"
    )
    assert result.confidence == 0.50, (
        f"Computed fallback confidence should be 0.50, got {result.confidence}"
    )
    assert notes == ["נטו לתשלום חושב כנטו פחות ניכויים שונים"], (
        f"Expected integrity note, got {notes}"
    )


def test_net_to_pay_no_other_deductions_uses_net_salary():
    """
    When other_deductions is None, extract_net_to_pay_ocr() should return
    net_salary unchanged (no computation, no extra notes).
    """
    from app.services.parser import ExtractedField, extract_net_to_pay_ocr

    pages_text = {0: "שכר נטו 5,370.20\nנטלתשלום\n"}

    net_salary_f = ExtractedField(value=5370.20, raw_text="שכר נטו 5,370.20",
                                  confidence=0.64, source_page=0)

    result, notes = extract_net_to_pay_ocr(pages_text, None, net_salary_f, None)

    assert result is not None, "Should return net_salary as fallback"
    assert abs(result.value - 5370.20) < 0.01, (
        f"Expected 5370.20 (same as net_salary), got {result.value}"
    )
    assert notes == [], f"No extra notes expected, got {notes}"


# ===========================================================================
# Test 7: extract_line_items_ocr — earnings table extraction
# ===========================================================================

# Synthetic OCR text mirroring the real tlush1.jpg OCR output structure.
# Contains the "פרוט התשלומים" anchor followed by earnings rows,
# then a stop anchor ("ניכויי חובה"), followed by employer-contribution rows.
# Earnings lines mirror the garbled-but-parseable style from Tesseract RTL OCR.
_TLUSH1_TABLE_SAMPLE = """\
תלוש שכר לחודש ?נואר 2026
חברה לדוגמה בע"מ

js 7 : פרוט התשלומים
משכורת שבת * 0025 76.50 67.755 592.90
| ed mn 462.20 17.25 63.75 125% שעות
= — שעות הפסקה+ 51.00 15.50 280.50
LN had Ahad 157.00 497.50 הבראה חודשי
נסיעות חודשי 380.00

ניכויי חובה - מסים 334.00
ביטוח לאומי 253.00
מס בריאות 81.00

קופות גמל מעסיק:
פיצויים מעסיק 519.50
"""


def test_extract_line_items_ocr_earnings():
    """
    Phase 2D.2: extract_line_items_ocr() must find at least 5 earnings items
    from a payslip OCR text containing the 'פרוט התשלומים' table.

    Uses a synthetic OCR snippet that mirrors the garbled-but-parseable
    style of the real tlush1.jpg Tesseract output.
    """
    from app.services.parser import extract_line_items_ocr
    from app.models.schemas import LineItemCategory

    pages_text = {0: _TLUSH1_TABLE_SAMPLE}
    items = extract_line_items_ocr(pages_text)

    # Must have at least 5 items total
    assert len(items) >= 5, (
        f"Expected >= 5 line items, got {len(items)}: {[li.description_hebrew for li in items]}"
    )

    earnings = [li for li in items if li.category == LineItemCategory.EARNING]
    assert len(earnings) >= 5, (
        f"Expected >= 5 earning items, got {len(earnings)}: {[li.description_hebrew for li in earnings]}"
    )

    # All earnings must have positive values
    for li in earnings:
        assert li.value is not None and li.value > 0, (
            f"Earning '{li.description_hebrew}' should have positive value, got {li.value}"
        )

    # Specific items we know should be extracted
    descs = {li.description_hebrew for li in earnings}
    assert "משכורת שבת" in descs, f"Expected 'משכורת שבת' in earnings, got {descs}"
    assert "שעות נוספות" in descs, f"Expected 'שעות נוספות' in earnings, got {descs}"
    assert "דמי הבראה" in descs, f"Expected 'דמי הבראה' in earnings, got {descs}"

    # All items must have confidence > 0
    for li in items:
        assert li.confidence > 0, f"Item {li.id} has zero confidence"


def test_extract_line_items_ocr_deductions():
    """
    Phase 2D.2: extract_line_items_ocr() must correctly classify deductions
    found in the payslip OCR text.

    The deductions section starts after the stop anchor 'ניכויי חובה'.
    However, extract_line_items_ocr() only parses lines INSIDE the table region.
    The deduction rows (national_ins, health) appear AFTER the stop anchor,
    so they are not extracted by extract_line_items_ocr() from the table.

    This test verifies the FULL pipeline behavior: parse_with_ocr supplements
    the table items with summary-box deduction rows.

    For unit testing extract_line_items_ocr() alone, we embed deduction rows
    INSIDE the table region (before any stop anchor).
    """
    from app.services.parser import extract_line_items_ocr
    from app.models.schemas import LineItemCategory

    # Embed deduction rows inside the table region (no stop anchor)
    sample = """\
פרוט התשלומים
משכורת בסיס 5,000.00
נסיעות 380.00
מס הכנסה 334.00
ביטוח לאומי 253.00
מס בריאות 81.00
קופות גמל 519.50
"""
    pages_text = {0: sample}
    items = extract_line_items_ocr(pages_text)

    deductions = [li for li in items if li.category == LineItemCategory.DEDUCTION]
    assert len(deductions) >= 2, (
        f"Expected >= 2 deduction items, got {len(deductions)}: "
        f"{[li.description_hebrew for li in deductions]}"
    )

    # Deduction values should be negative
    for li in deductions:
        assert li.value is not None and li.value < 0, (
            f"Deduction '{li.description_hebrew}' should have negative value, got {li.value}"
        )

    ded_descs = {li.description_hebrew for li in deductions}
    assert "ביטוח לאומי (עובד)" in ded_descs or "מס הכנסה" in ded_descs, (
        f"Expected at least one of ביטוח לאומי / מס הכנסה in deductions, got {ded_descs}"
    )


def test_extract_line_items_ocr_no_anchor_fallback():
    """
    When the 'פרוט התשלומים' anchor is not found, extract_line_items_ocr()
    should fall back to scanning the full page for any recognizable items.
    """
    from app.services.parser import extract_line_items_ocr

    # Page with no anchor but recognizable item
    sample = """\
תלוש שכר
משכורת בסיס 5,000.00
ביטוח לאומי 253.00
"""
    pages_text = {0: sample}
    items = extract_line_items_ocr(pages_text)

    # Should find at least 2 items even without anchor
    assert len(items) >= 2, (
        f"Expected >= 2 items from fallback scan, got {len(items)}"
    )


def test_extract_line_items_ocr_dedup():
    """
    Duplicate rows (same description + same amount) should be deduplicated.
    """
    from app.services.parser import extract_line_items_ocr

    # Two identical lines — should produce only one item
    sample = """\
פרוט התשלומים
משכורת בסיס 5,000.00
משכורת בסיס 5,000.00
"""
    pages_text = {0: sample}
    items = extract_line_items_ocr(pages_text)

    by_desc = [li for li in items if li.description_hebrew == "משכורת בסיס"]
    assert len(by_desc) == 1, (
        f"Expected 1 deduplicated 'משכורת בסיס' item, got {len(by_desc)}"
    )


def test_extract_line_items_ocr_unknown_item():
    """
    Lines that don't match any known keyword should be returned as unknown items.
    """
    from app.services.parser import extract_line_items_ocr

    sample = """\
פרוט התשלומים
רכיב מיוחד XYZ 250.00
"""
    pages_text = {0: sample}
    items = extract_line_items_ocr(pages_text)

    unknown = [li for li in items if li.is_unknown]
    assert len(unknown) >= 1, (
        f"Expected at least 1 unknown item, got {len(unknown)}"
    )
    assert unknown[0].unknown_question is not None, "Unknown item should have a question"
    assert len(unknown[0].unknown_guesses) >= 1, "Unknown item should have guesses"
