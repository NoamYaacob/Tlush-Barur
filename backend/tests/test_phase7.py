"""
Phase 7 tests: UX Polish + Auto-Mapping Fallback + Math Verification Rules F/G/H.

All tests are pure-computation — no real files, no DB, no HTTP.

Test inventory (10 tests):
  Gross/net fallback (via _apply_gross_net_fallback):
  1.  gross=None, gross_taxable available → gross resolved, 15% confidence penalty
  2.  gross=None, gross_taxable=None, gross_ni available → second-priority fallback
  3.  gross already extracted → fallback NOT applied, original confidence preserved
  4.  net=None, net_to_pay available → net resolved, 15% confidence penalty
  5.  net=None, net_to_pay=None, net_salary available → second-priority net fallback

  Rule engine new rules F / G / H (via _run_extended_checks):
  6.  Rule F: employer pension < 6.5% → Warning 'ano_employer_pension_rate_unusual'
  7.  Rule F: employer pension = 7.5% (normal) → no anomaly
  8.  Rule G: NI rate < 2% → Warning 'ano_national_insurance_rate_unusual'
  9.  Rule H: health rate > 6.5% → Warning 'ano_health_tax_rate_unusual'
  10. Rules F/G/H: gross=None → none of the rules fire
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call_fallback(
    *,
    gross: float | None = None,
    net: float | None = None,
    gross_field_confidence: float | None = None,
    net_field_confidence: float | None = None,
    gross_taxable_value: float | None = None,
    gross_taxable_confidence: float | None = None,
    gross_ni_value: float | None = None,
    gross_ni_confidence: float | None = None,
    net_to_pay_value: float | None = None,
    net_to_pay_confidence: float | None = None,
    net_salary_value: float | None = None,
    net_salary_confidence: float | None = None,
):
    """Thin wrapper around _apply_gross_net_fallback for cleaner test calls."""
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
    )


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
    """Thin wrapper around _run_extended_checks (same pattern as test_phase4.py)."""
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


def _make_employer_pension_item(value: float, gross: float):
    """Create a minimal LineItem representing an employer pension contribution."""
    from app.models.schemas import LineItem, LineItemCategory
    return LineItem(
        id="li_employer_pension",
        category=LineItemCategory.EMPLOYER_CONTRIBUTION,
        description_hebrew="פנסיה מעסיק",
        explanation_hebrew="הפרשת מעסיק לקרן פנסיה",
        value=value,
        raw_text=str(abs(value)),
        confidence=0.88,
        page_index=0,
        is_unknown=False,
        unknown_guesses=[],
        unknown_question=None,
    )


# ---------------------------------------------------------------------------
# Test 1: gross=None → fallback to gross_taxable with 15% confidence penalty
# ---------------------------------------------------------------------------

def test_gross_fallback_uses_gross_taxable_when_gross_none():
    """
    When gross=None and gross_taxable is available:
      - resolved_gross == gross_taxable_value
      - gross_confidence == round(gross_taxable_confidence × 0.85, 3)
      - gross_fallback_note == "ברוטו חושב מ-ברוטו למס הכנסה"
    """
    result = _call_fallback(
        gross=None,
        net=9_800.0,
        net_field_confidence=0.90,
        gross_taxable_value=12_000.0,
        gross_taxable_confidence=0.80,
    )
    resolved_gross, gross_conf, gross_note, resolved_net, net_conf, net_note = result

    assert resolved_gross == 12_000.0, "gross should be resolved from gross_taxable"
    assert gross_conf == round(0.80 * 0.85, 3), "confidence should have 15% penalty applied"
    assert gross_note == "ברוטו חושב מ-ברוטו למס הכנסה", "fallback note should be in Hebrew"
    assert resolved_net == 9_800.0, "net should be unchanged"
    assert net_note is None, "no net fallback note when net was already set"


# ---------------------------------------------------------------------------
# Test 2: gross=None, gross_taxable=None → fallback to gross_ni (2nd priority)
# ---------------------------------------------------------------------------

def test_gross_fallback_uses_gross_ni_when_gross_taxable_none():
    """
    When gross=None and gross_taxable=None but gross_ni is available:
      - resolved_gross == gross_ni_value
      - gross_fallback_note == "ברוטו חושב מ-ברוטו לביטוח לאומי"
    """
    result = _call_fallback(
        gross=None,
        net=9_800.0,
        net_field_confidence=0.90,
        gross_taxable_value=None,
        gross_taxable_confidence=None,
        gross_ni_value=11_500.0,
        gross_ni_confidence=0.75,
    )
    resolved_gross, gross_conf, gross_note, _, _, _ = result

    assert resolved_gross == 11_500.0, "gross should be resolved from gross_ni"
    assert gross_conf == round(0.75 * 0.85, 3), "confidence should have 15% penalty"
    assert gross_note == "ברוטו חושב מ-ברוטו לביטוח לאומי"


# ---------------------------------------------------------------------------
# Test 3: gross already extracted → fallback NOT applied
# ---------------------------------------------------------------------------

def test_gross_no_fallback_when_main_gross_extracted():
    """
    When gross is already populated (from main gross_pay pattern):
      - resolved_gross == original gross value
      - gross_confidence == gross_field_confidence (no penalty)
      - gross_fallback_note is None
    Even if gross_taxable is also present, it must not override main gross.
    """
    result = _call_fallback(
        gross=12_000.0,
        gross_field_confidence=0.92,
        net=9_800.0,
        net_field_confidence=0.88,
        gross_taxable_value=11_800.0,  # different value — must NOT be used
        gross_taxable_confidence=0.80,
    )
    resolved_gross, gross_conf, gross_note, resolved_net, net_conf, net_note = result

    assert resolved_gross == 12_000.0, "main gross must not be overridden by fallback"
    assert gross_conf == 0.92, "original confidence should be preserved"
    assert gross_note is None, "no fallback note when main gross is set"
    assert resolved_net == 9_800.0
    assert net_note is None


# ---------------------------------------------------------------------------
# Test 4: net=None → fallback to net_to_pay (1st priority)
# ---------------------------------------------------------------------------

def test_net_fallback_uses_net_to_pay():
    """
    When net=None and net_to_pay is available:
      - resolved_net == net_to_pay_value
      - net_confidence == round(net_to_pay_confidence × 0.85, 3)
      - net_fallback_note == "נטו חושב מ-נטו לתשלום"
    """
    result = _call_fallback(
        gross=12_000.0,
        gross_field_confidence=0.90,
        net=None,
        net_to_pay_value=9_600.0,
        net_to_pay_confidence=0.82,
        net_salary_value=9_800.0,     # present but lower priority
        net_salary_confidence=0.85,
    )
    _, _, _, resolved_net, net_conf, net_note = result

    assert resolved_net == 9_600.0, "net should be resolved from net_to_pay (first priority)"
    assert net_conf == round(0.82 * 0.85, 3), "net confidence should have 15% penalty"
    assert net_note == "נטו חושב מ-נטו לתשלום"


# ---------------------------------------------------------------------------
# Test 5: net=None, net_to_pay=None → fallback to net_salary (2nd priority)
# ---------------------------------------------------------------------------

def test_net_fallback_uses_net_salary():
    """
    When net=None and net_to_pay=None but net_salary is available:
      - resolved_net == net_salary_value
      - net_fallback_note == "נטו חושב מ-שכר נטו"
    """
    result = _call_fallback(
        gross=12_000.0,
        gross_field_confidence=0.90,
        net=None,
        net_to_pay_value=None,
        net_to_pay_confidence=None,
        net_salary_value=9_800.0,
        net_salary_confidence=0.85,
    )
    _, _, _, resolved_net, net_conf, net_note = result

    assert resolved_net == 9_800.0, "net should be resolved from net_salary (second priority)"
    assert net_conf == round(0.85 * 0.85, 3)
    assert net_note == "נטו חושב מ-שכר נטו"


# ---------------------------------------------------------------------------
# Test 6: Rule F — employer pension < 6.5% → Warning
# ---------------------------------------------------------------------------

def test_rule_f_employer_pension_low_rate_warns():
    """
    Employer pension = ₪400 on gross = ₪12_000 → rate = 3.3% < 6.5% → Warning.
    Anomaly ID: 'ano_employer_pension_rate_unusual'
    """
    pension_item = _make_employer_pension_item(value=400.0, gross=12_000.0)
    anomalies = _call_extended(
        gross=12_000.0,
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=600.0,
        health=400.0,
        credit_points=2.25,
        line_items=[pension_item],
    )
    ids = {a.id for a in anomalies}
    assert "ano_employer_pension_rate_unusual" in ids, \
        f"Expected Rule F anomaly; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_employer_pension_rate_unusual")
    assert ano.severity.value == "Warning"
    assert "פנסיה" in ano.what_we_found or "מעסיק" in ano.what_we_found
    assert ano.ask_payroll, "ask_payroll must not be empty"


# ---------------------------------------------------------------------------
# Test 7: Rule F — employer pension = 7.5% (normal range) → no anomaly
# ---------------------------------------------------------------------------

def test_rule_f_employer_pension_normal_rate_no_anomaly():
    """
    Employer pension = ₪900 on gross = ₪12_000 → rate = 7.5% ∈ [6.5%, 8.5%] → no anomaly.
    """
    pension_item = _make_employer_pension_item(value=900.0, gross=12_000.0)
    anomalies = _call_extended(
        gross=12_000.0,
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=600.0,
        health=400.0,
        credit_points=2.25,
        line_items=[pension_item],
    )
    ids = {a.id for a in anomalies}
    assert "ano_employer_pension_rate_unusual" not in ids, \
        f"Rule F should NOT fire for 7.5% employer pension; got {ids}"


# ---------------------------------------------------------------------------
# Test 8: Rule G — NI rate < 2% → Warning
# ---------------------------------------------------------------------------

def test_rule_g_ni_rate_out_of_range_warns():
    """
    NI = ₪100 on gross = ₪12_000 → rate = 0.83% < 2% → Warning.
    Anomaly ID: 'ano_national_insurance_rate_unusual'
    """
    anomalies = _call_extended(
        gross=12_000.0,
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=100.0,   # suspiciously low
        health=400.0,
        credit_points=2.25,
    )
    ids = {a.id for a in anomalies}
    assert "ano_national_insurance_rate_unusual" in ids, \
        f"Expected Rule G anomaly; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_national_insurance_rate_unusual")
    assert ano.severity.value == "Warning"
    assert "ביטוח לאומי" in ano.what_we_found
    assert ano.ask_payroll, "ask_payroll must not be empty"


# ---------------------------------------------------------------------------
# Test 9: Rule H — health rate > 6.5% → Warning
# ---------------------------------------------------------------------------

def test_rule_h_health_rate_out_of_range_warns():
    """
    health = ₪900 on gross = ₪12_000 → rate = 7.5% > 6.5% → Warning.
    Anomaly ID: 'ano_health_tax_rate_unusual'
    """
    anomalies = _call_extended(
        gross=12_000.0,
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=600.0,
        health=900.0,    # suspiciously high
        credit_points=2.25,
    )
    ids = {a.id for a in anomalies}
    assert "ano_health_tax_rate_unusual" in ids, \
        f"Expected Rule H anomaly; got {ids}"
    ano = next(a for a in anomalies if a.id == "ano_health_tax_rate_unusual")
    assert ano.severity.value == "Warning"
    assert "בריאות" in ano.what_we_found
    assert ano.ask_payroll, "ask_payroll must not be empty"


# ---------------------------------------------------------------------------
# Test 10: Rules F/G/H do NOT fire when gross is None
# ---------------------------------------------------------------------------

def test_rules_fgh_not_fired_without_gross():
    """
    When gross=None, Rules F, G, and H must remain silent (no gross → can't compute rates).
    Rule E ('ano_no_gross_found') should fire instead.
    """
    pension_item = _make_employer_pension_item(value=900.0, gross=12_000.0)
    anomalies = _call_extended(
        gross=None,          # key: no gross
        net=9_800.0,
        income_tax=1_200.0,
        national_ins=600.0,
        health=400.0,
        credit_points=2.25,
        line_items=[pension_item],
    )
    ids = {a.id for a in anomalies}
    assert "ano_employer_pension_rate_unusual" not in ids, \
        "Rule F must not fire without gross"
    assert "ano_national_insurance_rate_unusual" not in ids, \
        "Rule G must not fire without gross"
    assert "ano_health_tax_rate_unusual" not in ids, \
        "Rule H must not fire without gross"
    # Rule E should have fired
    assert "ano_no_gross_found" in ids, \
        "Rule E (no gross) should fire when gross=None"
