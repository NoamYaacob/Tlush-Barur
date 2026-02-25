"""
Provider-specific parsing adapters for Phase 3 (Generic Multi-Slip Parsing v1).

Each adapter subclass overrides the anchor regex patterns used by parser.py
to locate payslip sections (earnings table, stop boundary, employer contributions,
YTD data, balances, and summary box). This lets the extraction logic stay generic
while handling layout differences between payroll software vendors.

Phase 10: Added SectionDef dataclass and SECTION_DEFINITIONS attribute.
The new extract_line_items_by_sections() function in parser.py uses SECTION_DEFINITIONS
to detect ALL section headers and capture every Hebrew+amount row per section,
assigning LineItemCategory by section context rather than keyword matching.

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
# Phase 10: SectionDef — describes one logical section of a payslip
# ---------------------------------------------------------------------------

@dataclass
class SectionDef:
    """
    Describes one logical section of an Israeli payslip
    (e.g. earnings, mandatory deductions, employer contributions).

    Used by extract_line_items_by_sections() in parser.py to determine which
    LineItemCategory to assign to rows captured under each section header.

    Attributes:
        category_str:    String key for LineItemCategory — one of:
                         "earning", "deduction", "employer_contribution",
                         "benefit_in_kind", "balance".
                         Stored as a string to avoid circular imports with schemas.py
                         (the enum lives in app.models.schemas, which imports from
                         parser.py, which imports from here).
        header_patterns: Compiled regexes that identify this section's header line.
                         The scanner checks each line; the first pattern that matches
                         activates this section. The ORDERING of SectionDefs in
                         SECTION_DEFINITIONS matters only for tie-breaking — the
                         actual section transitions are driven by document order.
        section_type:    String tag compatible with SectionBlock.section_type.
    """
    category_str: str
    header_patterns: list[re.Pattern]
    section_type: str = "unknown_section"


# ---------------------------------------------------------------------------
# Phase 10: Generic section definitions builder
# ---------------------------------------------------------------------------

def _build_generic_section_defs() -> list[SectionDef]:
    """
    Return the default list of SectionDef objects covering all major Israeli
    payslip section headers. Used by GenericAdapter and as a base for provider
    overrides.

    Ordering within each category group is irrelevant for section assignment
    (which is driven by the document's top-to-bottom order). However, note that
    the employer-contribution `קופות גמל מעסיק` pattern MUST appear before any
    bare `קופות גמל` deduction pattern so that the employer section wins when
    both are present.

    The function is defined at module level (not as a class method) so that
    provider subclasses can call it to obtain the base list and prepend their
    own provider-specific patterns.
    """
    F = re.UNICODE | re.IGNORECASE
    FM = re.UNICODE | re.IGNORECASE | re.MULTILINE

    return [
        # ── Earnings sections ────────────────────────────────────────────────
        SectionDef(
            category_str="earning",
            section_type="earnings_table",
            header_patterns=[
                re.compile(r'פרו\S{0,2}\s+ה?תשלומ', F),        # פרוט התשלומים (most common)
                re.compile(r'רכיבי\s+שכר', F),                  # רכיבי שכר
                re.compile(r'הכנסות', re.UNICODE),               # הכנסות (Hilan primary — no IGNORECASE needed)
                re.compile(r'^\s*תשלומים\s*$', FM),             # standalone תשלומים
                re.compile(r'פרוט\s+שעות\s+ותשלומים', F),       # Synel primary
                re.compile(r'שעות\s+ותשלומים', F),              # Synel short form
                re.compile(r'רשימת\s+רכיבים', F),               # Malam primary
                re.compile(r'שכר\s+ו?תוספות', F),               # some providers
                re.compile(r'מרכיבי\s+שכר', F),                 # variant
            ],
        ),
        # ── Deduction sections ───────────────────────────────────────────────
        # NOTE: קופות גמל מעסיק (employer) is checked in employer section below.
        # The bare קופות גמל pattern here uses a negative lookahead to skip
        # the employer variant and only match the employee-side deduction header.
        SectionDef(
            category_str="deduction",
            section_type="deductions_section",
            header_patterns=[
                re.compile(r'ניכויי\s+חובה', F),                # mandatory deductions (most common)
                re.compile(r'ניכויים\s+חובה', F),               # variant spacing
                re.compile(r'^ניכויים\s*$', FM),                # standalone ניכויים — Hilan
                re.compile(r'ניכויים\s+והפרש', F),              # ניכויים והפרשות
                re.compile(r'ניכויים\s+שונים', F),              # other deductions
                re.compile(r'קופות\s+גמל(?!\s+מעסיק)', F),     # employee provident funds (not employer)
            ],
        ),
        # ── Employer contribution sections ───────────────────────────────────
        SectionDef(
            category_str="employer_contribution",
            section_type="contributions_section",
            header_patterns=[
                re.compile(r'הפרשות\s+(?:מעסיק|סוציאליות)', F),  # הפרשות מעסיק
                re.compile(r'קופות\s+גמל\s+מעסיק', F),           # must match before bare קופות גמל
                re.compile(r'תשלומי\s+מעסיק', F),
                re.compile(r'הפרשות\s+המעסיק', F),
                re.compile(r'ביטוח\s+ו?פנסיה\s+מעסיק', F),
            ],
        ),
    ]


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

    # Phase 10: Section definitions for generic section-scanning engine.
    # Defines which section headers map to which LineItemCategory.
    SECTION_DEFINITIONS: list[SectionDef] = field(
        default_factory=_build_generic_section_defs
    )


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

    # Phase 10: Hilan-specific section definitions.
    # Earnings header is "הכנסות"; deductions header is standalone "ניכויים".
    SECTION_DEFINITIONS: list[SectionDef] = field(default_factory=lambda: [
        SectionDef(
            category_str="earning",
            section_type="earnings_table",
            header_patterns=[
                re.compile(r'הכנסות', re.UNICODE),                                         # Hilan primary
                re.compile(r'פרו\S{0,2}\s+ה?תשלומ', re.UNICODE | re.IGNORECASE),         # generic fallback
                re.compile(r'רכיבי\s+שכר', re.UNICODE | re.IGNORECASE),
                re.compile(r'^\s*תשלומים\s*$', re.UNICODE | re.IGNORECASE | re.MULTILINE),
                re.compile(r'שכר\s+ו?תוספות', re.UNICODE | re.IGNORECASE),
                re.compile(r'מרכיבי\s+שכר', re.UNICODE | re.IGNORECASE),
            ],
        ),
        SectionDef(
            category_str="deduction",
            section_type="deductions_section",
            header_patterns=[
                re.compile(r'^ניכויים\s*$', re.UNICODE | re.MULTILINE),                   # Hilan primary
                re.compile(r'ניכויי\s+חובה', re.UNICODE | re.IGNORECASE),
                re.compile(r'ניכויים\s+חובה', re.UNICODE | re.IGNORECASE),
                re.compile(r'ניכויים\s+והפרש', re.UNICODE | re.IGNORECASE),
                re.compile(r'ניכויים\s+שונים', re.UNICODE | re.IGNORECASE),
                re.compile(r'קופות\s+גמל(?!\s+מעסיק)', re.UNICODE | re.IGNORECASE),
            ],
        ),
        SectionDef(
            category_str="employer_contribution",
            section_type="contributions_section",
            header_patterns=[
                re.compile(r'הפרשות\s+(?:מעסיק|סוציאליות)', re.UNICODE | re.IGNORECASE),
                re.compile(r'קופות\s+גמל\s+מעסיק', re.UNICODE | re.IGNORECASE),
                re.compile(r'תשלומי\s+מעסיק', re.UNICODE | re.IGNORECASE),
                re.compile(r'הפרשות\s+המעסיק', re.UNICODE | re.IGNORECASE),
            ],
        ),
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

    # Phase 10: Synel-specific section definitions.
    SECTION_DEFINITIONS: list[SectionDef] = field(default_factory=lambda: [
        SectionDef(
            category_str="earning",
            section_type="earnings_table",
            header_patterns=[
                re.compile(r'פרוט\s+שעות\s+ותשלומים', re.UNICODE | re.IGNORECASE),  # Synel primary
                re.compile(r'שעות\s+ותשלומים', re.UNICODE | re.IGNORECASE),
                re.compile(r'פרו\S{0,2}\s+ה?תשלומ', re.UNICODE | re.IGNORECASE),
                re.compile(r'רכיבי\s+שכר', re.UNICODE | re.IGNORECASE),
                re.compile(r'הכנסות', re.UNICODE),
                re.compile(r'^\s*תשלומים\s*$', re.UNICODE | re.IGNORECASE | re.MULTILINE),
            ],
        ),
        SectionDef(
            category_str="deduction",
            section_type="deductions_section",
            header_patterns=[
                re.compile(r'ניכויי\s+חובה', re.UNICODE | re.IGNORECASE),
                re.compile(r'ניכויים\s+חובה', re.UNICODE | re.IGNORECASE),
                re.compile(r'^ניכויים\s*$', re.UNICODE | re.MULTILINE),
                re.compile(r'ניכויים\s+והפרש', re.UNICODE | re.IGNORECASE),
                re.compile(r'ניכויים\s+שונים', re.UNICODE | re.IGNORECASE),
                re.compile(r'קופות\s+גמל(?!\s+מעסיק)', re.UNICODE | re.IGNORECASE),
            ],
        ),
        SectionDef(
            category_str="employer_contribution",
            section_type="contributions_section",
            header_patterns=[
                re.compile(r'הפרשות\s+(?:מעסיק|סוציאליות)', re.UNICODE | re.IGNORECASE),
                re.compile(r'קופות\s+גמל\s+מעסיק', re.UNICODE | re.IGNORECASE),
                re.compile(r'תשלומי\s+מעסיק', re.UNICODE | re.IGNORECASE),
                re.compile(r'הפרשות\s+המעסיק', re.UNICODE | re.IGNORECASE),
            ],
        ),
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

    # Phase 10: Malam-specific section definitions.
    SECTION_DEFINITIONS: list[SectionDef] = field(default_factory=lambda: [
        SectionDef(
            category_str="earning",
            section_type="earnings_table",
            header_patterns=[
                re.compile(r'רשימת\s+רכיבים', re.UNICODE | re.IGNORECASE),   # Malam primary
                re.compile(r'קוד\s+תיאור', re.UNICODE | re.IGNORECASE),
                re.compile(r'פרו\S{0,2}\s+ה?תשלומ', re.UNICODE | re.IGNORECASE),
                re.compile(r'רכיבי\s+שכר', re.UNICODE | re.IGNORECASE),
                re.compile(r'הכנסות', re.UNICODE),
                re.compile(r'^\s*תשלומים\s*$', re.UNICODE | re.IGNORECASE | re.MULTILINE),
            ],
        ),
        SectionDef(
            category_str="deduction",
            section_type="deductions_section",
            header_patterns=[
                re.compile(r'ניכויי\s+חובה', re.UNICODE | re.IGNORECASE),
                re.compile(r'ניכויים\s+חובה', re.UNICODE | re.IGNORECASE),
                re.compile(r'^ניכויים\s*$', re.UNICODE | re.MULTILINE),
                re.compile(r'ניכויים\s+והפרש', re.UNICODE | re.IGNORECASE),
                re.compile(r'ניכויים\s+שונים', re.UNICODE | re.IGNORECASE),
                re.compile(r'קופות\s+גמל(?!\s+מעסיק)', re.UNICODE | re.IGNORECASE),
            ],
        ),
        SectionDef(
            category_str="employer_contribution",
            section_type="contributions_section",
            header_patterns=[
                re.compile(r'הפרשות\s+(?:מעסיק|סוציאליות)', re.UNICODE | re.IGNORECASE),
                re.compile(r'קופות\s+גמל\s+מעסיק', re.UNICODE | re.IGNORECASE),
                re.compile(r'תשלומי\s+מעסיק', re.UNICODE | re.IGNORECASE),
                re.compile(r'הפרשות\s+המעסיק', re.UNICODE | re.IGNORECASE),
            ],
        ),
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
