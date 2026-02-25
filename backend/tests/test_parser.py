"""
Unit tests for backend/app/services/parser.py

Uses fpdf2 + DejaVuSans.ttf to generate synthetic in-memory PDF fixtures
containing known Hebrew payslip text, then asserts that the parser extracts
the correct values with the expected confidence tiers.

Run from the backend/ directory:
    python -m pytest tests/test_parser.py -v
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the backend package is importable when running from the backend/ dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ---------------------------------------------------------------------------
# Font path — tests skip gracefully if the font is absent
# ---------------------------------------------------------------------------

FONT_PATH = Path(__file__).parent / "DejaVuSans.ttf"


# ---------------------------------------------------------------------------
# PDF fixture helper
# ---------------------------------------------------------------------------

def _make_pdf_with_text(text: str) -> Path:
    """
    Write a temporary PDF containing the given text using fpdf2 + DejaVuSans.
    Returns the path to the temp file (caller must delete after use).
    Skips the test if DejaVuSans.ttf is not present.
    """
    if not FONT_PATH.exists():
        pytest.skip(f"DejaVuSans.ttf not found at {FONT_PATH} — skip Hebrew PDF test")

    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", str(FONT_PATH))
    pdf.set_font("DejaVu", size=12)
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            pdf.cell(0, 10, stripped, ln=True)

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    pdf.output(tmp.name)
    tmp.close()
    return Path(tmp.name)


def _make_empty_pdf() -> Path:
    """Create a minimal PDF with no text content (simulates a scanned-image PDF)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    # No text added — pdfplumber will extract empty string

    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    pdf.output(tmp.name)
    tmp.close()
    return Path(tmp.name)


# ---------------------------------------------------------------------------
# Synthetic payslip text fixtures
# ---------------------------------------------------------------------------

FULL_PAYSLIP_TEXT = """
חברה לדוגמה בע"מ
חילן מערכת שכר
חודש שכר: 01/2025
ברוטו: 15,000
נטו לתשלום: 11,000
מס הכנסה: 2,500
ביטוח לאומי: 900
מס בריאות: 250
נקודות זיכוי: 2.75
"""

MISMATCH_PAYSLIP_TEXT = """
חברה לדוגמה
ברוטו: 15,000
נטו לתשלום: 9,000
מס הכנסה: 2,500
ביטוח לאומי: 900
מס בריאות: 250
"""
# gross=15000, known_deductions=3650, implied_net=11350, actual_net=9000
# delta=2350 >> 2% tolerance (300) → CRITICAL anomaly expected


# ===========================================================================
# Test 1: Full text-layer PDF — all fields extracted
# ===========================================================================

def test_full_text_layer_extraction():
    """
    Given a PDF with clear Hebrew payslip text, all core fields should be
    extracted with the correct numeric values and confidence >= 0.6.
    """
    from app.services.parser import (
        FIELD_PATTERNS,
        TEXT_MIN_CHARS,
        detect_provider,
        extract_field,
        extract_pay_month,
        extract_text_from_pdf,
        has_text_layer,
    )

    pdf_path = _make_pdf_with_text(FULL_PAYSLIP_TEXT)
    try:
        pages = extract_text_from_pdf(pdf_path)

        assert pages, "Should extract at least one page"
        assert has_text_layer(pages), f"Should detect text layer (got {sum(len(t.strip()) for t in pages.values())} chars, need {TEXT_MIN_CHARS})"

        full_text = "\n".join(pages.values())

        net = extract_field(pages, "net_pay", FIELD_PATTERNS["net_pay"])
        gross = extract_field(pages, "gross_pay", FIELD_PATTERNS["gross_pay"])
        income_tax = extract_field(pages, "income_tax", FIELD_PATTERNS["income_tax"])
        nat_ins = extract_field(pages, "national_insurance", FIELD_PATTERNS["national_insurance"])
        health = extract_field(pages, "health_tax", FIELD_PATTERNS["health_tax"])
        credits = extract_field(pages, "tax_credits", FIELD_PATTERNS["tax_credits"])
        pay_month = extract_pay_month(pages)
        provider, _conf = detect_provider(full_text)

        assert net is not None, "net_pay should be extracted"
        assert net.value == 11000.0, f"net expected 11000, got {net.value}"
        assert net.confidence >= 0.6

        assert gross is not None, "gross_pay should be extracted"
        assert gross.value == 15000.0, f"gross expected 15000, got {gross.value}"
        assert gross.confidence >= 0.6

        assert income_tax is not None, "income_tax should be extracted"
        assert income_tax.value == 2500.0

        assert nat_ins is not None, "national_insurance should be extracted"
        assert nat_ins.value == 900.0

        assert health is not None, "health_tax should be extracted"
        assert health.value == 250.0

        assert credits is not None, "tax_credits should be extracted"
        assert credits.value == 2.75

        assert pay_month is not None, "pay_month should be extracted"
        assert pay_month[0] == "2025-01", f"pay_month expected 2025-01, got {pay_month[0]}"

        assert provider == "חילן", f"Provider expected חילן, got {provider}"

    finally:
        pdf_path.unlink(missing_ok=True)


# ===========================================================================
# Test 2: Net/gross mismatch → CRITICAL anomaly
# ===========================================================================

def test_net_gross_mismatch_anomaly():
    """
    When net is significantly below (gross - known_deductions), the parser
    should set integrity_ok=False and produce at least one CRITICAL anomaly.
    """
    from app.models.schemas import AnomalySeverity
    from app.services.parser import parse_pdf

    pdf_path = _make_pdf_with_text(MISMATCH_PAYSLIP_TEXT)
    try:
        payload = parse_pdf(pdf_path, answers=None)

        assert payload.error_code is None, f"Unexpected error_code: {payload.error_code}"
        assert payload.parse_source == "pdf_text_layer"

        assert payload.summary.gross == 15000.0
        assert payload.summary.net == 9000.0
        assert not payload.summary.integrity_ok, "Integrity check should FAIL"
        assert len(payload.summary.integrity_notes) >= 1, "Should have at least one integrity note"

        critical = [a for a in payload.anomalies if a.severity == AnomalySeverity.CRITICAL]
        assert len(critical) >= 1, f"Expected at least 1 CRITICAL anomaly, got {payload.anomalies}"

    finally:
        pdf_path.unlink(missing_ok=True)


# ===========================================================================
# Test 3: Empty PDF (no text) → OCR_REQUIRED
# ===========================================================================

def test_empty_pdf_returns_ocr_required():
    """
    A PDF with no text content (simulating a scanned image PDF) should return
    a payload with error_code='OCR_REQUIRED' and no line items.
    """
    from app.services.parser import parse_pdf

    pdf_path = _make_empty_pdf()
    try:
        payload = parse_pdf(pdf_path, answers=None)

        assert payload.error_code == "OCR_REQUIRED", f"Expected OCR_REQUIRED, got {payload.error_code}"
        assert payload.parse_source == "ocr_required"
        assert payload.summary.gross is None
        assert payload.summary.net is None
        assert len(payload.line_items) == 0

    finally:
        pdf_path.unlink(missing_ok=True)


# ===========================================================================
# Test 4: has_text_layer threshold boundary
# ===========================================================================

def test_has_text_layer_threshold():
    """Verify TEXT_MIN_CHARS boundary and Hebrew Unicode validation for has_text_layer.

    Phase 8.1: has_text_layer() now requires both minimum char count AND real Hebrew
    Unicode code points. This prevents Hilan-encoded PDFs (which produce Latin garbage)
    from bypassing the OCR upgrade path.
    """
    from app.services.parser import TEXT_MIN_CHARS, has_text_layer

    # Just below threshold → False (א is Hebrew Unicode, but not enough chars)
    short = {0: "א" * (TEXT_MIN_CHARS - 1)}
    assert not has_text_layer(short), f"Expected False for {TEXT_MIN_CHARS - 1} chars"

    # Exactly at threshold with Hebrew → True
    exact = {0: "א" * TEXT_MIN_CHARS}
    assert has_text_layer(exact), f"Expected True for exactly {TEXT_MIN_CHARS} Hebrew chars"

    # Well above threshold (55+ chars of real payslip text with Hebrew Unicode)
    rich = {0: "ברוטו: 15,000 נטו לתשלום: 11,000 מס הכנסה: 2,500 ביטוח לאומי: 900"}
    assert has_text_layer(rich), f"Expected True for rich Hebrew text"

    # Whitespace only → False (stripped)
    whitespace = {0: "   \n\t  " * 20}
    assert not has_text_layer(whitespace), "Whitespace-only should be False"

    # Phase 8.1: Latin-only text (Hilan proprietary font encoding) → False,
    # even when total char count far exceeds TEXT_MIN_CHARS.
    # This simulates what pdfplumber extracts from a Hilan-encoded PDF.
    hilan_garbled = {0: "äæ øëù ùåìúá íéðåúðä æåëéø úåáåçø ïîâøáî òá úååö 511514101 ãéâàú " * 3}
    assert not has_text_layer(hilan_garbled), \
        "Latin-only garbled text (Hilan font encoding) must return False"


# ===========================================================================
# Test 5: Provider detection
# ===========================================================================

def test_detect_provider():
    """Known provider strings are correctly identified; unknown text returns None."""
    from app.services.parser import detect_provider

    provider, conf = detect_provider("מערכת חילן גרסה 10.5 — תלוש שכר חודשי")
    assert provider == "חילן", f"Expected חילן, got {provider}"
    assert conf == 1.0

    provider2, _ = detect_provider("תלוש שכר מערכת סינאל SYNEL 2025")
    assert provider2 == "סינאל", f"Expected סינאל, got {provider2}"

    provider3, _ = detect_provider("מלאם-תים פתרונות שכר")
    assert provider3 == "מלאם-תים", f"Expected מלאם-תים, got {provider3}"

    provider4, conf4 = detect_provider("טקסט כללי ללא שם ספק מוכר")
    assert provider4 is None
    assert conf4 == 0.0


# ===========================================================================
# Test 6: _parse_number handles various formats
# ===========================================================================

def test_parse_number():
    """_parse_number converts Israeli-formatted strings to floats correctly."""
    from app.services.parser import _parse_number

    assert _parse_number("12,500") == 12500.0
    assert _parse_number("1,250.50") == 1250.5
    assert _parse_number("750") == 750.0
    assert _parse_number("2.75") == 2.75
    assert _parse_number("  1,000  ") == 1000.0
    assert _parse_number("abc") is None
    assert _parse_number("") is None
    # "1,2,3" → remove commas → "123" → 123.0 (acceptable behavior: commas stripped naively)
    result = _parse_number("1,2,3")
    assert result == 123.0  # strips all commas, parses remainder


# ===========================================================================
# Test 7: parse_pdf full integration with real Hebrew PDF
# ===========================================================================

def test_parse_pdf_full_integration():
    """
    End-to-end: parse_pdf() on a full Hebrew text-layer PDF returns a
    ParsedSlipPayload with parse_source='pdf_text_layer' and populated summary.
    """
    from app.services.parser import parse_pdf

    pdf_path = _make_pdf_with_text(FULL_PAYSLIP_TEXT)
    try:
        payload = parse_pdf(pdf_path, answers=None)

        assert payload.parse_source == "pdf_text_layer"
        assert payload.error_code is None
        assert payload.summary.gross == 15000.0
        assert payload.summary.net == 11000.0
        assert payload.summary.income_tax == 2500.0
        assert payload.summary.national_insurance == 900.0
        assert payload.summary.health_insurance == 250.0
        # Integrity: net (11000) vs gross-deductions (15000-3650=11350), delta=350 > 300 (2%)
        # → integrity should fail
        assert not payload.summary.integrity_ok
        assert len(payload.line_items) >= 3  # gross + at least 2 deductions
        assert payload.tax_credits_detected is not None
        assert payload.tax_credits_detected.credit_points_detected == 2.75
        assert payload.slip_meta.provider_guess == "חילן"
        assert payload.slip_meta.pay_month == "2025-01"
        assert payload.answers_applied is False

    finally:
        pdf_path.unlink(missing_ok=True)


# ===========================================================================
# Test 8: Hilan font encoding — garbled text bypasses text layer (Phase 8.1)
# ===========================================================================

def test_hilan_encoding_bypasses_text_layer():
    """
    Garbled Latin text produced by pdfplumber on Hilan-encoded PDFs must fail
    has_text_layer() so that the OCR upgrade path is triggered instead.

    Phase 8.1: has_text_layer() now requires real Hebrew Unicode code points,
    not just any characters that pass the length threshold.
    """
    from app.services.parser import has_text_layer

    # Actual garbled text extracted from a real Hilan PDF via pdfplumber
    hilan_garbled = {
        0: "äæ øëù ùåìúá íéðåúðä æåëéø úåáåçø ïîâøáî òá úååö 511514101 ãéâàú 926190869",
        1: "ïåéæéçî êåñ äñðëä õîéá åèøá äëéøö êåñ äñðëä õîéá åèøá",
    }
    assert not has_text_layer(hilan_garbled), \
        "Hilan proprietary-font encoding must NOT be treated as a readable Hebrew text layer"


# ===========================================================================
# Test 9: Zero-field fallback → OCR_REQUIRED (Phase 8.1)
# ===========================================================================

def test_zero_field_fallback_triggers_ocr_required():
    """
    When parse_pdf() reads a text-layer PDF but cannot extract gross or net
    (e.g. because the text layer has some Hebrew chars but no matching field patterns),
    it must return error_code='OCR_REQUIRED' so the processor can retry with Tesseract.

    This test uses a PDF whose text contains valid Hebrew but no payslip field labels,
    simulating the Hilan case where has_text_layer() would otherwise pass.
    """
    from app.services.parser import parse_pdf

    # A PDF with sufficient Hebrew Unicode to pass has_text_layer() but no field values
    irrelevant_text = "שלום עולם זהו טקסט עברי ללא נתוני שכר כלל לא ברוטו לא נטו לא מסים"
    pdf_path = _make_pdf_with_text(irrelevant_text)
    try:
        payload = parse_pdf(pdf_path, answers=None)

        assert payload.error_code == "OCR_REQUIRED", (
            f"Zero-field text layer must fall through to OCR_REQUIRED; "
            f"got error_code={payload.error_code!r}, parse_source={payload.parse_source!r}"
        )
        assert payload.parse_source == "ocr_required"
        assert payload.summary.gross is None
        assert payload.summary.net is None
        assert len(payload.line_items) == 0

    finally:
        pdf_path.unlink(missing_ok=True)
