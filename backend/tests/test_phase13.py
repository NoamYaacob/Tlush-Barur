"""
test_phase13.py — Tests for Phase 13: Privacy & Parser Logic Fixes.

Test list:
  1.  test_gross_fallback_prefers_total_payments_other
  2.  test_gross_fallback_falls_back_to_gross_taxable_when_no_total_payments
  3.  test_gross_fallback_falls_back_to_gross_ni_last
  4.  test_gross_fallback_prefers_existing_gross_over_all_fallbacks
  5.  test_sanitize_removes_latin_artifacts
  6.  test_sanitize_removes_date_artifacts
  7.  test_sanitize_removes_numeric_codes
  8.  test_sanitize_preserves_hebrew_and_digits
  9.  test_sanitize_fallback_when_all_noise
  10. test_sanitize_short_latin_abbreviations_preserved
"""

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_fallback(
    gross=None,
    net=None,
    gross_field_confidence=None,
    net_field_confidence=None,
    gross_taxable_value=None,
    gross_taxable_confidence=None,
    gross_ni_value=None,
    gross_ni_confidence=None,
    net_to_pay_value=None,
    net_to_pay_confidence=None,
    net_salary_value=None,
    net_salary_confidence=None,
    total_payments_other_value=None,
    total_payments_other_confidence=None,
):
    from app.services.parser import _apply_gross_net_fallback
    return _apply_gross_net_fallback(
        gross=gross,
        net=net,
        gross_field_confidence=gross_field_confidence,
        net_field_confidence=net_field_confidence,
        gross_taxable_value=gross_taxable_value,
        gross_taxable_confidence=gross_taxable_confidence,
        gross_ni_value=gross_ni_value,
        gross_ni_confidence=gross_ni_confidence,
        net_to_pay_value=net_to_pay_value,
        net_to_pay_confidence=net_to_pay_confidence,
        net_salary_value=net_salary_value,
        net_salary_confidence=net_salary_confidence,
        total_payments_other_value=total_payments_other_value,
        total_payments_other_confidence=total_payments_other_confidence,
    )


# ---------------------------------------------------------------------------
# 1. total_payments_other wins over gross_taxable
# ---------------------------------------------------------------------------

def test_gross_fallback_prefers_total_payments_other():
    """
    When both total_payments_other and gross_taxable are available and gross is
    None, total_payments_other must be chosen (it's the actual employee pay,
    not the tax-inflated gross).
    """
    resolved_gross, gross_conf, gross_note, resolved_net, net_conf, net_note = _call_fallback(
        gross=None,
        total_payments_other_value=6223.70,
        total_payments_other_confidence=0.95,
        gross_taxable_value=6463.00,
        gross_taxable_confidence=0.90,
    )

    assert resolved_gross == 6223.70, (
        f"total_payments_other (6223.70) should win over gross_taxable (6463.00), "
        f"but got {resolved_gross}"
    )
    assert gross_note is not None and "תשלומים" in gross_note, (
        f"Fallback note should mention 'תשלומים', got: {gross_note!r}"
    )
    # Confidence is penalised 15% for being a fallback
    assert abs(gross_conf - round(0.95 * 0.85, 3)) < 1e-6, (
        f"Expected confidence {round(0.95 * 0.85, 3)}, got {gross_conf}"
    )


# ---------------------------------------------------------------------------
# 2. Falls back to gross_taxable when total_payments_other absent
# ---------------------------------------------------------------------------

def test_gross_fallback_falls_back_to_gross_taxable_when_no_total_payments():
    """
    When total_payments_other is None but gross_taxable is available, the
    fallback must select gross_taxable.
    """
    resolved_gross, _, gross_note, *_ = _call_fallback(
        gross=None,
        total_payments_other_value=None,
        total_payments_other_confidence=None,
        gross_taxable_value=6463.00,
        gross_taxable_confidence=0.90,
    )

    assert resolved_gross == 6463.00, (
        f"gross_taxable (6463.00) should be selected when total_payments_other absent, "
        f"but got {resolved_gross}"
    )
    assert gross_note is not None and "מס הכנסה" in gross_note, (
        f"Fallback note should mention 'מס הכנסה', got: {gross_note!r}"
    )


# ---------------------------------------------------------------------------
# 3. Falls back to gross_ni last
# ---------------------------------------------------------------------------

def test_gross_fallback_falls_back_to_gross_ni_last():
    """
    When both total_payments_other and gross_taxable are absent, gross_ni must
    be selected as the last-resort gross fallback.
    """
    resolved_gross, _, gross_note, *_ = _call_fallback(
        gross=None,
        total_payments_other_value=None,
        gross_taxable_value=None,
        gross_ni_value=6100.00,
        gross_ni_confidence=0.80,
    )

    assert resolved_gross == 6100.00, (
        f"gross_ni (6100.00) should be last resort, got {resolved_gross}"
    )
    assert gross_note is not None and "ביטוח לאומי" in gross_note, (
        f"Fallback note should mention 'ביטוח לאומי', got: {gross_note!r}"
    )


# ---------------------------------------------------------------------------
# 4. Existing gross is never overridden
# ---------------------------------------------------------------------------

def test_gross_fallback_prefers_existing_gross_over_all_fallbacks():
    """
    When gross is already set (not None), it must NEVER be replaced by any
    fallback — not by total_payments_other, gross_taxable, or gross_ni.
    """
    original_gross = 7000.00
    resolved_gross, gross_conf, gross_note, *_ = _call_fallback(
        gross=original_gross,
        gross_field_confidence=0.99,
        total_payments_other_value=6223.70,
        total_payments_other_confidence=0.95,
        gross_taxable_value=6463.00,
        gross_taxable_confidence=0.90,
    )

    assert resolved_gross == original_gross, (
        f"Existing gross ({original_gross}) must not be overridden, got {resolved_gross}"
    )
    assert gross_note is None, (
        f"No fallback note expected when gross was already set, got: {gross_note!r}"
    )
    assert gross_conf == 0.99, (
        f"Confidence should match field confidence (0.99), got {gross_conf}"
    )


# ---------------------------------------------------------------------------
# 5. _sanitize_ocr_description: removes Latin artifacts ≥3 chars
# ---------------------------------------------------------------------------

def test_sanitize_removes_latin_artifacts():
    """
    Latin words of 3+ characters (Tesseract artifacts from redacted Hebrew)
    must be stripped.  Examples: DANN, NAD, DNN.
    """
    from app.services.parser import _sanitize_ocr_description

    # Simulate OCR output from a redacted payslip row
    raw = "שכר בסיס DANN 4,500"
    result = _sanitize_ocr_description(raw)

    assert "DANN" not in result, (
        f"Latin artifact 'DANN' should be removed, got: {result!r}"
    )
    assert "שכר" in result, (
        f"Hebrew text 'שכר' must be preserved, got: {result!r}"
    )
    assert "4,500" in result or "4" in result, (
        f"Amount should be preserved, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 6. _sanitize_ocr_description: removes date artifacts
# ---------------------------------------------------------------------------

def test_sanitize_removes_date_artifacts():
    """
    Date strings in DD/MM/YYYY format (from table header cells misread by OCR)
    must be stripped.
    """
    from app.services.parser import _sanitize_ocr_description

    raw = "תוספת ותק 01/01/2024 500"
    result = _sanitize_ocr_description(raw)

    assert "01/01/2024" not in result, (
        f"Date artifact '01/01/2024' should be removed, got: {result!r}"
    )
    assert "תוספת" in result, (
        f"Hebrew word 'תוספת' must be preserved, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 7. _sanitize_ocr_description: removes standalone numeric codes
# ---------------------------------------------------------------------------

def test_sanitize_removes_numeric_codes():
    """
    Standalone sequences of 4+ digits (numeric codes / IDs that are not
    amounts) must be stripped.
    """
    from app.services.parser import _sanitize_ocr_description

    raw = "קוד רכיב 10045 שעות נוספות"
    result = _sanitize_ocr_description(raw)

    assert "10045" not in result, (
        f"Numeric code '10045' should be removed, got: {result!r}"
    )
    assert "שעות" in result, (
        f"Hebrew text 'שעות' must be preserved, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 8. _sanitize_ocr_description: preserves valid Hebrew + digits
# ---------------------------------------------------------------------------

def test_sanitize_preserves_hebrew_and_digits():
    """
    A clean Hebrew description with a normal amount must pass through
    sanitization unchanged (except for whitespace normalization).
    """
    from app.services.parser import _sanitize_ocr_description

    raw = "שכר בסיס 5,200.00"
    result = _sanitize_ocr_description(raw)

    assert "שכר בסיס" in result, (
        f"Hebrew text 'שכר בסיס' must be preserved, got: {result!r}"
    )
    # The comma-delimited amount uses commas so numeric-code regex should not touch it
    assert "5,200" in result or "5" in result, (
        f"Amount digits should be preserved, got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 9. _sanitize_ocr_description: falls back to original when all is noise
# ---------------------------------------------------------------------------

def test_sanitize_fallback_when_all_noise():
    """
    When sanitization would remove all meaningful content, the function must
    return the original string (stripped) rather than an empty string.
    """
    from app.services.parser import _sanitize_ocr_description

    # A string that is entirely Latin artifacts
    raw = "DANN NAD DNN MAN"
    result = _sanitize_ocr_description(raw)

    # Should return the original rather than empty
    assert result.strip(), (
        "Sanitization must never return an empty string; fall back to original"
    )
    # The original (stripped) is expected since all content was artifacts
    assert result.strip() == raw.strip(), (
        f"When all content is noise, return original. Got: {result!r}"
    )


# ---------------------------------------------------------------------------
# 10. _sanitize_ocr_description: short (1-2 char) Latin abbreviations preserved
# ---------------------------------------------------------------------------

def test_sanitize_short_latin_abbreviations_preserved():
    """
    Short Latin tokens (1-2 characters) that might be legitimate abbreviations
    must NOT be removed by the 3+-char Latin artifact filter.
    """
    from app.services.parser import _sanitize_ocr_description

    # "VIP" is exactly 3 chars — will be removed.  "OK" is 2 — should survive.
    raw = "שכר OK 5,000"
    result = _sanitize_ocr_description(raw)

    # "OK" (2 chars) is below the 3-char threshold and must be kept
    assert "OK" in result, (
        f"Short 2-char Latin token 'OK' should be preserved, got: {result!r}"
    )
    assert "שכר" in result, (
        f"Hebrew text must be preserved, got: {result!r}"
    )
