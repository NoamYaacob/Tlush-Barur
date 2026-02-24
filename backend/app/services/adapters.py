"""
Provider-specific parsing adapters for Phase 3 (Generic Multi-Slip Parsing v1).

Each adapter subclass overrides the anchor regex patterns used by parser.py
to locate payslip sections (earnings table, stop boundary, employer contributions,
YTD data, balances, and summary box). This lets the extraction logic stay generic
while handling layout differences between payroll software vendors.

Usage:
    adapter = get_adapter(provider_name)   # e.g. "חילן" → HilanAdapter()
    adapter = get_adapter(None)            # → GenericAdapter()

Rules:
- No imports from other app modules (avoids circular imports with parser.py).
- All patterns are compiled once at instantiation (dataclass field defaults).
- Use get_adapter() factory — never instantiate adapter classes directly in callers.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Base / Generic adapter
# ---------------------------------------------------------------------------

@dataclass
class ProviderAdapter:
    """
    Generic/fallback adapter. Used when the provider is unknown or unrecognised.
    Patterns here are the broadest possible to maximise recall across all layouts.
    """
    name: str = "generic"

    # Earnings table START anchors (first match wins across all pages)
    TABLE_START_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'פרו\S{0,2}\s+ה?תשלומ', re.UNICODE | re.IGNORECASE),  # "פרוט התשלומים" + typo variants
        re.compile(r'רכיבי\s+שכר', re.UNICODE | re.IGNORECASE),            # "רכיבי שכר"
        re.compile(r'הכנסות', re.UNICODE),                                  # bare "הכנסות" column header
    ])

    # Earnings table STOP anchors (first match ends the earnings region)
    TABLE_STOP_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'ניכויי\s+חובה', re.UNICODE | re.IGNORECASE),           # mandatory-deductions section
        re.compile(r'קופות\s+גמל', re.UNICODE | re.IGNORECASE),             # provident-funds section
        re.compile(r'ניכויים\s+והפרש', re.UNICODE | re.IGNORECASE),         # "deductions & differences"
        re.compile(r'סה["\u05d4]?כ\s+ברוטו', re.UNICODE | re.IGNORECASE),  # gross total line
        re.compile(r'ברוטו\s+ל(?:מס|ב\.?ל)', re.UNICODE | re.IGNORECASE),  # "ברוטו למס" / "ברוטו לב.ל"
    ])

    # Employer contributions section anchors (Phase C of extraction)
    CONTRIBUTIONS_ANCHOR_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'הפרשות\s+(?:מעסיק|סוציאליות)', re.UNICODE | re.IGNORECASE),
        re.compile(r'תשלומי\s+מעסיק', re.UNICODE | re.IGNORECASE),
        re.compile(r'קופות\s+גמל\s+מעסיק', re.UNICODE | re.IGNORECASE),
        re.compile(r'הראל|מנורה|מגדל|כלל', re.UNICODE),                     # insurance company names
    ])

    # YTD (year-to-date) section anchors
    YTD_ANCHOR_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'מצטבר\s+(?:שנתי|שנה|מתחילת)', re.UNICODE | re.IGNORECASE),
        re.compile(r'(?:נתונים|סיכום)\s+שנתי', re.UNICODE | re.IGNORECASE),
        re.compile(r'שנה\s+עד\s+היום', re.UNICODE | re.IGNORECASE),
    ])

    # Balance (carry-forward) section anchors
    BALANCE_ANCHOR_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'יתרת\s+(?:ימי\s+(?:חופש|מחלה)|שעות|ימים)', re.UNICODE | re.IGNORECASE),
        re.compile(r'יתרות', re.UNICODE),
    ])

    # Summary box anchors (right-hand totals panel)
    SUMMARY_BOX_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'ניכויי\s+חובה', re.UNICODE | re.IGNORECASE),
        re.compile(r'שכר\s+נטו', re.UNICODE | re.IGNORECASE),
        re.compile(r'נטו\s+לתשלום', re.UNICODE | re.IGNORECASE),
    ])


# ---------------------------------------------------------------------------
# Provider-specific adapters
# ---------------------------------------------------------------------------

@dataclass
class HilanAdapter(ProviderAdapter):
    """
    HILAN (חילן) — Israel's most widely-used payroll system.

    Layout characteristics:
    - Earnings section header is "הכנסות" (not "פרוט התשלומים")
    - Deductions appear under a standalone "ניכויים" header line
    - Uses two-column layout: earnings on the right, deductions on the left
    """
    name: str = "חילן"

    TABLE_START_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'הכנסות', re.UNICODE),                                  # HILAN primary header
        re.compile(r'פרו\S{0,2}\s+ה?תשלומ', re.UNICODE | re.IGNORECASE),  # generic fallback
        re.compile(r'רכיבי\s+שכר', re.UNICODE | re.IGNORECASE),
    ])

    TABLE_STOP_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'^ניכויים\s*$', re.UNICODE | re.MULTILINE),            # standalone "ניכויים" header
        re.compile(r'הפרשות\s+(?:מעסיק|סוציאליות)', re.UNICODE | re.IGNORECASE),
        re.compile(r'סה["\u05d4]?כ\s+ברוטו', re.UNICODE | re.IGNORECASE),
        re.compile(r'ניכויי\s+חובה', re.UNICODE | re.IGNORECASE),
    ])


@dataclass
class SynelAdapter(ProviderAdapter):
    """
    SYNEL (סינאל) — common in manufacturing, shift-based orgs, and factories.

    Layout characteristics:
    - Earnings table anchor: "פרוט שעות ותשלומים" (hours + payments detail)
    - Often combines time-tracking data with salary components
    """
    name: str = "סינאל"

    TABLE_START_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'פרוט\s+שעות\s+ותשלומים', re.UNICODE | re.IGNORECASE),  # SYNEL primary
        re.compile(r'שעות\s+ותשלומים', re.UNICODE | re.IGNORECASE),          # shorter variant
        re.compile(r'פרו\S{0,2}\s+ה?תשלומ', re.UNICODE | re.IGNORECASE),    # generic fallback
    ])


@dataclass
class MalamAdapter(ProviderAdapter):
    """
    MALAM-TEAM (מלאם-תים) — enterprise / large-org payroll.

    Layout characteristics:
    - Items prefixed with 3–4 digit numeric codes
    - Column headers: "קוד  תיאור  ..." or "רשימת רכיבים"
    - Dense two-column layouts with codes left of item names
    """
    name: str = "מלאם-תים"

    TABLE_START_PATTERNS: list[re.Pattern] = field(default_factory=lambda: [
        re.compile(r'רשימת\s+רכיבים', re.UNICODE | re.IGNORECASE),           # MALAM primary
        re.compile(r'קוד\s+תיאור', re.UNICODE | re.IGNORECASE),              # column header row
        re.compile(r'פרו\S{0,2}\s+ה?תשלומ', re.UNICODE | re.IGNORECASE),    # generic fallback
    ])


@dataclass
class GenericAdapter(ProviderAdapter):
    """
    Fallback when provider is unknown or not yet specialised (SAP, Priority, Reinhold, Base).
    Identical to the base ProviderAdapter — explicit class for clarity in logs/tests.
    """
    name: str = "generic"


# ---------------------------------------------------------------------------
# Lookup map + factory
# ---------------------------------------------------------------------------

_ADAPTER_MAP: dict[str, type[ProviderAdapter]] = {
    # Hebrew provider names (as returned by detect_provider())
    "חילן":       HilanAdapter,
    "סינאל":      SynelAdapter,
    "מלאם-תים":   MalamAdapter,
    "מלאם":       MalamAdapter,
    # Latin/English aliases (detect_provider() keywords)
    "hilan":      HilanAdapter,
    "synel":      SynelAdapter,
    "malam":      MalamAdapter,
    "malam-team": MalamAdapter,
}


def get_adapter(provider_name: str | None) -> ProviderAdapter:
    """
    Factory: return the most appropriate ProviderAdapter for the detected provider.

    Falls back to GenericAdapter for:
    - None (provider not detected)
    - Providers not yet specialised: SAP, Priority, Reinhold, Base, etc.

    Args:
        provider_name: Display name returned by detect_provider(), e.g. "חילן", or None.

    Returns:
        An instantiated ProviderAdapter subclass (never raises).

    Examples:
        get_adapter("חילן")      → HilanAdapter()
        get_adapter("hilan")     → HilanAdapter()
        get_adapter("מלאם-תים")  → MalamAdapter()
        get_adapter(None)        → GenericAdapter()
        get_adapter("SAP")       → GenericAdapter()
    """
    if not provider_name:
        return GenericAdapter()
    # Try exact match first, then lowercase
    adapter_cls = _ADAPTER_MAP.get(provider_name) or _ADAPTER_MAP.get(provider_name.lower())
    return adapter_cls() if adapter_cls else GenericAdapter()
