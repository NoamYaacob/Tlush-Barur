"""
test_phase10.py — Tests for Phase 10: Universal Israeli Payslip Extractor.

Verifies the generic section-scanning engine (extract_line_items_by_sections)
and the new SectionDef dataclass / SECTION_DEFINITIONS adapter attribute.

Test list:
  1.  test_section_def_dataclass
  2.  test_generic_adapter_has_section_definitions
  3.  test_extract_by_sections_earnings_only
  4.  test_extract_by_sections_deductions_section
  5.  test_extract_by_sections_three_sections
  6.  test_unknown_item_in_deductions_gets_deduction_category
  7.  test_no_section_header_fallback
  8.  test_hilan_adapter_section_definitions
  9.  test_deduplication_preserved
  10. test_value_sign_by_section
"""

import pytest
from app.services.adapters import SectionDef, GenericAdapter, HilanAdapter, get_adapter
from app.services.parser import extract_line_items_by_sections
from app.models.schemas import LineItemCategory


# ---------------------------------------------------------------------------
# 1. SectionDef dataclass
# ---------------------------------------------------------------------------

def test_section_def_dataclass():
    """SectionDef instantiates correctly with all fields accessible."""
    import re
    sec = SectionDef(
        category_str="earning",
        header_patterns=[re.compile(r'תשלומים', re.UNICODE)],
        section_type="earnings_table",
    )
    assert sec.category_str == "earning"
    assert sec.section_type == "earnings_table"
    assert len(sec.header_patterns) == 1
    assert sec.header_patterns[0].search("פרוט התשלומים") is not None


# ---------------------------------------------------------------------------
# 2. GenericAdapter has SECTION_DEFINITIONS
# ---------------------------------------------------------------------------

def test_generic_adapter_has_section_definitions():
    """GenericAdapter.SECTION_DEFINITIONS must include earning, deduction, employer."""
    adapter = GenericAdapter()
    assert hasattr(adapter, "SECTION_DEFINITIONS"), "SECTION_DEFINITIONS attribute missing"
    defs = adapter.SECTION_DEFINITIONS
    assert len(defs) >= 3, f"Expected >= 3 SectionDefs, got {len(defs)}"

    cats = {sd.category_str for sd in defs}
    assert "earning" in cats, "No earning section defined"
    assert "deduction" in cats, "No deduction section defined"
    assert "employer_contribution" in cats, "No employer_contribution section defined"


# ---------------------------------------------------------------------------
# 3. Earnings-only page
# ---------------------------------------------------------------------------

def test_extract_by_sections_earnings_only():
    """Rows under an earnings section header must all be EARNING."""
    sample = {0: """\
פרוט התשלומים
משכורת בסיס 5,000.00
דמי הבראה 450.00
נסיעות 380.00
"""}
    items = extract_line_items_by_sections(sample)
    assert len(items) >= 3, f"Expected >= 3 items, got {len(items)}"
    for li in items:
        assert li.category == LineItemCategory.EARNING, (
            f"Item '{li.description_hebrew}' should be EARNING, got {li.category}"
        )


# ---------------------------------------------------------------------------
# 4. Earnings then deductions section (THE CORE SHIFT)
# ---------------------------------------------------------------------------

def test_extract_by_sections_deductions_section():
    """
    Rows under 'ניכויי חובה' must be DEDUCTION regardless of keyword matching.
    This is the central test of the Phase 10 paradigm shift.
    """
    sample = {0: """\
פרוט התשלומים
משכורת בסיס 5,000.00
נסיעות 380.00
ניכויי חובה
מס הכנסה 334.00
ביטוח לאומי 253.00
מס בריאות 81.00
"""}
    items = extract_line_items_by_sections(sample)
    earnings = [li for li in items if li.category == LineItemCategory.EARNING]
    deductions = [li for li in items if li.category == LineItemCategory.DEDUCTION]

    assert len(earnings) >= 2, f"Expected >= 2 earnings, got {len(earnings)}"
    assert len(deductions) >= 2, (
        f"Expected >= 2 deductions, got {len(deductions)}: "
        f"{[li.description_hebrew for li in deductions]}"
    )
    # Deduction values must be negative
    for li in deductions:
        assert li.value is not None and li.value < 0, (
            f"Deduction '{li.description_hebrew}' should be negative, got {li.value}"
        )


# ---------------------------------------------------------------------------
# 5. Three sections: earnings → deductions → employer contributions
# ---------------------------------------------------------------------------

def test_extract_by_sections_three_sections():
    """All three section types must be correctly split when headers appear."""
    sample = {0: """\
פרוט התשלומים
משכורת בסיס 5,000.00
נסיעות 380.00
ניכויי חובה
מס הכנסה 334.00
ביטוח לאומי 253.00
הפרשות מעסיק
הפרשת מעסיק לפנסיה 375.00
הפרשה לפיצויים 416.50
"""}
    items = extract_line_items_by_sections(sample)
    cats = {li.category for li in items}

    assert LineItemCategory.EARNING in cats, "No EARNING items found"
    assert LineItemCategory.DEDUCTION in cats, "No DEDUCTION items found"
    assert LineItemCategory.EMPLOYER_CONTRIBUTION in cats, "No EMPLOYER_CONTRIBUTION items found"

    # Employer contributions must be positive
    employer = [li for li in items if li.category == LineItemCategory.EMPLOYER_CONTRIBUTION]
    for li in employer:
        assert li.value is not None and li.value > 0, (
            f"Employer contribution '{li.description_hebrew}' should be positive, got {li.value}"
        )


# ---------------------------------------------------------------------------
# 6. Unknown item inside deductions section → DEDUCTION category
# ---------------------------------------------------------------------------

def test_unknown_item_in_deductions_gets_deduction_category():
    """
    An unrecognized description inside a deductions section must be marked
    is_unknown=True AND have category DEDUCTION (not EARNING as before Phase 10).
    This is the key behavior fix.
    """
    sample = {0: """\
ניכויי חובה
ניכוי מיוחד לא מוכר 120.00
"""}
    items = extract_line_items_by_sections(sample)
    assert len(items) >= 1, "Expected at least one item"

    unknown_items = [li for li in items if li.is_unknown]
    assert len(unknown_items) >= 1, "Expected at least one unknown item"

    for li in unknown_items:
        assert li.category == LineItemCategory.DEDUCTION, (
            f"Unknown item '{li.description_hebrew}' in deductions section should be "
            f"DEDUCTION, got {li.category}"
        )
        assert li.value is not None and li.value < 0, (
            f"Unknown deduction '{li.description_hebrew}' should be negative, got {li.value}"
        )


# ---------------------------------------------------------------------------
# 7. No section header — known-keyword fallback
# ---------------------------------------------------------------------------

def test_no_section_header_fallback():
    """
    When no section headers are present, known-keyword items must still be extracted
    (backward compatibility with providers that have no explicit section headers).
    """
    sample = {0: """\
משכורת בסיס 5,000.00
נסיעות 380.00
מס הכנסה 334.00
"""}
    items = extract_line_items_by_sections(sample)
    # Known-keyword fallback: at least the known items should be captured
    assert len(items) >= 2, (
        f"Expected >= 2 known-keyword items without section header, got {len(items)}"
    )
    # All items must not be unknown (the fallback only emits known items)
    for li in items:
        assert not li.is_unknown, (
            f"Fallback mode should only emit known items, got unknown: '{li.description_hebrew}'"
        )


# ---------------------------------------------------------------------------
# 8. HilanAdapter SECTION_DEFINITIONS
# ---------------------------------------------------------------------------

def test_hilan_adapter_section_definitions():
    """HilanAdapter must have הכנסות as earning header and standalone ניכויים as deduction."""
    adapter = HilanAdapter()
    assert hasattr(adapter, "SECTION_DEFINITIONS"), "HilanAdapter missing SECTION_DEFINITIONS"

    earning_defs = [sd for sd in adapter.SECTION_DEFINITIONS if sd.category_str == "earning"]
    deduction_defs = [sd for sd in adapter.SECTION_DEFINITIONS if sd.category_str == "deduction"]

    assert len(earning_defs) >= 1, "No earning SectionDef in HilanAdapter"
    assert len(deduction_defs) >= 1, "No deduction SectionDef in HilanAdapter"

    # Verify הכנסות triggers the earning section
    earning_hit = any(
        any(p.search("הכנסות") for p in sd.header_patterns)
        for sd in earning_defs
    )
    assert earning_hit, "HilanAdapter earning section must match 'הכנסות'"

    # Verify standalone 'ניכויים' triggers the deduction section
    deduction_hit = any(
        any(p.search("ניכויים") for p in sd.header_patterns)
        for sd in deduction_defs
    )
    assert deduction_hit, "HilanAdapter deduction section must match standalone 'ניכויים'"


# ---------------------------------------------------------------------------
# 9. Deduplication preserved
# ---------------------------------------------------------------------------

def test_deduplication_preserved():
    """
    Identical description + value appearing in two sections must be deduplicated
    to a single item (sign-normalised).
    """
    sample = {0: """\
פרוט התשלומים
משכורת בסיס 5,000.00
ניכויי חובה
משכורת בסיס 5,000.00
"""}
    items = extract_line_items_by_sections(sample)
    descriptions = [li.description_hebrew for li in items]
    # "משכורת בסיס" appears twice with the same amount but in different sections.
    # The dedup key is (description, rounded_abs_value) → only one survives.
    base_salary_items = [li for li in items if "משכורת" in li.description_hebrew]
    assert len(base_salary_items) == 1, (
        f"Duplicate (description, abs_value) should be deduplicated — got {len(base_salary_items)}"
    )


# ---------------------------------------------------------------------------
# 10. Value sign by section
# ---------------------------------------------------------------------------

def test_value_sign_by_section():
    """Section context must determine value sign regardless of keyword category."""
    # Use a simple amount and description that appears in both sections.
    # The earning section makes the item positive; the deduction section negative.
    # Since dedup uses (description, abs_value), and the display name from _LI_KNOWN_ITEMS
    # normalises the description, both items will have the same abs_value (380) but
    # different signs, meaning different dedup keys → both survive.
    sample = {0: """\
פרוט התשלומים
משכורת בסיס 5,000.00
ניכויי חובה
מס הכנסה 334.00
הפרשות מעסיק
הפרשת מעסיק לפנסיה 375.00
"""}
    items = extract_line_items_by_sections(sample)

    # Earnings must have positive values
    earnings = [li for li in items if li.category == LineItemCategory.EARNING]
    for li in earnings:
        assert li.value is not None and li.value > 0, (
            f"Earning '{li.description_hebrew}' must be positive, got {li.value}"
        )

    # Deductions must have negative values
    deductions = [li for li in items if li.category == LineItemCategory.DEDUCTION]
    for li in deductions:
        assert li.value is not None and li.value < 0, (
            f"Deduction '{li.description_hebrew}' must be negative, got {li.value}"
        )

    # Employer contributions must be positive
    employer = [li for li in items if li.category == LineItemCategory.EMPLOYER_CONTRIBUTION]
    for li in employer:
        assert li.value is not None and li.value > 0, (
            f"Employer contribution '{li.description_hebrew}' must be positive, got {li.value}"
        )
