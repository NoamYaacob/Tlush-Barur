"""
Phase 3 tests: provider adapters, generic block detection,
line-item extraction with adapters, YTD extraction, balance extraction,
and schema serialization.

All tests use synthetic OCR text — no real image files required.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Test 1: get_adapter factory returns correct adapter types
# ---------------------------------------------------------------------------

def test_get_adapter_factory():
    from app.services.adapters import (
        get_adapter,
        HilanAdapter, SynelAdapter, MalamAdapter, GenericAdapter,
    )
    # Hebrew provider names (as returned by detect_provider)
    assert isinstance(get_adapter("חילן"), HilanAdapter),      "חילן → HilanAdapter"
    assert isinstance(get_adapter("סינאל"), SynelAdapter),     "סינאל → SynelAdapter"
    assert isinstance(get_adapter("מלאם-תים"), MalamAdapter),  "מלאם-תים → MalamAdapter"
    assert isinstance(get_adapter("מלאם"), MalamAdapter),      "מלאם → MalamAdapter"
    # English aliases
    assert isinstance(get_adapter("hilan"), HilanAdapter),     "hilan → HilanAdapter"
    assert isinstance(get_adapter("synel"), SynelAdapter),     "synel → SynelAdapter"
    assert isinstance(get_adapter("malam"), MalamAdapter),     "malam → MalamAdapter"
    # None + unknown → GenericAdapter
    assert isinstance(get_adapter(None), GenericAdapter),      "None → GenericAdapter"
    assert isinstance(get_adapter("SAP"), GenericAdapter),     "SAP → GenericAdapter"
    assert isinstance(get_adapter("Priority"), GenericAdapter),"Priority → GenericAdapter"
    assert isinstance(get_adapter(""), GenericAdapter),        "empty string → GenericAdapter"


# ---------------------------------------------------------------------------
# Test 2: HilanAdapter TABLE_START_PATTERNS matches "הכנסות" as primary anchor
# ---------------------------------------------------------------------------

def test_hilan_adapter_table_start_matches_hakanasot():
    from app.services.adapters import get_adapter
    adapter = get_adapter("חילן")
    # "הכנסות" is the primary HILAN earnings table header
    assert any(p.search("הכנסות") for p in adapter.TABLE_START_PATTERNS), \
        "HilanAdapter.TABLE_START_PATTERNS must match 'הכנסות'"
    # Generic fallback also present
    assert any(p.search("פרוט התשלומים") for p in adapter.TABLE_START_PATTERNS), \
        "HilanAdapter.TABLE_START_PATTERNS must also match generic 'פרוט התשלומים'"


# ---------------------------------------------------------------------------
# Test 3: SynelAdapter TABLE_START_PATTERNS matches SYNEL-specific anchor
# ---------------------------------------------------------------------------

def test_synel_adapter_table_start_matches_synel_anchor():
    from app.services.adapters import get_adapter
    adapter = get_adapter("סינאל")
    assert any(p.search("פרוט שעות ותשלומים") for p in adapter.TABLE_START_PATTERNS), \
        "SynelAdapter must match 'פרוט שעות ותשלומים'"
    # Generic fallback present
    assert any(p.search("פרוט התשלומים") for p in adapter.TABLE_START_PATTERNS), \
        "SynelAdapter must also match generic anchor as fallback"


# ---------------------------------------------------------------------------
# Test 4: detect_section_blocks finds earnings_table and summary_box sections
# ---------------------------------------------------------------------------

_HILAN_SAMPLE = """\
תלוש שכר לחודש ינואר 2026
חברה בע"מ

הכנסות
שכר יסוד 12,000
שעות נוספות 850

ניכויי חובה - מסים 2,500
ביטוח לאומי 900

שכר נטו 9,600
נטו לתשלום 9,584
"""


def test_detect_section_blocks_finds_earnings_and_summary():
    from app.services.parser import detect_section_blocks
    from app.services.adapters import get_adapter
    adapter = get_adapter("חילן")
    pages_text = {0: _HILAN_SAMPLE}
    blocks = detect_section_blocks(pages_text, adapter)
    section_types = {b.section_type for b in blocks}
    assert "earnings_table" in section_types, \
        f"Expected 'earnings_table' in section_types, got: {section_types}"
    assert "summary_box" in section_types, \
        f"Expected 'summary_box' in section_types, got: {section_types}"
    # Each block must have section_type set (not "page" for this sample)
    for b in blocks:
        assert b.section_type != "", "section_type must not be empty string"
        assert b.raw_text_preview is None, "raw_text_preview must be None (privacy)"


# ---------------------------------------------------------------------------
# Test 5: detect_section_blocks fallback produces "page" blocks when no anchors
# ---------------------------------------------------------------------------

def test_detect_section_blocks_fallback_page_blocks():
    from app.services.parser import detect_section_blocks
    from app.services.adapters import GenericAdapter
    adapter = GenericAdapter()
    # Text with no recognisable section anchors
    pages_text = {0: "שכר עובד 5000", 1: "דמי הבראה 450"}
    blocks = detect_section_blocks(pages_text, adapter)
    assert len(blocks) > 0, "Fallback must produce at least one block"
    # All fallback blocks should have section_type="page"
    page_blocks = [b for b in blocks if b.section_type == "page"]
    assert len(page_blocks) == len(blocks), \
        "When no anchors found, all blocks should have section_type='page'"


# ---------------------------------------------------------------------------
# Test 6: extract_line_items_ocr with explicit adapter produces same results
# ---------------------------------------------------------------------------

_LI_TABLE_SAMPLE = """\
פרוט התשלומים
שכר יסוד 10,000.00
שעות נוספות 500.00
ניכויי חובה - מסים 1,800.00
"""


def test_extract_line_items_ocr_with_explicit_adapter():
    from app.services.parser import extract_line_items_ocr
    from app.services.adapters import GenericAdapter
    adapter = GenericAdapter()
    pages_text = {0: _LI_TABLE_SAMPLE}
    # Call with adapter
    items_with = extract_line_items_ocr(pages_text, adapter)
    # Call without adapter (backward compat)
    items_without = extract_line_items_ocr(pages_text)
    # Both should return the same number of items
    assert len(items_with) == len(items_without), \
        f"Adapter shouldn't change item count: {len(items_with)} vs {len(items_without)}"
    # Both should find at least 1 item
    assert len(items_with) >= 1, "Should find at least one line item"


# ---------------------------------------------------------------------------
# Test 7: extract_ytd_ocr finds YTD values when anchor + data present
# ---------------------------------------------------------------------------

_YTD_SAMPLE = """\
תלוש שכר
מצטבר שנתי
מצטבר ברוטו: 78,000
מצטבר נטו: 58,000
מצטבר מס הכנסה: 12,000
מצטבר ביטוח לאומי: 5,400
"""


def test_extract_ytd_ocr_finds_values():
    from app.services.parser import extract_ytd_ocr
    from app.services.adapters import GenericAdapter
    adapter = GenericAdapter()
    pages_text = {0: _YTD_SAMPLE}
    ytd = extract_ytd_ocr(pages_text, adapter)
    assert ytd is not None, "YTD section should be found"
    assert ytd.gross_ytd == pytest.approx(78000.0), \
        f"gross_ytd expected 78000.0, got {ytd.gross_ytd}"
    assert ytd.net_ytd == pytest.approx(58000.0), \
        f"net_ytd expected 58000.0, got {ytd.net_ytd}"
    assert ytd.income_tax_ytd == pytest.approx(12000.0), \
        f"income_tax_ytd expected 12000.0, got {ytd.income_tax_ytd}"
    assert ytd.national_insurance_ytd == pytest.approx(5400.0), \
        f"national_insurance_ytd expected 5400.0, got {ytd.national_insurance_ytd}"
    assert 0.0 < ytd.confidence <= 1.0, "confidence must be in (0, 1]"


# ---------------------------------------------------------------------------
# Test 8: extract_ytd_ocr returns None when no YTD section
# ---------------------------------------------------------------------------

def test_extract_ytd_ocr_returns_none_when_absent():
    from app.services.parser import extract_ytd_ocr
    from app.services.adapters import GenericAdapter
    adapter = GenericAdapter()
    pages_text = {0: "שכר יסוד 12,000\nנטו לתשלום 9,000\nביטוח לאומי 900"}
    ytd = extract_ytd_ocr(pages_text, adapter)
    assert ytd is None, "No YTD anchor → should return None"


# ---------------------------------------------------------------------------
# Test 9: extract_balances_ocr finds vacation days and sick days
# ---------------------------------------------------------------------------

_BALANCE_SAMPLE = """\
יתרות חופש ומחלה
יתרת ימי חופש: 14
יתרת ימי מחלה: 8
"""


def test_extract_balances_ocr_finds_vacation_and_sick():
    from app.services.parser import extract_balances_ocr
    from app.services.adapters import GenericAdapter
    adapter = GenericAdapter()
    pages_text = {0: _BALANCE_SAMPLE}
    balances = extract_balances_ocr(pages_text, adapter)
    assert len(balances) >= 2, f"Expected ≥2 balance items, got {len(balances)}"
    ids = {b.id for b in balances}
    assert "bal_vacation_days" in ids, "Vacation days balance not found"
    assert "bal_sick_days" in ids, "Sick days balance not found"
    vac = next(b for b in balances if b.id == "bal_vacation_days")
    assert vac.balance_value == pytest.approx(14.0), \
        f"Vacation days expected 14.0, got {vac.balance_value}"
    assert vac.unit == "days", f"unit expected 'days', got {vac.unit}"
    assert vac.raw_text is None, "raw_text must be None (privacy)"
    sick = next(b for b in balances if b.id == "bal_sick_days")
    assert sick.balance_value == pytest.approx(8.0)
    assert sick.unit == "days"


# ---------------------------------------------------------------------------
# Test 10: ParsedSlipPayload schema accepts ytd and balances and serializes
# ---------------------------------------------------------------------------

def test_schema_ytd_and_balances_serialization():
    from app.models.schemas import (
        ParsedSlipPayload, SlipMeta, SummaryTotals,
        YTDMetrics, BalanceItem,
    )
    ytd = YTDMetrics(
        gross_ytd=100_000.0,
        net_ytd=72_000.0,
        income_tax_ytd=18_000.0,
        confidence=0.65,
    )
    balances = [
        BalanceItem(id="bal_vacation_days", name_hebrew="יתרת ימי חופש",
                    balance_value=12.0, unit="days", confidence=0.70),
        BalanceItem(id="bal_sick_days", name_hebrew="יתרת ימי מחלה",
                    balance_value=6.5, unit="days", confidence=0.70),
    ]
    payload = ParsedSlipPayload(
        slip_meta=SlipMeta(),
        summary=SummaryTotals(),
        line_items=[],
        anomalies=[],
        blocks=[],
        ytd=ytd,
        balances=balances,
    )
    # Verify field values
    assert payload.ytd is not None
    assert payload.ytd.gross_ytd == pytest.approx(100_000.0)
    assert payload.ytd.net_ytd == pytest.approx(72_000.0)
    assert len(payload.balances) == 2
    assert payload.balances[0].unit == "days"
    assert payload.balances[1].balance_value == pytest.approx(6.5)
    # Verify JSON serialization round-trip
    json_str = payload.model_dump_json()
    assert '"gross_ytd":100000.0' in json_str or '"gross_ytd": 100000.0' in json_str \
        or "100000" in json_str, "YTD values must appear in serialized JSON"
    assert "bal_vacation_days" in json_str, "Balance item ids must appear in JSON"
    # Default: payload without ytd/balances still works (backward compat)
    payload_plain = ParsedSlipPayload(
        slip_meta=SlipMeta(),
        summary=SummaryTotals(),
        line_items=[],
        anomalies=[],
        blocks=[],
    )
    assert payload_plain.ytd is None, "Default ytd must be None"
    assert payload_plain.balances == [], "Default balances must be []"
