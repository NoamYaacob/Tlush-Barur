"""
Phase 5 tests: Tax Credits Wizard — compute_credits() function.

All tests use synthetic data (no real files, no DB, no HTTP).

Test inventory (8 tests):
  1.  Single, 0 children, no degree, no army → expected = 2.25
  2.  Married, 0 children, no extras → expected = 2.75
  3.  Married, 2 children, degree, army → expected = 5.75
  4.  Gap: detected < expected → gap_direction = "under", mismatch_reasons non-empty
  5.  Gap: detected ≈ expected (within 0.5) → gap_direction = "ok"
  6.  Gap: detected > expected → gap_direction = "over"
  7.  detected_points = None → gap = None, gap_direction = "unknown"
  8.  All fields "unknown" → confidence < 0.70

Formula reminder (2025 rules):
    base = 2.25 (resident)
    married → +0.50
    1st child → +1.00
    each extra child → +0.50
    degree → +1.00
    army → +0.50
    new immigrant → +0.50 (conservative)
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helper: build a CreditWizardRequest-like object from kwargs
# ---------------------------------------------------------------------------

def _req(
    marital_status="single",
    num_children=0,
    has_degree="no",
    has_army_service="no",
    is_new_immigrant="no",
    is_disabled="no",
):
    """Return a CreditWizardRequest with the given field values."""
    from app.models.schemas import CreditWizardRequest
    return CreditWizardRequest(
        marital_status=marital_status,
        num_children=num_children,
        has_degree=has_degree,
        has_army_service=has_army_service,
        is_new_immigrant=is_new_immigrant,
        is_disabled=is_disabled,
    )


def _compute(req, detected=None):
    """Thin wrapper around compute_credits."""
    from app.services.credits_wizard import compute_credits
    return compute_credits(req, detected)


# ---------------------------------------------------------------------------
# Test 1: Single, 0 children, no degree, no army → 2.25 pts
# ---------------------------------------------------------------------------

def test_single_no_children_no_extras():
    """Base case: single, no children, no extras → expected = 2.25."""
    req = _req(marital_status="single", num_children=0, has_degree="no", has_army_service="no")
    result = _compute(req)
    assert result.expected_points == pytest.approx(2.25), \
        f"Expected 2.25, got {result.expected_points}"
    # Base resident component must be present and applied
    base = next((c for c in result.components if "בסיס" in c.label_hebrew), None)
    assert base is not None, "Base resident component not found"
    assert base.applied is True
    assert base.points == pytest.approx(2.25)


# ---------------------------------------------------------------------------
# Test 2: Married, 0 children, no extras → 2.75 pts
# ---------------------------------------------------------------------------

def test_married_no_children():
    """Married with no children → 2.25 + 0.50 = 2.75."""
    req = _req(marital_status="married", num_children=0)
    result = _compute(req)
    assert result.expected_points == pytest.approx(2.75), \
        f"Expected 2.75, got {result.expected_points}"
    married_comp = next((c for c in result.components if "נשוי" in c.label_hebrew), None)
    assert married_comp is not None and married_comp.applied is True, \
        "Married component not applied"


# ---------------------------------------------------------------------------
# Test 3: Married, 2 children, degree, army → 5.75 pts
# ---------------------------------------------------------------------------

def test_married_2children_degree_army():
    """
    Married (+0.50) + 2 children (+1.00+0.50) + degree (+1.00) + army (+0.50)
    = 2.25 + 0.50 + 1.50 + 1.00 + 0.50 = 5.75
    """
    req = _req(
        marital_status="married",
        num_children=2,
        has_degree="yes",
        has_army_service="yes",
    )
    result = _compute(req)
    assert result.expected_points == pytest.approx(5.75), \
        f"Expected 5.75, got {result.expected_points}"
    # All four non-base components must be applied
    degree_c = next((c for c in result.components if "אקדמי" in c.label_hebrew), None)
    army_c   = next((c for c in result.components if "צבאי" in c.label_hebrew), None)
    assert degree_c is not None and degree_c.applied, "Degree component not applied"
    assert army_c   is not None and army_c.applied,   "Army component not applied"


# ---------------------------------------------------------------------------
# Test 4: Gap detected < expected → gap_direction = "under"
# ---------------------------------------------------------------------------

def test_gap_under_direction():
    """When detected (2.25) < expected (4.25), gap_direction must be 'under'."""
    req = _req(marital_status="married", has_degree="yes")  # expected = 2.25+0.50+1.00 = 3.75
    result = _compute(req, detected=2.25)
    assert result.gap_direction == "under", \
        f"Expected 'under', got {result.gap_direction}"
    assert result.gap is not None and result.gap > 0, \
        f"Gap must be positive for 'under', got {result.gap}"
    assert len(result.mismatch_reasons) > 0, \
        "mismatch_reasons must be non-empty for 'under' direction"
    assert result.what_to_do, "what_to_do must not be empty"


# ---------------------------------------------------------------------------
# Test 5: Gap detected ≈ expected (within 0.5) → gap_direction = "ok"
# ---------------------------------------------------------------------------

def test_gap_ok_direction():
    """When |detected - expected| < 0.50, gap_direction must be 'ok'."""
    req = _req(marital_status="married")   # expected = 2.75
    result = _compute(req, detected=2.75)  # gap = 0.0 → ok
    assert result.gap_direction == "ok", \
        f"Expected 'ok', got {result.gap_direction}"
    assert result.gap is not None
    assert abs(result.gap) < 0.50


# ---------------------------------------------------------------------------
# Test 6: Gap detected > expected → gap_direction = "over"
# ---------------------------------------------------------------------------

def test_gap_over_direction():
    """When detected (5.0) > expected (2.75), gap_direction must be 'over'."""
    req = _req(marital_status="married")   # expected = 2.75
    result = _compute(req, detected=5.0)
    assert result.gap_direction == "over", \
        f"Expected 'over', got {result.gap_direction}"
    assert result.gap is not None and result.gap < 0, \
        "Gap must be negative for 'over' direction"
    assert len(result.mismatch_reasons) > 0, \
        "mismatch_reasons must be non-empty for 'over' direction"


# ---------------------------------------------------------------------------
# Test 7: detected_points = None → gap = None, gap_direction = "unknown"
# ---------------------------------------------------------------------------

def test_no_detected_points_unknown():
    """When detected_points is None, gap must be None and direction 'unknown'."""
    req = _req(marital_status="married")
    result = _compute(req, detected=None)
    assert result.gap is None, f"Gap must be None when no detected points, got {result.gap}"
    assert result.gap_direction == "unknown", \
        f"Expected 'unknown', got {result.gap_direction}"


# ---------------------------------------------------------------------------
# Test 8: All fields "unknown" → confidence < 0.70
# ---------------------------------------------------------------------------

def test_all_unknown_low_confidence():
    """When all wizard fields are 'unknown', confidence must be below 0.70."""
    from app.models.schemas import CreditWizardRequest
    req = CreditWizardRequest(
        marital_status="unknown",
        num_children=0,
        has_degree="unknown",
        has_army_service="unknown",
        is_new_immigrant="unknown",
        is_disabled="unknown",
    )
    result = _compute(req)
    assert result.confidence < 0.70, \
        f"Expected confidence < 0.70 when all unknown, got {result.confidence}"
    # Expected points still computed (base 2.25 only, since married/degree/army are unknown)
    assert result.expected_points == pytest.approx(2.25), \
        f"With all unknown, only base (2.25) should apply, got {result.expected_points}"
