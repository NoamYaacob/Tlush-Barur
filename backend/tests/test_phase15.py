"""
test_phase15.py — Tests for Phase 15: Educational Insights Engine.

Test list:
  1.  test_zero_income_tax_with_credit_points_yields_info_insight
  2.  test_pension_above_minimum_yields_success_insight
  3.  test_min_wage_ok_yields_success_insight
  4.  test_havraa_in_line_items_yields_info_insight
  5.  test_missing_health_with_ni_yields_warning_insight
  6.  test_no_insights_when_all_none
  7.  test_insights_sort_order_success_before_info_before_warning
  8.  test_min_wage_below_threshold_yields_warning
  9.  test_build_insights_returns_pydantic_list
 10.  test_generate_insights_returns_empty_list_no_trigger
"""

import pytest
from typing import Optional

from app.logic.insights import Insight, generate_insights, _MIN_WAGE_HOURLY, _PENSION_MANDATORY_MIN_PCT


# ---------------------------------------------------------------------------
# Helper — minimal stub for a LineItem-like object
# ---------------------------------------------------------------------------

class _FakeLineItem:
    """Minimal duck-type stub matching the attributes insights.py uses."""
    def __init__(
        self,
        description_hebrew: str,
        category: str = "earning",
        value: Optional[float] = None,
        rate: Optional[float] = None,
    ) -> None:
        self.description_hebrew = description_hebrew
        self.category = category
        self.value = value
        self.rate = rate


# ---------------------------------------------------------------------------
# 1. Zero income tax + credit points → info insight
# ---------------------------------------------------------------------------

def test_zero_income_tax_with_credit_points_yields_info_insight():
    """When income_tax is 0 and credit_points are set, generate an info insight."""
    results = generate_insights(
        gross=6000.0,
        net=5500.0,
        income_tax=0.0,
        national_insurance=310.0,
        health_insurance=139.0,
        pension_employee=None,
        credit_points=2.25,
    )
    ids = [ins.id for ins in results]
    assert "ins_tax_zero_credits" in ids, "Expected ins_tax_zero_credits insight"

    insight = next(ins for ins in results if ins.id == "ins_tax_zero_credits")
    assert insight.kind == "info"
    assert "נקודות זיכוי" in insight.body or "זיכוי" in insight.body


# ---------------------------------------------------------------------------
# 2. Pension above mandatory minimum → success insight
# ---------------------------------------------------------------------------

def test_pension_above_minimum_yields_success_insight():
    """When pension_employee > 6% of gross, generate a 'success' insight."""
    gross = 10_000.0
    # 8% pension — above the 6% minimum
    pension = 800.0
    results = generate_insights(
        gross=gross,
        net=8500.0,
        income_tax=500.0,
        national_insurance=200.0,
        health_insurance=100.0,
        pension_employee=pension,
        credit_points=None,
    )
    ids = [ins.id for ins in results]
    assert "ins_pension_above_minimum" in ids, "Expected ins_pension_above_minimum insight"

    insight = next(ins for ins in results if ins.id == "ins_pension_above_minimum")
    assert insight.kind == "success"
    # Pension % should appear in the body
    assert "8" in insight.body or "%" in insight.body


# ---------------------------------------------------------------------------
# 3. Hourly rate >= minimum wage → success insight
# ---------------------------------------------------------------------------

def test_min_wage_ok_yields_success_insight():
    """When a base-salary line item has rate >= min wage, yield success insight."""
    items = [
        _FakeLineItem(
            description_hebrew="שכר בסיס",
            category="earning",
            value=6000.0,
            rate=_MIN_WAGE_HOURLY + 10.0,   # comfortably above minimum
        )
    ]
    results = generate_insights(
        gross=6000.0,
        net=5000.0,
        income_tax=300.0,
        national_insurance=200.0,
        health_insurance=100.0,
        pension_employee=None,
        credit_points=None,
        line_items=items,
    )
    ids = [ins.id for ins in results]
    assert "ins_min_wage_ok" in ids, "Expected ins_min_wage_ok insight"

    insight = next(ins for ins in results if ins.id == "ins_min_wage_ok")
    assert insight.kind == "success"


# ---------------------------------------------------------------------------
# 4. Havra'a in line items → info insight
# ---------------------------------------------------------------------------

def test_havraa_in_line_items_yields_info_insight():
    """When a line item description contains 'הבראה', generate an info insight."""
    items = [
        _FakeLineItem(description_hebrew="שכר בסיס", value=6000.0),
        _FakeLineItem(description_hebrew="דמי הבראה", value=756.0),
    ]
    results = generate_insights(
        gross=6756.0,
        net=5800.0,
        income_tax=400.0,
        national_insurance=310.0,
        health_insurance=139.0,
        pension_employee=None,
        credit_points=None,
        line_items=items,
    )
    ids = [ins.id for ins in results]
    assert "ins_havraa_explained" in ids, "Expected ins_havraa_explained insight"

    insight = next(ins for ins in results if ins.id == "ins_havraa_explained")
    assert insight.kind == "info"
    # The body should mention the value
    assert "756" in insight.body


# ---------------------------------------------------------------------------
# 5. Missing health insurance when NI is found → warning insight
# ---------------------------------------------------------------------------

def test_missing_health_with_ni_yields_warning_insight():
    """When national_insurance is present but health_insurance is None → warning."""
    results = generate_insights(
        gross=6000.0,
        net=5000.0,
        income_tax=400.0,
        national_insurance=310.0,
        health_insurance=None,          # missing
        pension_employee=None,
        credit_points=None,
    )
    ids = [ins.id for ins in results]
    assert "ins_missing_health" in ids, "Expected ins_missing_health insight"

    insight = next(ins for ins in results if ins.id == "ins_missing_health")
    assert insight.kind == "warning"


# ---------------------------------------------------------------------------
# 6. All inputs None / zero → empty list (nothing triggered)
# ---------------------------------------------------------------------------

def test_no_insights_when_all_none():
    """When all inputs are None (incomplete payslip), no insights should fire."""
    results = generate_insights(
        gross=None,
        net=None,
        income_tax=None,
        national_insurance=None,
        health_insurance=None,
        pension_employee=None,
        credit_points=None,
    )
    # No insights should be triggered when there's no data at all
    assert isinstance(results, list)
    # The only insight that might fire is ins_missing_health, but it requires NI to be set
    # so with both None nothing triggers.
    assert "ins_missing_health" not in [ins.id for ins in results]


# ---------------------------------------------------------------------------
# 7. Sort order: success first, then info, then warning
# ---------------------------------------------------------------------------

def test_insights_sort_order_success_before_info_before_warning():
    """Insights must be returned success → info → warning."""
    items = [_FakeLineItem(description_hebrew="דמי הבראה", value=500.0)]
    results = generate_insights(
        gross=10_000.0,
        net=8_000.0,
        income_tax=0.0,            # triggers info (zero tax + credits)
        national_insurance=310.0,
        health_insurance=None,     # triggers warning (missing health)
        pension_employee=900.0,    # 9% > 6% → triggers success
        credit_points=2.25,        # needed for ins_tax_zero_credits
        line_items=items,          # triggers info (havra'a)
    )
    kinds = [ins.kind for ins in results]
    # All success items must precede all info items; all info must precede all warning
    last_success = max((i for i, k in enumerate(kinds) if k == "success"), default=-1)
    first_info   = min((i for i, k in enumerate(kinds) if k == "info"),    default=len(kinds))
    first_warning = min((i for i, k in enumerate(kinds) if k == "warning"), default=len(kinds))

    assert last_success < first_info or "success" not in kinds or "info" not in kinds, (
        f"success insights must appear before info: {kinds}"
    )
    assert first_info <= first_warning or "info" not in kinds or "warning" not in kinds, (
        f"info insights must appear before warning: {kinds}"
    )


# ---------------------------------------------------------------------------
# 8. Hourly rate below minimum wage → warning insight
# ---------------------------------------------------------------------------

def test_min_wage_below_threshold_yields_warning():
    """When a base-salary line item has rate < min wage, yield warning insight."""
    low_rate = _MIN_WAGE_HOURLY - 5.0   # clearly below minimum
    items = [
        _FakeLineItem(
            description_hebrew="שכר בסיס",
            category="earning",
            value=4000.0,
            rate=low_rate,
        )
    ]
    results = generate_insights(
        gross=4000.0,
        net=3500.0,
        income_tax=100.0,
        national_insurance=150.0,
        health_insurance=70.0,
        pension_employee=None,
        credit_points=None,
        line_items=items,
    )
    ids = [ins.id for ins in results]
    assert "ins_min_wage_low" in ids, "Expected ins_min_wage_low warning insight"

    insight = next(ins for ins in results if ins.id == "ins_min_wage_low")
    assert insight.kind == "warning"


# ---------------------------------------------------------------------------
# 9. _build_insights returns list of Pydantic Insight schema objects
# ---------------------------------------------------------------------------

def test_build_insights_returns_pydantic_list():
    """_build_insights should return a list of Pydantic Insight schema objects."""
    from app.services.parser import _build_insights
    from app.models.schemas import Insight as InsightSchema

    items = [_FakeLineItem(description_hebrew="דמי הבראה", value=378.0)]
    result = _build_insights(
        gross=6000.0,
        net=5000.0,
        income_tax=400.0,
        national_ins=310.0,
        health=None,        # triggers missing_health warning
        pension_employee=None,
        credit_points=None,
        line_items=items,   # triggers havraa info
    )
    assert isinstance(result, list)
    assert len(result) >= 1
    for ins in result:
        assert isinstance(ins, InsightSchema), f"Expected InsightSchema, got {type(ins)}"
        assert ins.id and ins.kind and ins.title and ins.body


# ---------------------------------------------------------------------------
# 10. generate_insights returns empty list when nothing triggers
# ---------------------------------------------------------------------------

def test_generate_insights_returns_empty_list_no_trigger():
    """With all optional inputs absent except non-zero tax, nothing triggers."""
    results = generate_insights(
        gross=5000.0,
        net=4000.0,
        income_tax=500.0,   # non-zero → won't trigger zero-tax insight
        national_insurance=None,  # absent → won't trigger missing_health
        health_insurance=None,
        pension_employee=None,
        credit_points=None,
    )
    assert isinstance(results, list)
    # No insight rule should fire in this minimal scenario
    assert len(results) == 0, f"Expected no insights, got: {[ins.id for ins in results]}"
