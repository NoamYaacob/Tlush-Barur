"""
Phase 4 tests: extended rule-engine checks (Warning / Info severity anomalies).

All tests exercise _run_extended_checks() and _build_anomalies_from_real_data()
with synthetic data — no real files required.

Test inventory (8 tests):
  1.  Missing ALL deductions → Warning anomalies emitted (Phase 6: split IDs)
  2.  Credit points 1.5 → Info anomaly emitted
  3.  Credit points 10.0 → Warning anomaly emitted
  4.  Normal credit points (2.25) → no credit-related anomaly
  5.  net_to_pay vs. net_salary gap > ₪50 → Info anomaly emitted
  6.  net_to_pay == net_salary (gap = 0) → no gap anomaly
  7.  No gross found → Info anomaly "לא זוהה שכר ברוטו"
  8.  All checks clean → 0 extended anomalies (only existing Critical path unchanged)

Phase 6 note: Rule A was upgraded. The old 'ano_missing_mandatory_deductions' ID
is replaced by two separate anomalies:
  - 'ano_missing_income_tax'     (Warning) when estimated_tax > ₪100 and tax not detected
  - 'ano_missing_social_deductions' (Warning) when national_ins AND health are both None
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_extended(
    gross=None,
    net=None,
    income_tax=None,
    national_ins=None,
    health=None,
    credit_points=None,
    net_salary=None,
    net_to_pay=None,
    line_items=None,
    answers=None,
):
    """Thin wrapper around _run_extended_checks for cleaner test calls."""
    from app.services.parser import _run_extended_checks
    return _run_extended_checks(
        gross=gross,
        net=net,
        income_tax=income_tax,
        national_ins=national_ins,
        health=health,
        credit_points=credit_points,
        net_salary=net_salary,
        net_to_pay=net_to_pay,
        line_items=line_items or [],
        answers=answers,
    )


def _call_builder(
    gross=None,
    net=None,
    integrity_ok=True,
    integrity_notes=None,
    **kwargs,
):
    """Thin wrapper around _build_anomalies_from_real_data."""
    from app.services.parser import _build_anomalies_from_real_data
    return _build_anomalies_from_real_data(
        gross=gross,
        net=net,
        integrity_ok=integrity_ok,
        integrity_notes=integrity_notes or [],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Test 1: Missing ALL mandatory deductions → Warning anomalies (Phase 6 split)
# ---------------------------------------------------------------------------

def test_missing_all_deductions_emits_warning():
    """
    When income_tax, national_ins, and health are all None with gross=12_000:
      - estimated_tax = 12_000 × 0.10 = ₪1_200 > ₪100
        → 'ano_missing_income_tax' Warning
      - national_ins AND health both None
        → 'ano_missing_social_deductions' Warning
    (Phase 6: old 'ano_missing_mandatory_deductions' replaced by two separate IDs)
    """
    anomalies = _call_extended(
        gross=12_000.0,
        net=10_000.0,
        income_tax=None,
        national_ins=None,
        health=None,
    )
    ids = {a.id for a in anomalies}
    # Both new anomaly IDs must be present
    assert "ano_missing_income_tax" in ids, \
        f"Expected 'ano_missing_income_tax' in anomaly ids, got: {ids}"
    assert "ano_missing_social_deductions" in ids, \
        f"Expected 'ano_missing_social_deductions' in anomaly ids, got: {ids}"
    # Verify severities are Warning
    income_tax_ano = next(a for a in anomalies if a.id == "ano_missing_income_tax")
    social_ano = next(a for a in anomalies if a.id == "ano_missing_social_deductions")
    assert income_tax_ano.severity.value == "Warning", \
        f"Expected severity Warning for income tax anomaly, got {income_tax_ano.severity}"
    assert social_ano.severity.value == "Warning", \
        f"Expected severity Warning for social deductions anomaly, got {social_ano.severity}"
    # Verify Hebrew content present
    assert income_tax_ano.ask_payroll, "ask_payroll must not be empty"
    assert social_ano.ask_payroll, "ask_payroll must not be empty"


# ---------------------------------------------------------------------------
# Test 2: Credit points 1.5 → Info anomaly
# ---------------------------------------------------------------------------

def test_low_credit_points_emits_info():
    """Credit points below 2.0 → Info anomaly 'ano_low_credit_points'."""
    anomalies = _call_extended(
        gross=12_000.0,
        net=10_000.0,
        income_tax=1_200.0,
        national_ins=500.0,
        health=300.0,
        credit_points=1.5,
    )
    ids = {a.id for a in anomalies}
    assert "ano_low_credit_points" in ids, \
        f"Expected 'ano_low_credit_points' in {ids}"
    ano = next(a for a in anomalies if a.id == "ano_low_credit_points")
    assert ano.severity.value == "Info", \
        f"Expected severity Info, got {ano.severity}"
    assert "1.50" in ano.what_we_found or "1.5" in ano.what_we_found, \
        "what_we_found should mention the credit point value"


# ---------------------------------------------------------------------------
# Test 3: Credit points 10.0 → Warning anomaly
# ---------------------------------------------------------------------------

def test_high_credit_points_emits_warning():
    """Credit points above 8.0 → Warning anomaly 'ano_high_credit_points'."""
    anomalies = _call_extended(
        gross=15_000.0,
        net=12_000.0,
        income_tax=1_500.0,
        national_ins=600.0,
        health=400.0,
        credit_points=10.0,
    )
    ids = {a.id for a in anomalies}
    assert "ano_high_credit_points" in ids, \
        f"Expected 'ano_high_credit_points' in {ids}"
    ano = next(a for a in anomalies if a.id == "ano_high_credit_points")
    assert ano.severity.value == "Warning", \
        f"Expected severity Warning, got {ano.severity}"


# ---------------------------------------------------------------------------
# Test 4: Normal credit points (2.25) → no credit anomaly
# ---------------------------------------------------------------------------

def test_normal_credit_points_no_anomaly():
    """Standard 2.25 credit points → neither low nor high anomaly emitted."""
    anomalies = _call_extended(
        gross=12_000.0,
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=600.0,
        health=400.0,
        credit_points=2.25,
    )
    ids = {a.id for a in anomalies}
    assert "ano_low_credit_points" not in ids, \
        "Should NOT emit low-credit-points anomaly for 2.25 points"
    assert "ano_high_credit_points" not in ids, \
        "Should NOT emit high-credit-points anomaly for 2.25 points"


# ---------------------------------------------------------------------------
# Test 5: net_to_pay vs. net_salary gap > ₪50 → Info
# ---------------------------------------------------------------------------

def test_net_to_pay_gap_emits_info():
    """When |net_to_pay - net_salary| > 50 → Info anomaly 'ano_net_to_pay_gap'."""
    anomalies = _call_extended(
        gross=12_000.0,
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=600.0,
        health=400.0,
        net_salary=9_800.0,
        net_to_pay=9_600.0,   # gap = 200 → > 50
    )
    ids = {a.id for a in anomalies}
    assert "ano_net_to_pay_gap" in ids, \
        f"Expected 'ano_net_to_pay_gap' in {ids}"
    ano = next(a for a in anomalies if a.id == "ano_net_to_pay_gap")
    assert ano.severity.value == "Info", \
        f"Expected severity Info, got {ano.severity}"
    # Both amounts should be mentioned
    assert "9,800" in ano.what_we_found or "9800" in ano.what_we_found
    assert "9,600" in ano.what_we_found or "9600" in ano.what_we_found


# ---------------------------------------------------------------------------
# Test 6: net_to_pay == net_salary → no gap anomaly
# ---------------------------------------------------------------------------

def test_net_to_pay_equal_no_gap_anomaly():
    """When net_to_pay == net_salary → no gap anomaly emitted."""
    anomalies = _call_extended(
        gross=12_000.0,
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=600.0,
        health=400.0,
        net_salary=9_800.0,
        net_to_pay=9_800.0,   # gap = 0
    )
    ids = {a.id for a in anomalies}
    assert "ano_net_to_pay_gap" not in ids, \
        "Should NOT emit gap anomaly when net_to_pay == net_salary"


# ---------------------------------------------------------------------------
# Test 7: No gross found → Info "לא זוהה שכר ברוטו"
# ---------------------------------------------------------------------------

def test_no_gross_emits_info():
    """When gross is None → Info anomaly 'ano_no_gross_found'."""
    anomalies = _call_extended(
        gross=None,
        net=9_000.0,
        income_tax=1_000.0,
        national_ins=500.0,
        health=300.0,
    )
    ids = {a.id for a in anomalies}
    assert "ano_no_gross_found" in ids, \
        f"Expected 'ano_no_gross_found' in {ids}"
    ano = next(a for a in anomalies if a.id == "ano_no_gross_found")
    assert ano.severity.value == "Info", \
        f"Expected severity Info, got {ano.severity}"
    assert "ברוטו" in ano.what_we_found, "what_we_found should mention 'ברוטו'"


# ---------------------------------------------------------------------------
# Test 8: All checks clean → 0 extended anomalies
# ---------------------------------------------------------------------------

def test_all_clean_no_extended_anomalies():
    """When all data is present and within normal ranges → no extended anomalies.

    Phase 8: NI and health values must match the 2025/2026 bracket formula closely.
    For gross=15_000 (above ₪7_522 threshold):
      expected NI     = 7_522×0.0104 + (15_000−7_522)×0.07  ≈  602
      expected health = 7_522×0.0323 + (15_000−7_522)×0.0517 ≈  630
    """
    anomalies = _call_extended(
        gross=15_000.0,
        net=11_500.0,
        income_tax=2_000.0,
        national_ins=602.0,    # matches 2025/2026 bracket calculation (≈601.69)
        health=630.0,          # matches 2025/2026 bracket calculation (≈629.57)
        credit_points=2.25,    # normal
        net_salary=11_500.0,
        net_to_pay=11_500.0,   # no gap
    )
    # None of the extended anomaly IDs should appear
    extended_ids = {
        # Phase 6: replaced 'ano_missing_mandatory_deductions' with split IDs
        "ano_missing_income_tax",
        "ano_below_tax_threshold",
        "ano_missing_social_deductions",
        "ano_low_credit_points",
        "ano_high_credit_points",
        "ano_net_to_pay_gap",
        "ano_pension_rate_unusual",
        "ano_no_gross_found",
    }
    actual_ids = {a.id for a in anomalies}
    unexpected = actual_ids & extended_ids
    assert not unexpected, \
        f"Unexpected extended anomalies emitted for clean data: {unexpected}"
    # Total anomaly count should be 0 (integrity also ok)
    assert len(anomalies) == 0, \
        f"Expected 0 anomalies for clean data, got {len(anomalies)}: {actual_ids}"
