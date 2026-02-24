"""
Phase 8 tests: Precise Israeli Labor Law Rule Engine.

All tests are pure-computation — no real files, no DB, no HTTP.

Test inventory (14 tests):
  Income tax multi-bracket:
  1.  gross=9_000, no credits, income_tax=None →
      estimated = 10%×7_010 + 14%×(9_000−7_010) = 701 + 278.6 = 979.6 > 20 → Warning
  2.  gross=9_000, income_tax=900 → returns None (tax present, no anomaly)

  NI exact bracket (Rule G, new ID):
  3.  gross_ni=6_000, ni=62.40 (= 6000×0.0104) → diff=0, pct=0 → no anomaly
  4.  gross_ni=6_000, ni=300 → diff≈238 > 20 AND pct≈383% > 5% → Warning bracket mismatch
  5.  gross_ni=12_000 (above 7_522), ni≈correct → no anomaly
  6.  gross_ni=12_000, health=900 → expected_health≈382 → diff≈518 > 20 AND pct≈136% → Warning

  Rule I: Employee pension minimum (6%):
  7.  "שכר לקצבה"=10_000, pension_deduction=-500 (5%) → Warning
  8.  "שכר לקצבה"=10_000, pension_deduction=-620 (6.2%) → no Warning
  9.  No "שכר לקצבה" item → skip silently (no Warning)

  Rule J: Section 14 / Severance detection:
  10. "פיצויים"=833 on base=10_000 → 8.33% → Info ano_section14_detected
  11. "פיצויים"=600 on base=10_000 → 6.00% → Info ano_standard_severance_detected
  12. No severance item + other employer_contribution items → Info ano_severance_not_detected

  Rule K: Convalescence pay rate:
  13. "דמי הבראה", quantity=3, value=999  → implied rate=333 < 418 → Warning
  14. "דמי הבראה", quantity=3, value=1254 → implied rate=418 ≥ 418 → no Warning
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_line_item(
    *,
    id: str,
    category_str: str,           # "earning" | "deduction" | "employer_contribution"
    description_hebrew: str,
    value: float,
    quantity: float | None = None,
    rate: float | None = None,
):
    """Build a minimal LineItem for use in rule-engine tests."""
    from app.models.schemas import LineItem, LineItemCategory

    cat_map = {
        "earning": LineItemCategory.EARNING,
        "deduction": LineItemCategory.DEDUCTION,
        "employer_contribution": LineItemCategory.EMPLOYER_CONTRIBUTION,
    }
    return LineItem(
        id=id,
        category=cat_map[category_str],
        description_hebrew=description_hebrew,
        explanation_hebrew="",
        value=value,
        raw_text=str(abs(value)),
        confidence=0.90,
        page_index=0,
        is_unknown=False,
        unknown_guesses=[],
        unknown_question=None,
        quantity=quantity,
        rate=rate,
    )


def _extended(
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
    gross_taxable=None,
    provident_funds_deduction=None,
    gross_ni=None,
):
    """Thin wrapper around _run_extended_checks for Phase 8 tests."""
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
        gross_taxable=gross_taxable,
        provident_funds_deduction=provident_funds_deduction,
        gross_ni=gross_ni,
    )


# ---------------------------------------------------------------------------
# Test 1: Income tax bracket 2 applies — multi-bracket formula fires Warning
# ---------------------------------------------------------------------------

def test_income_tax_bracket2_applies():
    """
    gross=9_000, no credits, income_tax=None.
    Bracket 1: 10% × 7_010 = 701.0
    Bracket 2: 14% × (9_000 − 7_010) = 14% × 1_990 = 278.6
    estimated = 979.6 > 20 → Warning 'ano_missing_income_tax'
    """
    from app.models.schemas import AnomalySeverity
    from app.services.parser import _check_income_tax_rule

    result = _check_income_tax_rule(
        gross=9_000.0,
        income_tax=None,
        credit_points=None,
        gross_taxable=None,
        provident_funds_deduction=None,
    )

    assert result is not None, "Should return a Warning anomaly"
    assert result.id == "ano_missing_income_tax"
    assert result.severity == AnomalySeverity.WARNING


# ---------------------------------------------------------------------------
# Test 2: Income tax present → no anomaly (even in bracket 2 range)
# ---------------------------------------------------------------------------

def test_income_tax_present_no_anomaly_bracket2():
    """
    gross=9_000, income_tax=900 → returns None (tax detected, no anomaly).
    """
    from app.services.parser import _check_income_tax_rule

    result = _check_income_tax_rule(
        gross=9_000.0,
        income_tax=900.0,
        credit_points=None,
    )
    assert result is None, "When income_tax is present, rule must return None"


# ---------------------------------------------------------------------------
# Test 3: NI below threshold, correct amount → no anomaly
# ---------------------------------------------------------------------------

def test_ni_below_threshold_correct_no_anomaly():
    """
    gross_ni=6_000 (< 7_522), expected NI = 6_000 × 1.04% = 62.40.
    Actual ni = 62.40 → diff = 0 → no anomaly.
    """
    anomalies = _extended(
        gross=6_000.0,
        gross_ni=6_000.0,
        national_ins=62.40,
        income_tax=500.0,    # suppress income-tax anomaly
        health=200.0,
    )
    ids = {a.id for a in anomalies}
    assert "ano_national_insurance_bracket_mismatch" not in ids, \
        f"No NI mismatch expected for correct amount; got {ids}"


# ---------------------------------------------------------------------------
# Test 4: NI below threshold, wrong amount → Warning
# ---------------------------------------------------------------------------

def test_ni_below_threshold_wrong_triggers_warning():
    """
    gross_ni=6_000 (< 7_522), expected NI ≈ 62.40.
    Actual ni = 300 → diff ≈ 238 > 20 AND pct ≈ 383% > 5% → Warning.
    """
    from app.models.schemas import AnomalySeverity

    anomalies = _extended(
        gross=6_000.0,
        gross_ni=6_000.0,
        national_ins=300.0,    # way off
        income_tax=500.0,
        health=200.0,
    )
    ids = {a.id for a in anomalies}
    assert "ano_national_insurance_bracket_mismatch" in ids, \
        f"Expected NI bracket mismatch Warning; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_national_insurance_bracket_mismatch")
    assert ano.severity == AnomalySeverity.WARNING
    assert "ביטוח לאומי" in ano.what_we_found


# ---------------------------------------------------------------------------
# Test 5: NI above threshold, correct amount → no anomaly
# ---------------------------------------------------------------------------

def test_ni_above_threshold_correct_no_anomaly():
    """
    gross_ni=12_000 (> 7_522).
    expected NI = 7_522×0.0104 + (12_000−7_522)×0.07
               = 78.23 + 313.46 = 391.69 ≈ 392
    Actual ni = 392 → diff ≈ 0.31, pct ≈ 0.08% → well below tolerance.
    """
    anomalies = _extended(
        gross=12_000.0,
        gross_ni=12_000.0,
        national_ins=392.0,
        income_tax=1_200.0,
        health=475.0,
    )
    ids = {a.id for a in anomalies}
    assert "ano_national_insurance_bracket_mismatch" not in ids, \
        f"No NI mismatch expected for approximately correct amount; got {ids}"


# ---------------------------------------------------------------------------
# Test 6: Health above threshold, wrong amount → Warning
# ---------------------------------------------------------------------------

def test_health_above_threshold_wrong_triggers_warning():
    """
    gross_ni=12_000 (> 7_522).
    expected health = 7_522×0.0323 + (12_000−7_522)×0.0517
                    ≈ 242.96 + 231.61 = 474.57 ≈ 475
    Actual health = 900 → diff ≈ 425 > 20 AND pct ≈ 89% > 5% → Warning.
    """
    from app.models.schemas import AnomalySeverity

    anomalies = _extended(
        gross=12_000.0,
        gross_ni=12_000.0,
        national_ins=392.0,
        income_tax=1_200.0,
        health=900.0,    # suspiciously high
    )
    ids = {a.id for a in anomalies}
    assert "ano_health_tax_bracket_mismatch" in ids, \
        f"Expected health bracket mismatch Warning; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_health_tax_bracket_mismatch")
    assert ano.severity == AnomalySeverity.WARNING
    assert "בריאות" in ano.what_we_found


# ---------------------------------------------------------------------------
# Test 7: Rule I — pension < 6% of pension base → Warning
# ---------------------------------------------------------------------------

def test_rule_i_pension_below_minimum_warns():
    """
    "שכר לקצבה" base = 10_000, employee pension deduction = -500 (5%) → Warning.
    Rule I: 5% < 6% minimum → ano_pension_employee_below_minimum.
    """
    from app.models.schemas import AnomalySeverity

    pension_base = _make_line_item(
        id="li_base",
        category_str="earning",
        description_hebrew="שכר לקצבה",
        value=10_000.0,
    )
    pension_deduction = _make_line_item(
        id="li_pension",
        category_str="deduction",
        description_hebrew="פנסיה עובד",
        value=-500.0,   # 5% of 10_000
    )
    anomalies = _extended(
        gross=12_000.0,
        income_tax=1_200.0,
        line_items=[pension_base, pension_deduction],
    )
    ids = {a.id for a in anomalies}
    assert "ano_pension_employee_below_minimum" in ids, \
        f"Expected Rule I warning for 5% pension; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_pension_employee_below_minimum")
    assert ano.severity == AnomalySeverity.WARNING


# ---------------------------------------------------------------------------
# Test 8: Rule I — pension ≥ 6% → no Warning
# ---------------------------------------------------------------------------

def test_rule_i_pension_above_minimum_no_warning():
    """
    "שכר לקצבה" base = 10_000, employee pension deduction = -620 (6.2%) → no Warning.
    """
    pension_base = _make_line_item(
        id="li_base",
        category_str="earning",
        description_hebrew="שכר לקצבה",
        value=10_000.0,
    )
    pension_deduction = _make_line_item(
        id="li_pension",
        category_str="deduction",
        description_hebrew="פנסיה עובד",
        value=-620.0,   # 6.2% of 10_000
    )
    anomalies = _extended(
        gross=12_000.0,
        income_tax=1_200.0,
        line_items=[pension_base, pension_deduction],
    )
    ids = {a.id for a in anomalies}
    assert "ano_pension_employee_below_minimum" not in ids, \
        f"Rule I must NOT fire for 6.2% pension; got {ids}"


# ---------------------------------------------------------------------------
# Test 9: Rule I — no pension base item → skip silently
# ---------------------------------------------------------------------------

def test_rule_i_no_pension_base_item_silent():
    """
    Only a deduction item exists; no "שכר לקצבה" / "שכר בסיס" earning line item.
    Rule I should skip silently — no false positive.
    """
    pension_deduction = _make_line_item(
        id="li_pension",
        category_str="deduction",
        description_hebrew="פנסיה עובד",
        value=-400.0,
    )
    anomalies = _extended(
        gross=12_000.0,
        income_tax=1_200.0,
        line_items=[pension_deduction],
    )
    ids = {a.id for a in anomalies}
    assert "ano_pension_employee_below_minimum" not in ids, \
        "Rule I must not fire when no pension base item is found"


# ---------------------------------------------------------------------------
# Test 10: Rule J — Section 14 @ 8.33% → Info ano_section14_detected
# ---------------------------------------------------------------------------

def test_rule_j_section14_detected():
    """
    "שכר לקצבה" base = 10_000, "פיצויים" employer = 833 → 8.33% → Info (Section 14).
    """
    from app.models.schemas import AnomalySeverity

    pension_base = _make_line_item(
        id="li_base",
        category_str="earning",
        description_hebrew="שכר לקצבה",
        value=10_000.0,
    )
    severance = _make_line_item(
        id="li_sev",
        category_str="employer_contribution",
        description_hebrew="פיצויים",
        value=833.0,    # 8.33% of 10_000
    )
    anomalies = _extended(
        gross=12_000.0,
        income_tax=1_200.0,
        line_items=[pension_base, severance],
    )
    ids = {a.id for a in anomalies}
    assert "ano_section14_detected" in ids, \
        f"Expected Section 14 Info anomaly; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_section14_detected")
    assert ano.severity == AnomalySeverity.INFO


# ---------------------------------------------------------------------------
# Test 11: Rule J — Standard severance 6% → Info ano_standard_severance_detected
# ---------------------------------------------------------------------------

def test_rule_j_standard_severance_detected():
    """
    "שכר לקצבה" base = 10_000, "פיצויים" employer = 600 → 6.0% → Info (standard).
    """
    from app.models.schemas import AnomalySeverity

    pension_base = _make_line_item(
        id="li_base",
        category_str="earning",
        description_hebrew="שכר לקצבה",
        value=10_000.0,
    )
    severance = _make_line_item(
        id="li_sev",
        category_str="employer_contribution",
        description_hebrew="פיצויים",
        value=600.0,    # 6.0% of 10_000
    )
    anomalies = _extended(
        gross=12_000.0,
        income_tax=1_200.0,
        line_items=[pension_base, severance],
    )
    ids = {a.id for a in anomalies}
    assert "ano_standard_severance_detected" in ids, \
        f"Expected standard severance Info anomaly; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_standard_severance_detected")
    assert ano.severity == AnomalySeverity.INFO


# ---------------------------------------------------------------------------
# Test 12: Rule J — No severance item + other employer_contribution → Info
# ---------------------------------------------------------------------------

def test_rule_j_no_severance_emits_info_when_employer_items_present():
    """
    No "פיצויים" employer item, but another employer_contribution item exists.
    → Info 'ano_severance_not_detected'
    (Guards against silently missing severance when OCR successfully parsed the section.)
    """
    from app.models.schemas import AnomalySeverity

    pension_employer = _make_line_item(
        id="li_emp_pension",
        category_str="employer_contribution",
        description_hebrew="פנסיה מעסיק",
        value=750.0,
    )
    anomalies = _extended(
        gross=12_000.0,
        income_tax=1_200.0,
        line_items=[pension_employer],
    )
    ids = {a.id for a in anomalies}
    assert "ano_severance_not_detected" in ids, \
        f"Expected ano_severance_not_detected when employer items exist but severance absent; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_severance_not_detected")
    assert ano.severity == AnomalySeverity.INFO


# ---------------------------------------------------------------------------
# Test 13: Rule K — Convalescence qty=3, value=999 → implied rate=333 < 418 → Warning
# ---------------------------------------------------------------------------

def test_rule_k_convalescence_rate_low_warns():
    """
    "דמי הבראה", quantity=3, value=999.
    implied_rate = 999 / 3 = 333 < 418 → Warning 'ano_convalescence_rate_low'.
    """
    from app.models.schemas import AnomalySeverity

    conv_item = _make_line_item(
        id="li_conv",
        category_str="earning",
        description_hebrew="דמי הבראה",
        value=999.0,
        quantity=3.0,
    )
    anomalies = _extended(
        gross=12_000.0,
        income_tax=1_200.0,
        line_items=[conv_item],
    )
    ids = {a.id for a in anomalies}
    assert "ano_convalescence_rate_low" in ids, \
        f"Expected Rule K warning for rate=333 < 418; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_convalescence_rate_low")
    assert ano.severity == AnomalySeverity.WARNING
    assert "418" in ano.what_we_found or "הבראה" in ano.what_we_found


# ---------------------------------------------------------------------------
# Test 14: Rule K — Convalescence qty=3, value=1254 → implied rate=418 → no Warning
# ---------------------------------------------------------------------------

def test_rule_k_convalescence_rate_at_minimum_no_warning():
    """
    "דמי הבראה", quantity=3, value=1254.
    implied_rate = 1254 / 3 = 418.0 = legal minimum → no warning.
    """
    conv_item = _make_line_item(
        id="li_conv",
        category_str="earning",
        description_hebrew="דמי הבראה",
        value=1_254.0,
        quantity=3.0,
    )
    anomalies = _extended(
        gross=12_000.0,
        income_tax=1_200.0,
        line_items=[conv_item],
    )
    ids = {a.id for a in anomalies}
    assert "ano_convalescence_rate_low" not in ids, \
        f"Rule K must NOT fire for rate=418 (exactly at minimum); got {ids}"
