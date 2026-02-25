"""
test_phase11.py — Tests for Phase 11 + 11.1 + 12: The Gatekeeper, Vision Upgrade,
                  Universal Summary Dictionary, OCR blind-spot fixes, and
                  Spatial Table Reconstruction.

Test list:
  1.  test_is_valid_payslip_accepts_payslip
  2.  test_is_valid_payslip_rejects_non_payslip
  3.  test_is_valid_payslip_threshold
  4.  test_preprocess_image_upscales             (Phase 11)
  5.  test_field_patterns_net_pay_synonyms
  6.  test_field_patterns_gross_pay_synonyms
  7.  test_preprocess_image_returns_binary       (Phase 11.1 — output is binarised)
  8.  test_spatial_reconstruct_psm11             (Phase 12 — PSM 11 in spatial engine)
  9.  test_spatial_reconstruct_groups_same_row   (Phase 12 — row grouping logic)
  10. test_spatial_reconstruct_rtl_order         (Phase 12 — RTL word order within rows)
  11. test_spatial_reconstruct_filters_noise     (Phase 12 — low-confidence words dropped)
"""

import re
import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# 1. Gatekeeper — accepts a valid payslip text
# ---------------------------------------------------------------------------

def test_is_valid_payslip_accepts_payslip():
    """A text containing many payroll keywords must be accepted."""
    from app.services.parser import is_valid_payslip
    payslip_text = (
        "תלוש שכר לחודש ינואר\n"
        "ברוטו לצורך מס: 8,500.00\n"
        "נטו לתשלום: 6,200.00\n"
        "ניכויי חובה\n"
        "מס הכנסה 420.00\n"
        "ביטוח לאומי 310.00\n"
        "הפרשות מעסיק לפנסיה\n"
        "עובד: ישראל ישראלי\n"
    )
    assert is_valid_payslip(payslip_text) is True, (
        "A typical payslip text should be accepted by the gatekeeper"
    )


# ---------------------------------------------------------------------------
# 2. Gatekeeper — rejects a non-payslip document
# ---------------------------------------------------------------------------

def test_is_valid_payslip_rejects_non_payslip():
    """A generic Hebrew text without payroll keywords must be rejected."""
    from app.services.parser import is_valid_payslip
    bank_statement = (
        "חשבון עו\"ש - דצמבר 2024\n"
        "תנועות בחשבון:\n"
        "העברה בנקאית 1,500.00\n"
        "רכישה בחנות 230.00\n"
        "יתרה לסוף חודש: 12,400.00\n"
    )
    assert is_valid_payslip(bank_statement) is False, (
        "A bank statement with no payroll keywords should be rejected"
    )


# ---------------------------------------------------------------------------
# 3. Gatekeeper — threshold boundary
# ---------------------------------------------------------------------------

def test_is_valid_payslip_threshold():
    """Exactly 2 keyword hits → False; exactly 3 hits → True."""
    from app.services.parser import is_valid_payslip, _PAYSLIP_MIN_KEYWORD_HITS

    assert _PAYSLIP_MIN_KEYWORD_HITS == 3, (
        f"Expected threshold of 3, got {_PAYSLIP_MIN_KEYWORD_HITS}"
    )

    # Construct text that hits exactly 2 known patterns: ברוטו and נטו
    two_hits = "ברוטו 5000 נטו 4000 חשבון"
    assert is_valid_payslip(two_hits) is False, (
        "2 keyword hits should be below threshold (< 3)"
    )

    # 3 hits: ברוטו + נטו + שכר
    three_hits = "ברוטו 5000 נטו 4000 שכר"
    assert is_valid_payslip(three_hits) is True, (
        "3 keyword hits should meet the threshold"
    )


# ---------------------------------------------------------------------------
# 4. Vision Upgrade — _preprocess_image upscales 2×
# ---------------------------------------------------------------------------

def test_preprocess_image_upscales():
    """_preprocess_image() output must be exactly 2× the input dimensions."""
    from app.services.ocr import _preprocess_image

    original_w, original_h = 300, 400
    img = Image.new("RGB", (original_w, original_h), color=(200, 200, 200))
    result = _preprocess_image(img)

    assert result.size == (original_w * 2, original_h * 2), (
        f"Expected size ({original_w * 2}, {original_h * 2}), got {result.size}"
    )


# ---------------------------------------------------------------------------
# 5. Dictionary — new net_pay synonyms match
# ---------------------------------------------------------------------------

def test_field_patterns_net_pay_synonyms():
    """Phase 11 net_pay synonyms (שכר נטו / נטו בנק / סכום בבנק) must match."""
    from app.services.parser import FIELD_PATTERNS
    net_patterns = FIELD_PATTERNS["net_pay"]

    test_cases = [
        ("שכר נטו 5,200.00",       "שכר נטו"),
        ("נטו בנק: 4,800.50",      "נטו בנק"),
        ("סכום בבנק 6,100.00",     "סכום בבנק"),
        ("סכום בנק 3,950.00",      "סכום בנק"),
    ]

    for text, label in test_cases:
        matched = any(
            re.search(pat, text, re.UNICODE)
            for pat, _ in net_patterns
        )
        assert matched, (
            f"net_pay pattern should match '{label}' but none of the patterns did.\n"
            f"Tested text: {text!r}"
        )


# ---------------------------------------------------------------------------
# 6. Dictionary — new gross_pay synonyms match
# ---------------------------------------------------------------------------

def test_field_patterns_gross_pay_synonyms():
    """Phase 11 gross_pay synonyms (סה\"כ תשלומים / סך כל התשלומים / סך הכל שכר) must match."""
    from app.services.parser import FIELD_PATTERNS
    gross_patterns = FIELD_PATTERNS["gross_pay"]

    test_cases = [
        ('סה"כ תשלומים 8,500.00',      'סה"כ תשלומים'),
        ("סה\u05d4כ תשלומים 8,500.00", "סהכ תשלומים"),   # variant without quote
        ("סך כל התשלומים 9,200.00",    "סך כל התשלומים"),
        ("סך התשלומים 7,800.00",       "סך התשלומים"),
        ("סך הכל שכר 10,000.00",       "סך הכל שכר"),
    ]

    for text, label in test_cases:
        matched = any(
            re.search(pat, text, re.UNICODE)
            for pat, _ in gross_patterns
        )
        assert matched, (
            f"gross_pay pattern should match '{label}' but none of the patterns did.\n"
            f"Tested text: {text!r}"
        )


# ---------------------------------------------------------------------------
# 7. Phase 11.1 — _preprocess_image output is binarised (only 0 and 255 pixels)
# ---------------------------------------------------------------------------

def test_preprocess_image_returns_binary():
    """
    After preprocessing, every pixel must be either 0 (black) or 255 (white).
    This confirms the two-pass binarisation (adaptive + Otsu) is working.
    """
    from app.services.ocr import _preprocess_image
    import numpy as np

    # Create a 100×100 image with a gradient (non-binary input)
    img = Image.new("RGB", (100, 100), color=(128, 128, 128))
    result = _preprocess_image(img)

    arr = np.array(result)
    unique_values = set(arr.flatten().tolist())
    assert unique_values <= {0, 255}, (
        f"Output image should contain only 0 and 255, but found: {unique_values}"
    )


# ---------------------------------------------------------------------------
# 8. Phase 12 — _spatial_reconstruct_lines uses --psm 11
# ---------------------------------------------------------------------------

def test_spatial_reconstruct_psm11():
    """
    _spatial_reconstruct_lines() must use --psm 11 (sparse text) in its
    Tesseract config so that it survives heavily redacted payslips.
    """
    import inspect
    from app.services import ocr as ocr_module

    source = inspect.getsource(ocr_module._spatial_reconstruct_lines)
    assert "--psm 11" in source, (
        "_spatial_reconstruct_lines() must use --psm 11 (sparse text).\n"
        "Found source:\n" + source[:600]
    )


# ---------------------------------------------------------------------------
# 9. Phase 12 — row grouping: words within tolerance end up on the same line
# ---------------------------------------------------------------------------

def test_spatial_reconstruct_groups_same_row():
    """
    Words with top-coordinates within _ROW_GROUP_TOLERANCE_PX must be grouped
    onto the same output line; words further apart must be on different lines.
    """
    from unittest.mock import patch
    from pytesseract import Output
    from app.services.ocr import _spatial_reconstruct_lines, _ROW_GROUP_TOLERANCE_PX

    # Two words on the same row (top difference = 5px, well within tolerance)
    # One word on a different row (top difference >> tolerance)
    mock_data = {
        "text":  ["שכר",   "5,000",   "ניכויים"],
        "conf":  [90,       90,         90],
        "top":   [100,      103,        300],    # first two same row, third different
        "left":  [500,      50,         480],    # RTL: שכר at right, 5,000 at left
    }

    with patch("pytesseract.image_to_data", return_value=mock_data):
        result = _spatial_reconstruct_lines(None, lang="heb+eng")

    lines = [l for l in result.splitlines() if l.strip()]
    assert len(lines) == 2, (
        f"Expected 2 lines (one per row group), got {len(lines)}: {lines}"
    )
    # First line should contain both same-row words
    assert "שכר" in lines[0] and "5,000" in lines[0], (
        f"Words on the same row should be on the same line. Line 0: {lines[0]!r}"
    )
    # Second line should contain only the distant word
    assert "ניכויים" in lines[1], (
        f"Word on a different row should be on its own line. Line 1: {lines[1]!r}"
    )


# ---------------------------------------------------------------------------
# 10. Phase 12 — RTL order: within a row, words are sorted right-to-left
# ---------------------------------------------------------------------------

def test_spatial_reconstruct_rtl_order():
    """
    Within each row, words must be sorted by descending left-coordinate
    (right-to-left) so that Hebrew descriptions appear before amounts.

    Layout (LTR pixel coords, but Hebrew reads RTL):
      left=400  → "משכורת"  (description, right side of page)
      left=50   → "5000.00"  (amount, left side of page)

    Expected output line: "משכורת 5000.00"
    """
    from unittest.mock import patch
    from app.services.ocr import _spatial_reconstruct_lines

    mock_data = {
        "text":  ["5000.00",  "משכורת"],
        "conf":  [90,          90],
        "top":   [100,         102],
        "left":  [50,          400],   # amount on left, description on right
    }

    with patch("pytesseract.image_to_data", return_value=mock_data):
        result = _spatial_reconstruct_lines(None, lang="heb+eng")

    lines = [l for l in result.splitlines() if l.strip()]
    assert len(lines) == 1, f"Expected 1 line, got {len(lines)}: {lines}"
    # Description (higher left) should come first after RTL sort
    idx_desc   = lines[0].index("משכורת")
    idx_amount = lines[0].index("5000.00")
    assert idx_desc < idx_amount, (
        f"Description should appear before amount in RTL order.\n"
        f"Line: {lines[0]!r} — desc at {idx_desc}, amount at {idx_amount}"
    )


# ---------------------------------------------------------------------------
# 11. Phase 12 — noise filter: low-confidence words are dropped
# ---------------------------------------------------------------------------

def test_spatial_reconstruct_filters_noise():
    """
    Words with confidence below _MIN_WORD_CONFIDENCE must be excluded
    from the reconstructed output.
    """
    from unittest.mock import patch
    from app.services.ocr import _spatial_reconstruct_lines, _MIN_WORD_CONFIDENCE

    below = max(0, _MIN_WORD_CONFIDENCE - 1)
    mock_data = {
        "text":  ["שכר",  "###NOISE###"],
        "conf":  [90,      below],
        "top":   [100,     101],
        "left":  [400,     200],
    }

    with patch("pytesseract.image_to_data", return_value=mock_data):
        result = _spatial_reconstruct_lines(None, lang="heb+eng")

    assert "###NOISE###" not in result, (
        f"Low-confidence word should be filtered out, but found in: {result!r}"
    )
    assert "שכר" in result, (
        f"High-confidence word should be kept, not found in: {result!r}"
    )
