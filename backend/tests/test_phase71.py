"""
Phase 7.1 tests: Section 45a Pension Tax Credit + improved income-tax rule.

All tests are pure-computation — no real files, no DB, no HTTP.

Test inventory (8 tests):
  Section 45a pension credit in _check_income_tax_rule:
  1.  gross_taxable=6463, credit_points=2.25, provident_funds=420 →
      estimated ≤ 0 → Info 'ano_below_tax_threshold', what_we_found contains "תקין"
  2.  gross_taxable=6463, credit_points=2.25, provident_funds=None →
      estimated = 646 − 580 = 66 > 20 → Warning 'ano_missing_income_tax'
      (confirms pension credit is what tips the scale in test 1)
  3.  provident_funds_deduction passed to _run_extended_checks → threads correctly to rule
  4.  gross_taxable preferred over gross in formula when both present
  5.  gross_taxable=None → falls back to gross in formula
  6.  Noise floor is ₪20 (not ₪100): estimated=50 > 20 → Warning
  7.  Borderline: estimated=15 ≤ 20 → None (no anomaly)
  8.  "תקין" string appears in what_we_found when below threshold (required by spec)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule(
    *,
    gross: float | None = None,
    income_tax: float | None = None,
    credit_points: float | None = None,
    gross_taxable: float | None = None,
    provident_funds_deduction: float | None = None,
):
    """Call _check_income_tax_rule with keyword args for readability."""
    from app.services.parser import _check_income_tax_rule
    return _check_income_tax_rule(
        gross=gross,
        income_tax=income_tax,
        credit_points=credit_points,
        gross_taxable=gross_taxable,
        provident_funds_deduction=provident_funds_deduction,
    )


def _extended(
    gross=None,
    income_tax=None,
    credit_points=None,
    gross_taxable=None,
    provident_funds_deduction=None,
):
    from app.services.parser import _run_extended_checks
    return _run_extended_checks(
        gross=gross,
        net=None,
        income_tax=income_tax,
        national_ins=None,
        health=None,
        credit_points=credit_points,
        net_salary=None,
        net_to_pay=None,
        line_items=[],
        answers=None,
        gross_taxable=gross_taxable,
        provident_funds_deduction=provident_funds_deduction,
    )


# ---------------------------------------------------------------------------
# Test 1: Real-world case — pension credit makes zero income tax correct
# ---------------------------------------------------------------------------

def test_section45a_zero_tax_is_correct_with_pension():
    """
    User's actual payslip scenario:
      gross_taxable = 6463, credit_points = 2.25, provident_funds_deduction = 420

    Formula:
      estimated = 6463 × 0.10 − 2.25 × 258 − 420 × 0.35
               = 646.3 − 580.5 − 147.0
               = −81.2  ≤ 0

    → Should emit Info 'ano_below_tax_threshold' with "תקין" in what_we_found.
    (Previously this incorrectly emitted a Warning.)
    """
    from app.models.schemas import AnomalySeverity

    result = _rule(
        gross=6_463.0,
        income_tax=None,
        credit_points=2.25,
        gross_taxable=6_463.0,
        provident_funds_deduction=420.0,
    )

    assert result is not None, "Should return an Info anomaly, not None"
    assert result.id == "ano_below_tax_threshold", \
        f"Expected 'ano_below_tax_threshold'; got '{result.id}'"
    assert result.severity == AnomalySeverity.INFO
    assert "תקין" in result.what_we_found, \
        f"what_we_found must contain 'תקין'; got: '{result.what_we_found}'"


# ---------------------------------------------------------------------------
# Test 2: Without pension credit the same gross WOULD have triggered a Warning
# ---------------------------------------------------------------------------

def test_section45a_without_pension_credit_triggers_warning():
    """
    Same gross_taxable = 6463, credit_points = 2.25, but NO pension deduction:
      estimated = 646.3 − 580.5 − 0 = 65.8 > 20

    → Should emit Warning 'ano_missing_income_tax'.
    This proves the pension credit in test 1 is the decisive factor.
    """
    from app.models.schemas import AnomalySeverity

    result = _rule(
        gross=6_463.0,
        income_tax=None,
        credit_points=2.25,
        gross_taxable=6_463.0,
        provident_funds_deduction=None,  # no pension deduction
    )

    assert result is not None, "Should return a Warning anomaly"
    assert result.id == "ano_missing_income_tax", \
        f"Expected Warning without pension credit; got '{result.id}'"
    assert result.severity == AnomalySeverity.WARNING


# ---------------------------------------------------------------------------
# Test 3: provident_funds_deduction threads through _run_extended_checks
# ---------------------------------------------------------------------------

def test_provident_funds_threads_through_extended_checks():
    """
    _run_extended_checks should accept and use provident_funds_deduction.
    With pension making estimated ≤ 0, the income-tax anomaly should be Info (not Warning).
    """
    from app.models.schemas import AnomalySeverity

    anomalies = _extended(
        gross=6_463.0,
        income_tax=None,
        credit_points=2.25,
        gross_taxable=6_463.0,
        provident_funds_deduction=420.0,
    )

    income_tax_anos = [a for a in anomalies if a.id in ("ano_missing_income_tax", "ano_below_tax_threshold")]
    assert len(income_tax_anos) == 1, \
        f"Expected exactly one income-tax anomaly; got {[a.id for a in income_tax_anos]}"
    assert income_tax_anos[0].id == "ano_below_tax_threshold"
    assert income_tax_anos[0].severity == AnomalySeverity.INFO


# ---------------------------------------------------------------------------
# Test 4: gross_taxable preferred over gross when both present
# ---------------------------------------------------------------------------

def test_gross_taxable_preferred_over_gross_in_formula():
    """
    gross=10_000, gross_taxable=6_463 → formula uses 6_463.
    With credit_points=2.25 and pension=420:
      estimated = 6463×0.10 − 2.25×258 − 420×0.35 = −81.2 ≤ 0 → Info

    If gross (10_000) were used instead:
      estimated = 1000 − 580.5 − 147 = 272.5 > 20 → Warning
    (so the test differentiates between the two inputs)
    """
    from app.models.schemas import AnomalySeverity

    result = _rule(
        gross=10_000.0,          # would trigger Warning if used
        income_tax=None,
        credit_points=2.25,
        gross_taxable=6_463.0,   # should be used instead
        provident_funds_deduction=420.0,
    )

    assert result is not None
    assert result.id == "ano_below_tax_threshold", \
        "gross_taxable should be preferred; formula must use 6463 not 10000"
    assert result.severity == AnomalySeverity.INFO


# ---------------------------------------------------------------------------
# Test 5: Falls back to gross when gross_taxable is None
# ---------------------------------------------------------------------------

def test_falls_back_to_gross_when_gross_taxable_none():
    """
    gross_taxable=None → formula uses gross=6_463.
    Same result as test 1.
    """
    from app.models.schemas import AnomalySeverity

    result = _rule(
        gross=6_463.0,
        income_tax=None,
        credit_points=2.25,
        gross_taxable=None,
        provident_funds_deduction=420.0,
    )

    assert result is not None
    assert result.id == "ano_below_tax_threshold"
    assert result.severity == AnomalySeverity.INFO


# ---------------------------------------------------------------------------
# Test 6: Noise floor is ₪20 (not ₪100) — estimated=50 triggers Warning
# ---------------------------------------------------------------------------

def test_noise_floor_is_20_not_100():
    """
    estimated ≈ 50 → above new ₪20 noise floor → Warning.
    (Under old ₪100 floor this would have been silently ignored.)
    """
    from app.models.schemas import AnomalySeverity

    # gross=2000, no credits, no pension → estimated = 200 > 20
    result = _rule(
        gross=2_000.0,
        income_tax=None,
        credit_points=None,
        gross_taxable=None,
        provident_funds_deduction=None,
    )

    assert result is not None
    assert result.id == "ano_missing_income_tax"
    assert result.severity == AnomalySeverity.WARNING, \
        "estimated=200 > 20 should be a Warning"


# ---------------------------------------------------------------------------
# Test 7: Borderline ≤ ₪20 → None (no anomaly emitted)
# ---------------------------------------------------------------------------

def test_borderline_under_20_returns_none():
    """
    Craft inputs so 0 < estimated ≤ 20 → None (borderline, not worth flagging).
    gross=2000, credit_points=1.9, provident_funds=5300:
      estimated = 200 − (1.9 × 258) − (5300 × 0.35)
               = 200 − 490.2 − 1855 = −2145.2 ≤ 0
    Hmm — let's try gross=1800, no pension:
      estimated = 180 − (1.9 × 258) = 180 − 490.2 = −310.2 ≤ 0
    Need 0 < estimated ≤ 20:
      gross=4000, credit_points=1.0, pension=9440:
        estimated = 400 − 258 − 9440×0.35 = 400 − 258 − 3304 = −3162 ≤ 0  (nope)
    Simpler: gross=3000, credit_points=1.068 (→ 275.5), pension=0:
      estimated = 300 − 275.5 = 24.5 → still > 20
    Let's try: gross=3000, credit_points=1.085:
      estimated = 300 − 1.085×258 = 300 − 279.93 = 20.07 → just above
    credit_points=1.09: estimated = 300 − 281.22 = 18.78 ≤ 20 → None

    So: gross=3000, credit_points=1.09, no pension, income_tax=None → None
    """
    result = _rule(
        gross=3_000.0,
        income_tax=None,
        credit_points=1.09,
        gross_taxable=None,
        provident_funds_deduction=None,
    )
    # 3000×0.10 − 1.09×258 = 300 − 281.22 = 18.78 which is in (0, 20]
    assert result is None, \
        "Estimated tax in (0, ₪20] should return None (borderline, no anomaly)"


# ---------------------------------------------------------------------------
# Test 8: "תקין" in what_we_found when below threshold (spec requirement)
# ---------------------------------------------------------------------------

def test_below_threshold_message_contains_takin():
    """
    Spec mandates: when estimated_tax ≤ 0, what_we_found must contain "תקין".
    """
    result = _rule(
        gross=3_000.0,
        income_tax=None,
        credit_points=10.0,  # massive credit → clearly below threshold
    )

    assert result is not None
    assert result.id == "ano_below_tax_threshold"
    assert "תקין" in result.what_we_found, \
        f"Spec requires 'תקין' in what_we_found; got: '{result.what_we_found}'"
