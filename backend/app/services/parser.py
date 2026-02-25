"""
PDF text-layer parser for Israeli payslips.
Phase 2B: replaces the fully-mocked payload with real regex extraction.

All functions are synchronous; callers must wrap with asyncio.to_thread().
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Confidence tiers
# ---------------------------------------------------------------------------

TEXT_MIN_CHARS = 50          # total stripped chars to consider text layer present
_HEBREW_UNICODE_MIN = 10     # minimum real Hebrew Unicode code points required in text layer
CONFIDENCE_EXACT = 0.85      # primary keyword + value pattern matched
CONFIDENCE_AMBIGUOUS = 0.60  # bare fallback keyword matched
CONFIDENCE_BOOST = 0.90      # boosted when same numeric value appears 2+ times
CREDIT_POINT_VALUE = 228.0   # ₪ per credit point monthly (2024 rate, Israeli tax authority)

# OCR confidence scale: OCR text is less reliable, so scale down all confidences
OCR_CONFIDENCE_SCALE = 0.75


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ExtractedField:
    value: float
    raw_text: str
    confidence: float
    source_page: int  # 0-indexed


# ---------------------------------------------------------------------------
# Provider detection table
# ---------------------------------------------------------------------------

# (display_name, search_strings, confidence_weight)
_PROVIDER_PATTERNS: list[tuple[str, list[str], float]] = [
    ("חילן",           ["חילן", "hilan"],                         1.0),
    ("סינאל",          ["סינאל", "synel"],                        1.0),
    ("מלאם-תים",       ["מלאם", "malam", "מלאם-תים"],             1.0),
    ("שכר גלובל",      ["שכר גלובל", "shachar global"],           1.0),
    ("ריינהולד קולר",  ["ריינהולד", "reinholds", "קולר", "rk"],   0.9),
    ("Priority",       ["priority"],                               0.9),
    ("SAP",            ["sap"],                                    0.9),
    ("בייס",           ["בייס", "base payroll"],                   0.9),
]


# ---------------------------------------------------------------------------
# Phase 11: Payslip Gatekeeper
# ---------------------------------------------------------------------------

# Keyword patterns that must appear for a document to be recognised as a payslip.
# A document must match at least _PAYSLIP_MIN_KEYWORD_HITS distinct patterns.
_PAYSLIP_KEYWORD_PATTERNS: list[re.Pattern] = [
    re.compile(r'תלוש',         re.UNICODE),       # payslip
    re.compile(r'שכר',          re.UNICODE),       # salary
    re.compile(r'ברוטו',        re.UNICODE),       # gross
    re.compile(r'נטו',          re.UNICODE),       # net
    re.compile(r'ניכו',         re.UNICODE),       # deduction prefix (ניכויים / ניכוי)
    re.compile(r'מעסיק',        re.UNICODE),       # employer
    re.compile(r'עובד',         re.UNICODE),       # employee
    re.compile(r'תשלומ',        re.UNICODE),       # payments prefix
    re.compile(r'מס\s+הכנסה',   re.UNICODE),       # income tax
    re.compile(r'ביטוח\s+לאומי', re.UNICODE),      # national insurance
]
_PAYSLIP_MIN_KEYWORD_HITS = 3  # at least 3 must match


def is_valid_payslip(text: str) -> bool:
    """
    Return True if *text* contains at least _PAYSLIP_MIN_KEYWORD_HITS distinct
    Hebrew payroll keywords, False otherwise.

    Called after OCR text is obtained.  A False result causes parse_with_ocr()
    to return error_code='INVALID_DOCUMENT' immediately without further processing.
    """
    hits = sum(1 for pat in _PAYSLIP_KEYWORD_PATTERNS if pat.search(text))
    return hits >= _PAYSLIP_MIN_KEYWORD_HITS


# ---------------------------------------------------------------------------
# Regex patterns per field
# Each entry: field_name → list of (pattern_str, confidence_tier)
# Listed most-specific first; first tier with a match wins.
# ---------------------------------------------------------------------------

FIELD_PATTERNS: dict[str, list[tuple[str, float]]] = {
    "net_pay": [
        (r'נטו\s+לתשלום[:\s]*₪?\s*([\d,]+\.?\d*)',    CONFIDENCE_EXACT),
        (r'סה["\u05d4]\u05db\s+לתשלום[:\s]*₪?\s*([\d,]+\.?\d*)', CONFIDENCE_EXACT),
        (r'נטו[:\s]+₪?\s*([\d,]+\.?\d*)',              CONFIDENCE_AMBIGUOUS),
        (r'לתשלום[:\s]*₪?\s*([\d,]+\.?\d*)',           CONFIDENCE_AMBIGUOUS),
        # Phase 11: additional synonyms
        (r'שכר\s+נטו[:\s]*₪?\s*([\d,]+\.?\d*)',       CONFIDENCE_EXACT),    # שכר נטו
        (r'נטו\s+בנק[:\s]*₪?\s*([\d,]+\.?\d*)',        CONFIDENCE_EXACT),    # נטו בנק
        (r'סכום\s+ב?בנק[:\s]*₪?\s*([\d,]+\.?\d*)',     CONFIDENCE_EXACT),    # סכום בבנק / סכום בנק
    ],
    "gross_pay": [
        (r'ברוטו\s+לצורך\s+מס[:\s]*₪?\s*([\d,]+\.?\d*)', CONFIDENCE_EXACT),
        (r'ברוטו\s+למס\s+הכנסה[:\s]*₪?\s*([\d,]+\.?\d*)', CONFIDENCE_EXACT),  # Phase 8.1: Hilan layout
        (r'סה["\u05d4]\u05db\s+ברוטו[:\s]*₪?\s*([\d,]+\.?\d*)',  CONFIDENCE_EXACT),
        (r'ברוטו[:\s]+₪?\s*([\d,]+\.?\d*)',               CONFIDENCE_AMBIGUOUS),
        (r'ברוטו\s+([\d,]+\.?\d*)',                        CONFIDENCE_AMBIGUOUS),
        # Phase 11: additional synonyms
        (r'סה["\u05d4]\u05db\s+תשלומים[:\s]*₪?\s*([\d,]+\.?\d*)', CONFIDENCE_EXACT),   # סה"כ תשלומים
        (r'סך\s+(?:כל\s+)?התשלומים[:\s]*₪?\s*([\d,]+\.?\d*)',      CONFIDENCE_EXACT),   # סך כל התשלומים
        (r'סך\s+הכל\s+שכר[:\s]*₪?\s*([\d,]+\.?\d*)',               CONFIDENCE_EXACT),   # סך הכל שכר
    ],
    "income_tax": [
        (r'מס\s+הכנסה[:\s]*₪?\s*([\d,]+\.?\d*)',       CONFIDENCE_EXACT),
        (r'מ["\u05d4]\.?ה[:\s]+₪?\s*([\d,]+\.?\d*)',   CONFIDENCE_AMBIGUOUS),
    ],
    "national_insurance": [
        (r'ביטוח\s+לאומי[:\s]*₪?\s*([\d,]+\.?\d*)',    CONFIDENCE_EXACT),
        (r'בטוח\s+לאומי[:\s]*₪?\s*([\d,]+\.?\d*)',     CONFIDENCE_EXACT),   # common typo
        (r'ב["\u05d4]\.?ל[:\s]+₪?\s*([\d,]+\.?\d*)',   CONFIDENCE_AMBIGUOUS),
    ],
    "health_tax": [
        (r'מס\s+בריאות[:\s]*₪?\s*([\d,]+\.?\d*)',      CONFIDENCE_EXACT),
        (r'ביטוח\s+בריאות[:\s]*₪?\s*([\d,]+\.?\d*)',   CONFIDENCE_EXACT),
    ],
    "tax_credits": [
        (r'נקודות\s+זיכוי[:\s]*([\d]+\.?\d*)',          CONFIDENCE_EXACT),
        (r'נקודות\s+ציכוי[:\s]*([\d]+\.?\d*)',          CONFIDENCE_EXACT),   # Phase 8.1: Hilan ציכוי variant
        (r'נ["\u05d4]\.?ז[:\s]+([\d]+\.?\d*)',          CONFIDENCE_AMBIGUOUS),
        (r'זיכוי\s+מס[:\s]*([\d]+\.?\d*)',              CONFIDENCE_AMBIGUOUS),
    ],
    # pay_month uses a separate function (two capture groups)
}

# Base OCR patterns: same regexes but confidence × OCR_CONFIDENCE_SCALE
_FIELD_PATTERNS_OCR_BASE: dict[str, list[tuple[str, float]]] = {
    field: [(pat, conf * OCR_CONFIDENCE_SCALE) for pat, conf in pats]
    for field, pats in FIELD_PATTERNS.items()
}

# OCR-hardened extra patterns appended after the base set.
# These tolerate common Tesseract deformations: missing spaces, letter swaps,
# garbled final letters, and RTL word-order variations.
_CONF_OCR_EXACT     = CONFIDENCE_EXACT     * OCR_CONFIDENCE_SCALE   # ≈ 0.638
_CONF_OCR_AMBIGUOUS = CONFIDENCE_AMBIGUOUS * OCR_CONFIDENCE_SCALE   # ≈ 0.450
_CONF_OCR_LOW       = 0.45  # heuristic / positional fallback

_FIELD_PATTERNS_OCR_EXTRA: dict[str, list[tuple[str, float]]] = {
    "net_pay": [
        # Tolerate missing/merged spaces in "נטו לתשלום": "נטלתשלום", "נטו  לתשלום", etc.
        (r'נטו\s*ל?\s*ת\s*ש?לו?ם[:\s\-]*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # Variant: "שכר נטו: AMOUNT" or "נטו לשכר"
        (r'שכר\s*נטו\s*[:\-]?\s*([0-9][0-9,\.]+)',          _CONF_OCR_EXACT),
        # Heuristic: a number with exactly 2 decimal places appearing on a line that
        # also contains "שכר" (net salary line) when no better match exists.
        # Pattern: money-like number (digits, comma, dot) followed by whitespace and "שכר"
        # OR "שכר" appearing before the number on the same line.
        (r'([0-9][0-9,]+\.[0-9]{2})\s[^\n]*שכר',            _CONF_OCR_LOW),
        (r'שכר[^\n]{0,30}([0-9][0-9,]+\.[0-9]{2})',          _CONF_OCR_LOW),
        # "נטו" followed by amount on same or next line (OCR sometimes inserts newlines)
        (r'נטו[^\n]{0,40}([0-9][0-9,]+\.?\d*)',              _CONF_OCR_AMBIGUOUS),
        # Phase 11: additional synonyms (OCR-tolerant)
        (r'נטו\s*בנק\s*[:\-]?\s*([0-9][0-9,\.]+)',           _CONF_OCR_EXACT),    # נטו בנק
        (r'סכום\s*ב?בנק\s*[:\-]?\s*([0-9][0-9,\.]+)',        _CONF_OCR_EXACT),    # סכום בבנק / סכום בנק
    ],
    "gross_pay": [
        # "ברוטו למס הכנסה" or "ברוטו למס" (no strict punctuation)
        (r'ברוטו\s+למס\s*הכנסה?\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # "ברוטו לצורך" with garbled מס
        (r'ברוטו\s+לצורך\s*\S{0,4}\s*([0-9][0-9,\.]+)',        _CONF_OCR_AMBIGUOUS),
        # Phase 11: additional synonyms (OCR-tolerant)
        (r'סה["\u05d4]?כ\s*תשלומ\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)',  _CONF_OCR_EXACT),   # סה"כ תשלומים
        (r'סך\s*כל\s*התשלומ\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)',        _CONF_OCR_EXACT),   # סך כל התשלומים
        (r'סך\s*הכל\s*שכר\s*[:\-]?\s*([0-9][0-9,\.]+)',                 _CONF_OCR_EXACT),   # סך הכל שכר
    ],
    "income_tax": [
        # "מס הכנסה" with last letter garbled ("מס הכנסו", "מס הכנסת", etc.)
        (r'מס\s+הכנס\S{0,2}[:\s\-]*([0-9][0-9,\.]+)',  _CONF_OCR_EXACT),
        # "מס הכנ" — partial word match
        (r'מס\s+הכנ\S{0,4}[:\s\-]*([0-9][0-9,\.]+)',   _CONF_OCR_AMBIGUOUS),
    ],
    "national_insurance": [
        # "ביטוח לאומי" with partial OCR
        (r'ביטו\S{0,3}\s+לאומ[יה]?\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
    ],
    "health_tax": [
        # "מס בריאות" with partial OCR
        (r'מס\s+בריאו\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
    ],
    "tax_credits": [
        # "נקודות זיכוי" with spaces/typos — label before number
        (r'נקודו\S{0,3}\s+זיכו\S{0,2}\s*[:\-]?\s*([0-9]+\.?[0-9]*)', _CONF_OCR_EXACT),
        # Phase 8.1: Tesseract confuses ז (zayin) with צ (tsadi) on Hilan fonts → "נקודות ציכוי"
        (r'נקודו\S{0,3}\s+ציכו\S{0,2}\s*[:\-]?\s*([0-9]+\.?[0-9]*)', _CONF_OCR_EXACT),
        # "נקודות" with number before (RTL layout: "2.25 ... נקודות")
        (r'([0-9]+\.[0-9]{2})\s+[^\n]{0,30}נקודו\S{0,3}', _CONF_OCR_AMBIGUOUS),
    ],
    # -----------------------------------------------------------------------
    # New summary-box fields (OCR only — not present in text-layer PDFs)
    # -----------------------------------------------------------------------
    "total_payments_other": [
        # סה"כ תשלומים אחרים — tolerates smart/straight quote and spacing
        (r'סה["\u05d4\u201c\u201d]?כ\s*תשלומ\S{0,3}\s*אחר\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        (r'סהכ\s*תשלומ\S{0,3}\s*אחר\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_AMBIGUOUS),
        # Reversed order: "תשלומים 6,223.70" (OCR reads RTL — number in middle of line)
        # Match lines containing "תשלומ" with a decimal amount on same line
        (r'תשלומ\S{0,3}\s+([0-9][0-9,]+\.[0-9]{2})', _CONF_OCR_AMBIGUOUS),
    ],
    "mandatory_taxes_total": [
        # ניכויי חובה-מסים / ניכויי חובה מסים (with various separators and OCR garble)
        (r'ניכוי\S{0,2}\s+חובה\s*[-–\.\-]\s*מסי\S{0,2}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        (r'ניכוי\S{0,2}\s+חובה\s+מסי\S{0,2}\s*[:\-]?\s*([0-9][0-9,\.]+)',            _CONF_OCR_EXACT),
        # OCR garbles חובה → תחובה (extra ת) — "ניכויי תחובה.- מסים"
        (r'ניכוי\S{0,2}\s+\S{0,2}חובה\S{0,2}\s*[-–\.\-]\s*מסי\S{0,2}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_AMBIGUOUS),
        (r'חובה\S{0,2}\s*[-–\.\-]\s*מסי\S{0,2}\s*[:\-]?\s*([0-9][0-9,\.]+)',          _CONF_OCR_AMBIGUOUS),
    ],
    "provident_funds_deduction": [
        # ניכוי קופות גמל — normal order
        (r'ניכוי\s+קופו\S{0,3}\s+גמל\S{0,2}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # OCR variant: "ניכויים לקופות" with number BEFORE label (RTL)
        # e.g. "20 01 | 519.50( ניכויים לקופות. S/N."
        (r'([0-9][0-9,]+\.[0-9]{2})\s*\(?[^\n]{0,15}ניכוי\S{0,3}\s+לקופו\S{0,3}', _CONF_OCR_AMBIGUOUS),
        # Phase 11: additional pension/provident fund synonyms
        (r'פנסיה\s*מקיפ\S{0,2}\s*[:\-]?\s*([0-9][0-9,\.]+)',               _CONF_OCR_EXACT),     # פנסיה מקיפה
        (r'קופת\s*גמל\s*בהסכ\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)',          _CONF_OCR_EXACT),     # קופת גמל בהסכם
        (r'קופו\S{0,3}\s+גמל\s+וקרנו\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_AMBIGUOUS), # קופות גמל וקרנות
    ],
    "other_deductions": [
        # ניכויים שונים
        (r'ניכוי\S{0,3}\s+שונ\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
    ],
    "net_salary": [
        # שכר נטו (dedicated summary-box label, not a heuristic)
        (r'שכר\s+נטו\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # Reversed order (number before label, RTL layout artifact)
        (r'([0-9][0-9,]+\.[0-9]{2})\s+[^\n]{0,30}שכר\s+נטו', _CONF_OCR_EXACT),
        # RTL marker variant: "5,370.20 "yg ‏שכר" (OCR garbles leading chars before שכר)
        (r'([0-9][0-9,]+\.[0-9]{2})[^\n]{0,15}[\u200e\u200f]?\s*שכר(?!\s+\S)', _CONF_OCR_AMBIGUOUS),
    ],
    "net_to_pay": [
        # נטו לתשלום / נטלתשלום (summary box — distinct from line-level net_pay)
        (r'נטו\s*ל\s*תשלום\s*[:\-]?\s*([0-9][0-9,\.]+)',   _CONF_OCR_EXACT),
        (r'נט[וו]?\s*לת\s*שלום\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        (r'נטלתשלום\s*[:\-]?\s*([0-9][0-9,\.]+)',            _CONF_OCR_EXACT),
    ],
    "gross_taxable": [
        # ברוטו למס הכנסה (summary-box version — also used for gross_pay above,
        # but captured here at full confidence as a dedicated field)
        # Normal order: label then number
        (r'ברוטו\s+למס\s*הכנסה?\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # "ברוטו למס רגיל" (OCR variant) — normal order
        (r'ברוטו\s+למס\s*רגיל\S{0,3}\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # "ברוטן למס הכנסה" (garbled ו→ן) — normal order
        (r'ברוט\S\s+למס\s*הכנסה?\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # Reversed order: number appears BEFORE label (RTL OCR layout artifact)
        # "6,463.00 ... ברוטן למס הכנסה" — allow garbage chars between number and label
        (r'([0-9][0-9,\.]+)[^\n]{0,40}ברוט[וּן]\s+למס\s*הכנסה?', _CONF_OCR_EXACT),
        (r'([0-9][0-9,\.]+)[^\n]{0,40}ברוט\S\s+למס\s*רגיל', _CONF_OCR_EXACT),
    ],
    "gross_ni": [
        # ברוטו לב.ל / ברוטו לביטוח לאומי — normal order
        (r'ברוטו\s+ל(?:ב\.?ל|ביטו\S{0,4}\s+לאומ\S{0,3})\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        (r'ברוטו\s+לב\.?ל\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # Reversed order (number before label)
        (r'([0-9][0-9,\.]+)[:\s]*\S*\s*ברוטו\s+לב\.?לאומ\S{0,3}', _CONF_OCR_EXACT),
    ],
}

# Merged OCR patterns: base (exact copies × 0.75) + extra (typo-tolerant)
FIELD_PATTERNS_OCR: dict[str, list[tuple[str, float]]] = {
    field: _FIELD_PATTERNS_OCR_BASE.get(field, []) + _FIELD_PATTERNS_OCR_EXTRA.get(field, [])
    for field in set(list(_FIELD_PATTERNS_OCR_BASE.keys()) + list(_FIELD_PATTERNS_OCR_EXTRA.keys()))
}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _parse_number(raw: str) -> float | None:
    """Convert Israeli-formatted number string to float. '12,500.50' → 12500.5"""
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_text_from_pdf(file_path: str | Path) -> dict[int, str]:
    """
    Open a PDF with pdfplumber and extract text per page.
    Returns {page_index: text}. Returns {} on any error (corrupt file, not a PDF, etc.).
    pdfplumber handles Hebrew logical text order internally.
    """
    import pdfplumber

    pages_text: dict[int, str] = {}
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
                pages_text[i] = text
    except Exception as exc:
        logger.warning("pdfplumber failed to open %s: %s", file_path, exc)
    return pages_text


def has_text_layer(pages_text: dict[int, str]) -> bool:
    """
    Return True only if the extracted text is both long enough AND contains
    real Hebrew Unicode characters.

    Phase 8.1: Hilan and some other Israeli payroll vendors embed Hebrew using
    proprietary font encodings (custom CMap). pdfplumber extracts garbled
    Latin-range characters instead of Hebrew Unicode — this function must detect
    that case and return False so the OCR upgrade path is triggered instead of
    returning an empty payload on the text-layer path.

    Checks:
      1. Total stripped chars >= TEXT_MIN_CHARS (original check).
      2. At least _HEBREW_UNICODE_MIN characters fall in the Hebrew Unicode block
         (U+0590–U+05FF) or Hebrew presentation forms (U+FB1D–U+FB4F).
    """
    full = "".join(pages_text.values())
    stripped = full.strip()
    if len(stripped) < TEXT_MIN_CHARS:
        return False
    # Require genuine Hebrew Unicode — proprietary-encoded PDFs fail this check
    # even when they contain thousands of Latin-range substitution characters.
    hebrew_chars = sum(
        1 for c in stripped
        if '\u0590' <= c <= '\u05FF' or '\uFB1D' <= c <= '\uFB4F'
    )
    return hebrew_chars >= _HEBREW_UNICODE_MIN


def extract_field(
    pages_text: dict[int, str],
    field_name: str,
    patterns: list[tuple[str, float]],
) -> ExtractedField | None:
    """
    Try each (pattern, confidence) tier in order across all pages.
    First tier that produces at least one match wins.
    Applies consistency boost: if the same numeric value appears 2+ times, confidence → CONFIDENCE_BOOST.
    Returns the first match from the winning tier.
    """
    for pattern_str, base_confidence in patterns:
        compiled = re.compile(pattern_str, re.UNICODE | re.IGNORECASE)
        all_matches: list[tuple[float, str, int]] = []

        for page_idx, text in pages_text.items():
            for m in compiled.finditer(text):
                raw_match = m.group(0).strip()
                value_str = m.group(1)
                val = _parse_number(value_str)
                if val is not None and val > 0:
                    all_matches.append((val, raw_match, page_idx))

        if not all_matches:
            continue  # try next tier

        # Consistency boost: same value found 2+ times
        value_counts: dict[float, int] = {}
        for val, _, _ in all_matches:
            value_counts[val] = value_counts.get(val, 0) + 1

        best_val, best_raw, best_page = all_matches[0]
        confidence = base_confidence
        if value_counts.get(best_val, 0) >= 2:
            confidence = max(confidence, CONFIDENCE_BOOST)

        return ExtractedField(
            value=best_val,
            raw_text=best_raw,
            confidence=confidence,
            source_page=best_page,
        )

    return None


# Tokens that indicate a *table row* rather than a summary-box deduction line.
# If a matched line also contains any of these, the match is considered a false positive.
_TABLE_ROW_TOKENS: list[str] = [
    "שעות",      # hours column
    "הפסקה",     # break / deduction column in earnings table
    "תעריף",     # rate column
    "ימים",      # days column
    "קוד",       # code column
    "מחיר",      # price column
    "יחידות",    # units column
]

# income_tax reject list: table-row tokens PLUS "ברוטו" (to avoid "ברוטו למס הכנסה" false positive)
_INCOME_TAX_REJECT: list[str] = _TABLE_ROW_TOKENS + ["ברוטו"]


def extract_field_filtered(
    pages_text: dict[int, str],
    field_name: str,
    patterns: list[tuple[str, float]],
    reject_tokens: list[str] | None = None,
) -> "ExtractedField | None":
    """
    Like extract_field(), but additionally rejects any match whose source line
    also contains one of the tokens in *reject_tokens* (table-row false-positive guard).

    Only relevant when reject_tokens is not None/empty.
    Falls back to plain extract_field() when no filtering is needed.
    """
    if not reject_tokens:
        return extract_field(pages_text, field_name, patterns)

    for pattern_str, base_confidence in patterns:
        compiled = re.compile(pattern_str, re.UNICODE | re.IGNORECASE)
        all_matches: list[tuple[float, str, int]] = []

        for page_idx, text in pages_text.items():
            for m in compiled.finditer(text):
                # Determine the line that contains this match
                match_start = m.start()
                # Find the line boundaries
                line_start = text.rfind('\n', 0, match_start) + 1
                line_end_idx = text.find('\n', match_start)
                line = text[line_start: line_end_idx if line_end_idx != -1 else len(text)]

                # Skip if this line looks like a table row
                if any(tok in line for tok in reject_tokens):
                    continue

                raw_match = m.group(0).strip()
                value_str = m.group(1)
                val = _parse_number(value_str)
                if val is not None and val > 0:
                    all_matches.append((val, raw_match, page_idx))

        if not all_matches:
            continue  # try next tier

        # Consistency boost: same value found 2+ times
        value_counts: dict[float, int] = {}
        for val, _, _ in all_matches:
            value_counts[val] = value_counts.get(val, 0) + 1

        best_val, best_raw, best_page = all_matches[0]
        confidence = base_confidence
        if value_counts.get(best_val, 0) >= 2:
            confidence = max(confidence, CONFIDENCE_BOOST)

        return ExtractedField(
            value=best_val,
            raw_text=best_raw,
            confidence=confidence,
            source_page=best_page,
        )

    return None


# ---------------------------------------------------------------------------
# OCR line-split helper: find an amount near a label across adjacent lines
# ---------------------------------------------------------------------------

# Regex that matches currency-like numbers: optional thousands separators, 2 decimals.
# Also matches numbers in parentheses: (334.00).
# Group 1 captures the raw digit string (without parentheses).
_MONEY_RE = re.compile(
    r'\(?([0-9]{1,3}(?:,\d{3})*\.\d{2})\)?',
    re.UNICODE,
)

# Regex that matches ONLY parenthesized amounts — deduction indicator.
# e.g. "(334.00)" or ")334.00(" (RTL mirrored parentheses from OCR).
_PAREN_MONEY_RE = re.compile(
    r'[(\)]([ 0-9]{1,3}(?:,\d{3})*\.\d{2})[)\(]',
    re.UNICODE,
)

# Confidence tiers for find_amount_near_label (OCR-scaled).
_NEAR_CONF_SAME_LINE  = 0.75   # amount on the label line itself
_NEAR_CONF_ADJACENT   = 0.65   # ±1 line
_NEAR_CONF_NEAR       = 0.55   # ±2–3 lines

# Reject tokens: lines containing these are skipped during nearby-scan.
# Used to avoid picking up gross/employer amounts near a deduction label.
_REJECT_NEAR_TOKENS: list[re.Pattern[str]] = [
    re.compile(r'ברוטו\s+ל', re.UNICODE),      # ברוטו לב.לאומי / ברוטו למס
    re.compile(r'ב\.לאומי|ב\.ל\b', re.UNICODE), # ביטוח לאומי abbreviations
]


def find_amount_near_label(
    text: str,
    label_regex: "re.Pattern[str]",
    max_lines_delta: int = 3,
    source_page: int = 0,
    skip_same_line: bool = False,
    prefer_paren: bool = False,
    scan_signs: tuple[int, ...] = (+1, -1),
    reject_tokens: "list[re.Pattern[str]] | None" = None,
) -> "ExtractedField | None":
    """
    OCR-only helper for fields where Tesseract splits a label and its amount
    across different lines (RTL layout artifact).

    Parameters
    ----------
    skip_same_line : bool
        If True, skip the same-line amount check.  Useful when the label line
        also contains a different amount (e.g. employer share).
    prefer_paren : bool
        If True, first try to find a *parenthesized* amount in the scan window
        (deduction indicator).  Only falls back to plain amounts if none found.
    scan_signs : tuple of ints
        Order in which to probe (+1, -1) during the proximity scan.
        Default: (+1, -1) — forward-first (below the label).
    reject_tokens : list of compiled patterns, optional
        If set, candidate lines matching any of these are skipped.

    Algorithm:
      1. Split the page text into lines.
      2. Find the first line matching *label_regex*.
      3. If not skip_same_line, check that line for a money number.
         → confidence = _NEAR_CONF_SAME_LINE
      4. If prefer_paren, do a first pass over the scan window looking only for
         parenthesized amounts (_PAREN_MONEY_RE), then a second pass for any.
      5. Otherwise scan normally at delta ±1, ±2, … up to *max_lines_delta*.
         → confidence = _NEAR_CONF_ADJACENT  (delta == 1)
                        _NEAR_CONF_NEAR       (delta == 2 or 3)
      6. Returns an ExtractedField, or None if no amount found.
    """
    lines = text.splitlines()
    label_line_idx: int | None = None

    for i, line in enumerate(lines):
        if label_regex.search(line):
            label_line_idx = i
            break

    if label_line_idx is None:
        return None  # label not found in this page

    label_line = lines[label_line_idx]
    total_lines = len(lines)

    def _is_rejected(line: str) -> bool:
        if not reject_tokens:
            return False
        return any(p.search(line) for p in reject_tokens)

    # --- Same-line check ---
    if not skip_same_line:
        m = _MONEY_RE.search(label_line)
        if m:
            val = _parse_number(m.group(1))
            if val is not None and val > 0:
                return ExtractedField(
                    value=val,
                    raw_text=label_line.strip()[:120],
                    confidence=_NEAR_CONF_SAME_LINE,
                    source_page=source_page,
                )

    # --- Proximity scan ---
    def _scan(use_paren: bool) -> "ExtractedField | None":
        pattern = _PAREN_MONEY_RE if use_paren else _MONEY_RE
        for delta in range(1, max_lines_delta + 1):
            for sign in scan_signs:
                idx = label_line_idx + sign * delta  # type: ignore[operator]
                if idx < 0 or idx >= total_lines:
                    continue
                candidate_line = lines[idx]
                if _is_rejected(candidate_line):
                    continue
                m = pattern.search(candidate_line)
                if m:
                    val = _parse_number(m.group(1))
                    if val is not None and val > 0:
                        conf = _NEAR_CONF_ADJACENT if delta == 1 else _NEAR_CONF_NEAR
                        snippet = f"{label_line.strip()} | {candidate_line.strip()}"
                        return ExtractedField(
                            value=val,
                            raw_text=snippet[:120],
                            confidence=conf,
                            source_page=source_page,
                        )
        return None

    if prefer_paren:
        result = _scan(use_paren=True)
        if result is not None:
            return result
        # Fallback to any amount
        return _scan(use_paren=False)

    return _scan(use_paren=False)


def find_amount_near_label_pages(
    pages_text: dict[int, str],
    label_regex: "re.Pattern[str]",
    max_lines_delta: int = 3,
    **kwargs,
) -> "ExtractedField | None":
    """
    Multi-page wrapper around find_amount_near_label().
    Tries each page in order; returns the first result found.
    Extra keyword args are forwarded to find_amount_near_label().
    """
    for page_idx, text in pages_text.items():
        result = find_amount_near_label(
            text, label_regex,
            max_lines_delta=max_lines_delta,
            source_page=page_idx,
            **kwargs,
        )
        if result is not None:
            return result
    return None


# Pre-compiled label regexes for the three OCR split-line fields.
_LABEL_RE_MANDATORY_TAXES = re.compile(
    r'ניכויי\S{0,2}\s+\S{0,2}חובה\S{0,2}\s*[-–\.]*\s*מסי\S{0,2}',
    re.UNICODE | re.IGNORECASE,
)
_LABEL_RE_OTHER_DEDUCTIONS = re.compile(
    r'ניכויים?\s*שונ\S{0,3}|\bשונ[יים]{0,3}\b',
    re.UNICODE | re.IGNORECASE,
)
_LABEL_RE_NET_TO_PAY = re.compile(
    r'נטו\s*ל\s*ת\s*שלום|נטלתשלום',
    re.UNICODE | re.IGNORECASE,
)

# Label regex for net_salary summary box (used as fallback for net_to_pay).
_LABEL_RE_NET_SALARY_BOX = re.compile(
    r'שכר\s+נטו',
    re.UNICODE | re.IGNORECASE,
)


def extract_mandatory_taxes_ocr(pages_text: dict[int, str],
                                 direct_field: "ExtractedField | None") -> "ExtractedField | None":
    """
    Extraction strategy for mandatory_taxes_total in OCR mode.

    The label 'ניכויי חובה - מסים' often appears with the EMPLOYER contribution
    on the same line (e.g. '...מסים בייל מעסיק 291.50').  The actual employee
    deduction is on the *next* line, frequently parenthesized: ')334.00('.

    Direct regex patterns capture the wrong value (employer share) on the label
    line, so we always skip direct_field and rely on the proximity search.

    Strategy:
      - Search forward (skip same-line amount) preferring parenthesized amounts
        in the next 3 lines, rejecting 'ברוטו ל' lines.
    """
    # Intentionally ignore direct_field: the patterns match the employer amount
    # on the label line (e.g. 291.50), not the employee deduction (e.g. 334.00).
    _ = direct_field  # unused

    # Forward-only, skip same-line amount, prefer parenthesized amounts,
    # reject gross/bi-lumi lines that might interfere.
    return find_amount_near_label_pages(
        pages_text,
        _LABEL_RE_MANDATORY_TAXES,
        max_lines_delta=3,
        skip_same_line=True,
        prefer_paren=True,
        scan_signs=(+1,),          # forward-only: amount is below label in OCR layout
        reject_tokens=_REJECT_NEAR_TOKENS,
    )


_ODED_NIKUIM_RE = re.compile(r'ניכויים', re.UNICODE)
# Exclude: provident funds, mandatory, grand total lines (סה), garbled accumulation rows
_ODED_EXCLUDE_RE = re.compile(r'קופות|חובה|לקופ|והפרש|סה|שונים', re.UNICODE)
# Summary-box anchor labels used for other_deductions fallback scan.
# The net-salary line contains שכר and a money amount (5,370.20);
# in RTL-garbled OCR, נטו is often dropped or separated from שכר.
_ODED_NET_SALARY_ANCHOR = re.compile(r'שכר', re.UNICODE)
_ODED_NET_TO_PAY_ANCHOR = re.compile(r'נטלתשלום|נטו\s*לת', re.UNICODE)
# Colon-as-decimal pattern: OCR sometimes reads "16.00" as "16:00".
# Only match small amounts (< 500) to avoid time-of-day false positives like "19:50".
_TIME_AS_AMOUNT_RE = re.compile(r'\b(\d{1,3}):(\d{2})\b', re.UNICODE)


def extract_other_deductions_ocr(pages_text: dict[int, str],
                                  direct_field: "ExtractedField | None") -> "ExtractedField | None":
    """
    Extraction strategy for other_deductions in OCR mode.

    OCR frequently garbles 'שונים' to non-Hebrew characters (e.g. 'Dow'), so
    _LABEL_RE_OTHER_DEDUCTIONS often matches only the total/accumulation line
    ('סה\"כ ניכויים שונים.') rather than the summary-box label.  The correct
    value is usually on a bare 'ניכויים <garbage> <amount>' line.

    Additionally, OCR sometimes reads monetary amounts with a colon instead of
    a period (e.g. '16:00' for '16.00'), especially in the payslip summary box.

    Strategy:
      1. Accept direct_field if present.
      2. Scan all lines for bare 'ניכויים' + money on the same line,
         excluding provident-fund, mandatory, total ('סה'), and clearly-שונים lines.
      3. Colon-as-decimal fallback: find lines between the 'שכר נטו' and
         'נטלתשלום' summary-box anchors; extract 'HH:MM'-style values that
         are plausible small deduction amounts (< 500 ₪).
    """
    if direct_field is not None:
        return direct_field

    for page_idx, text in pages_text.items():
        lines = text.splitlines()

        # --- Strategy 2: bare ניכויים + money ---
        for line in lines:
            if _ODED_NIKUIM_RE.search(line) and not _ODED_EXCLUDE_RE.search(line):
                m = _MONEY_RE.search(line)
                if m:
                    val = _parse_number(m.group(1))
                    if val is not None and val > 0:
                        return ExtractedField(
                            value=val,
                            raw_text=line.strip()[:120],
                            confidence=_NEAR_CONF_ADJACENT,
                            source_page=page_idx,
                        )

        # --- Strategy 3: colon-as-decimal between summary-box anchors ---
        # Find the window [שכר <with money amount> ... נטלתשלום] in line order.
        # The net-salary summary box line contains both 'שכר' AND a money amount.
        net_sal_idx: int | None = None
        ntp_idx: int | None = None
        for i, line in enumerate(lines):
            if (_ODED_NET_SALARY_ANCHOR.search(line)
                    and _MONEY_RE.search(line)
                    and net_sal_idx is None):
                net_sal_idx = i
            if _ODED_NET_TO_PAY_ANCHOR.search(line) and ntp_idx is None:
                ntp_idx = i
        if net_sal_idx is not None and ntp_idx is not None and net_sal_idx < ntp_idx:
            # Scan lines between the two anchors for colon-as-decimal patterns.
            for line in lines[net_sal_idx + 1 : ntp_idx]:
                m = _TIME_AS_AMOUNT_RE.search(line)
                if m:
                    hours, mins = int(m.group(1)), int(m.group(2))
                    # Only treat as a monetary amount if it looks plausible:
                    # small value (< 500), and mins matches a common cent value.
                    if hours < 500 and mins in (0, 5, 10, 13, 15, 20, 25, 30, 50, 75, 90, 95, 99):
                        val_str = f"{hours}.{mins:02d}"
                        val = _parse_number(val_str)
                        if val is not None and val > 0:
                            return ExtractedField(
                                value=val,
                                raw_text=line.strip()[:120],
                                # Lower confidence — colon-as-decimal is heuristic
                                confidence=_NEAR_CONF_NEAR,
                                source_page=page_idx,
                            )

    return None


_NTP_REJECT_RE = re.compile(
    r'ניכויים|ניכוי|קופות|לאומי|מס',
    re.UNICODE,
)  # reject lines that are likely deduction entries, not the net pay amount


def extract_net_to_pay_ocr(
    pages_text: dict[int, str],
    direct_field: "ExtractedField | None",
    net_salary_field: "ExtractedField | None",
    other_deductions_field: "ExtractedField | None" = None,
) -> "tuple[ExtractedField | None, list[str]]":
    """
    Extraction strategy for net_to_pay in OCR mode.

    'נטו לתשלום' is a summary-box label.  In Tesseract's column-scan order the
    amount for it (same as net_salary) appears several lines *before* the label,
    but adjacent lines often contain other deduction amounts (e.g. 16.00 for
    ניכויים שונים) that would be picked up first.

    Returns (ExtractedField | None, extra_integrity_notes).
    extra_integrity_notes is non-empty when the value was computed rather than
    read directly, so callers can append it to the integrity_notes list.

    Strategy:
      1. Accept direct_field if present.
      2. Proximity search: scan backward, rejecting deduction-like lines,
         with max_lines_delta=5.
      3. If other_deductions is known (> 0) and net_salary is known:
         compute net_to_pay = round(net_salary - other_deductions, 2) and attach
         an integrity note to signal this was calculated, not read.
      4. Final fallback (other_deductions is None or 0):
         use net_salary_field unchanged (they represent the same figure when there
         are no additional post-salary deductions).
    """
    if direct_field is not None:
        return direct_field, []

    net_sal_val = net_salary_field.value if net_salary_field else None
    oded_val = other_deductions_field.value if other_deductions_field else None

    # Backward-first scan: amount is above the label in OCR top-to-bottom order.
    # Reject deduction lines so we don't pick up ניכויים / ניכוי amounts.
    result = find_amount_near_label_pages(
        pages_text,
        _LABEL_RE_NET_TO_PAY,
        max_lines_delta=5,
        scan_signs=(-1, +1),          # backward-first
        reject_tokens=[_NTP_REJECT_RE],
    )

    # If the proximity search found a value that equals net_salary AND we know
    # other_deductions > 0, the scan picked up the net_salary line instead of
    # the net_to_pay amount.  Prefer the computed value in that case.
    if (result is not None
            and net_sal_val is not None
            and oded_val is not None
            and oded_val > 0
            and abs(result.value - net_sal_val) < 0.01):
        # The scan returned net_salary, not net_to_pay — fall through to compute.
        result = None

    if result is not None:
        return result, []

    # --- Computed fallback ---
    if net_sal_val is not None and oded_val is not None and oded_val > 0:
        # net_to_pay = net_salary minus other_deductions
        computed_val = round(net_sal_val - oded_val, 2)
        computed_field = ExtractedField(
            value=computed_val,
            raw_text=f"חישוב: {net_sal_val} - {oded_val} = {computed_val}",
            confidence=0.50,          # lower than OCR read — calculated not observed
            source_page=net_salary_field.source_page,  # type: ignore[union-attr]
        )
        notes = ["נטו לתשלום חושב כנטו פחות ניכויים שונים"]
        return computed_field, notes

    # No other_deductions — net_to_pay equals net_salary for this payslip.
    return net_salary_field, []


def extract_ocr_near_label(
    pages_text: dict[int, str],
    label_re: "re.Pattern[str]",
    direct_field: "ExtractedField | None",
    max_lines_delta: int = 3,
) -> "ExtractedField | None":
    """
    Generic helper: returns *direct_field* if not None, otherwise falls back
    to find_amount_near_label_pages() with default settings.
    """
    if direct_field is not None:
        return direct_field
    return find_amount_near_label_pages(pages_text, label_re, max_lines_delta)


def extract_pay_month(pages_text: dict[int, str]) -> tuple[str, float] | None:
    """
    Search for pay-month patterns. Returns ("YYYY-MM", confidence) or None.
    Handles 2-digit and 4-digit year variants.
    """
    patterns: list[tuple[str, float]] = [
        (
            r'(?:חודש\s+שכר|תקופת\s+שכר|לחודש|חודש)[:\s]*(\d{1,2})[/\-\.](\d{2,4})',
            CONFIDENCE_EXACT,
        ),
        (r'(\d{2})[/\-\.](\d{4})', CONFIDENCE_AMBIGUOUS),
    ]

    for pattern_str, confidence in patterns:
        compiled = re.compile(pattern_str, re.UNICODE | re.IGNORECASE)
        for _page_idx, text in pages_text.items():
            m = compiled.search(text)
            if m:
                month_str = m.group(1)
                year_str = m.group(2)
                month = int(month_str)
                year = int(year_str)
                if year < 100:
                    year += 2000  # 25 → 2025
                if 1 <= month <= 12 and 2000 <= year <= 2100:
                    return (f"{year:04d}-{month:02d}", confidence)

    return None


# Hebrew month name → ISO month number.
# Each entry: (month_number, list_of_regex_fragments)
# Fragments are tried with re.search (UNICODE | IGNORECASE).
# The typo-tolerant prefix "[?יYy]נואר" covers OCR garbling of ינואר where
# the first letter י is read as '?' or a Latin 'Y'.
_HEB_MONTH_PATTERNS: list[tuple[int, str]] = [
    (1,  r'[?יYy]?נואר'),          # ינואר — '?' or missing first letter
    (2,  r'פברואר'),
    (3,  r'מרץ'),
    (4,  r'אפריל'),
    (5,  r'מאי'),
    (6,  r'יונ[יה]'),
    (7,  r'יול[יה]'),
    (8,  r'אוגוסט'),
    (9,  r'ספטמבר'),
    (10, r'אוקטובר'),
    (11, r'נובמבר'),
    (12, r'דצמבר'),
]


def extract_pay_month_ocr(pages_text: dict[int, str]) -> tuple[str, float] | None:
    """
    OCR-specific pay-month extractor.

    Strategy (tried in order, first hit wins):
      1. "תלוש שכר לחודש <MONTH_WORD> <YEAR>" — Hebrew month word + 4-digit year
      2. Any line containing a Hebrew month word adjacent to a 4-digit year
      3. Numeric formats: MM/YYYY, DD/MM/YYYY (delegate to the generic extractor)

    Tolerates OCR garbling of the first letter of ינואר (→ '?' or 'Y').
    Returns ("YYYY-MM", confidence) or None.
    """
    confidence_high = CONFIDENCE_EXACT * OCR_CONFIDENCE_SCALE    # ≈ 0.638
    confidence_low  = CONFIDENCE_AMBIGUOUS * OCR_CONFIDENCE_SCALE  # ≈ 0.450

    full_text = "\n".join(pages_text.values())

    # Strategy 1 & 2: Hebrew month word + year anywhere on same line
    for line in full_text.splitlines():
        for month_num, fragment in _HEB_MONTH_PATTERNS:
            if re.search(fragment, line, re.UNICODE):
                # Look for a 4-digit year on the same line
                year_match = re.search(r'(20[0-9]{2})', line)
                if year_match:
                    year = int(year_match.group(1))
                    if 2000 <= year <= 2100:
                        # Higher confidence if a "שכר" or "לחודש" context phrase is nearby
                        conf = confidence_high if re.search(
                            r'(?:שכר|לחודש|תלוש)', line, re.UNICODE
                        ) else confidence_low
                        return (f"{year:04d}-{month_num:02d}", conf)

    # Strategy 3: fall back to generic numeric extractor
    return extract_pay_month(pages_text)


def detect_provider(full_text: str) -> tuple[str | None, float]:
    """
    Scan the full text (all pages concatenated) for known provider strings.
    Case-insensitive substring match. Returns (provider_display_name, confidence) or (None, 0.0).
    """
    lower_text = full_text.lower()
    for display_name, search_strings, weight in _PROVIDER_PATTERNS:
        for term in search_strings:
            if term.lower() in lower_text:
                return (display_name, weight)
    return (None, 0.0)


# ---------------------------------------------------------------------------
# Phase 15: Educational insights bridge
# ---------------------------------------------------------------------------

def _build_insights(
    gross: float | None,
    net: float | None,
    income_tax: float | None,
    national_ins: float | None,
    health: float | None,
    pension_employee: float | None,
    credit_points: float | None,
    line_items: list,
) -> list:
    """
    Call generate_insights() from app.logic.insights and convert the plain
    dataclass results to Insight Pydantic schema objects for inclusion in
    ParsedSlipPayload.  Import is deferred to avoid circular imports.
    """
    try:
        from app.logic.insights import generate_insights as _gen_insights
        from app.models.schemas import Insight as _InsightSchema
        raw = _gen_insights(
            gross=gross,
            net=net,
            income_tax=income_tax,
            national_insurance=national_ins,
            health_insurance=health,
            pension_employee=pension_employee,
            credit_points=credit_points,
            line_items=line_items,
        )
        return [
            _InsightSchema(id=ins.id, kind=ins.kind, title=ins.title, body=ins.body)
            for ins in raw
        ]
    except Exception as exc:
        logger.warning("insights: failed to generate insights (%s)", exc)
        return []


# ---------------------------------------------------------------------------
# Integrity checks
# ---------------------------------------------------------------------------

def _run_integrity_checks(
    gross: float | None,
    net: float | None,
    income_tax: float | None,
    national_ins: float | None,
    health: float | None,
) -> tuple[bool, list[str]]:
    """
    Run real arithmetic checks when at least gross + net are available.

    Check 1: net must be <= gross.
    Check 2: gross - sum_of_known_deductions ≈ net  (2% tolerance of gross).

    Returns (integrity_ok, notes_list).
    """
    notes: list[str] = []

    if gross is None or net is None:
        return (True, notes)

    # Check 1
    if net > gross:
        notes.append(
            f"נטו ({net:,.0f}₪) גדול מברוטו ({gross:,.0f}₪) — לא תקין"
        )

    # Check 2 — only if we have at least one deduction
    known_deductions = sum(
        d for d in [income_tax, national_ins, health] if d is not None
    )
    if known_deductions > 0:
        implied_net = gross - known_deductions
        tolerance = gross * 0.02  # 2%
        delta = abs(implied_net - net)
        if delta > tolerance:
            notes.append(
                f"ברוטו ({gross:,.0f}₪) פחות ניכויים שזוהו ({known_deductions:,.0f}₪)"
                f" = {implied_net:,.0f}₪, אך נטו בתלוש {net:,.0f}₪"
                f" — פער של {delta:,.0f}₪ (מעל 2%)"
            )

    return (len(notes) == 0, notes)


# ---------------------------------------------------------------------------
# Phase 6: Smart income-tax threshold helper
# ---------------------------------------------------------------------------

# 2024 Israeli tax authority values
_CREDIT_POINT_TAX_VALUE: float = 258.0    # ₪ monthly tax reduction per credit point (2024)
_INCOME_TAX_MARGINAL_RATE: float = 0.10   # bottom bracket rate used for threshold estimate
_INCOME_TAX_NOISE_FLOOR: float = 20.0     # gaps ≤ ₪20 are not flagged (Phase 7.1: lowered)
_PENSION_TAX_CREDIT_RATE: float = 0.35    # Section 45a: 35% tax credit on employee pension contributions

# Phase 7: Mathematical rate-verification constants (2024 Israeli law)
# Employer pension contribution (mandatory minimum per pension regulations)
_PENSION_EMPLOYER_RATE_MIN: float = 0.065   # 6.5%
_PENSION_EMPLOYER_RATE_MAX: float = 0.085   # 8.5%

# National Insurance (ביטוח לאומי) employee rate — simplified bracket
# Rate ≈ 3.5% below NI threshold (~₪7,522/mo), 12% above.
# Rule-engine bound: 2% (low-income) to 15% (incl. edge cases)
_NI_RATE_MIN: float = 0.02    # 2%
_NI_RATE_MAX: float = 0.15    # 15%

# Health Tax (מס בריאות) — flat 3.1% below threshold, ~5% above
# (superseded by Phase 8 exact brackets; kept for reference)
_HEALTH_RATE_MIN: float = 0.015   # 1.5% (low-income edge)
_HEALTH_RATE_MAX: float = 0.065   # 6.5% (upper, with variation)

# ---------------------------------------------------------------------------
# Phase 8: Precise 2025/2026 Israeli law constants
# ---------------------------------------------------------------------------

# Income tax — 2025 multi-bracket (replaces single-rate estimate from Phase 7.1)
_INCOME_TAX_BRACKET_1_MAX: float = 7_010.0    # ₪/month — top of 10% bracket
_INCOME_TAX_BRACKET_2_MAX: float = 10_060.0   # ₪/month — top of 14% bracket
_INCOME_TAX_BRACKET_2_RATE: float = 0.14      # rate for bracket 2

# NI / Health — 2025/2026 exact bracket thresholds (replaces coarse bounds above)
_NI_BRACKET_THRESHOLD: float = 7_522.0        # monthly ₪ threshold
_NI_RATE_LOW: float = 0.0104                  # 1.04% below threshold
_NI_RATE_HIGH: float = 0.07                   # 7.00% above threshold
_HEALTH_RATE_LOW: float = 0.0323              # 3.23% below threshold
_HEALTH_RATE_HIGH: float = 0.0517             # 5.17% above threshold

# Convalescence pay (דמי הבראה) — private sector legal minimum rate
_CONVALESCENCE_DAILY_RATE_MIN: float = 418.0  # ₪/day (frozen, private sector)

# Severance / Section 14 (פיצויים / סעיף 14)
_SEVERANCE_RATE_8_33: float = 0.0833          # Section 14 full rate (≈8.33%)
_SEVERANCE_RATE_6: float = 0.06               # Standard severance rate (6%)
_SEVERANCE_RATE_TOLERANCE: float = 0.005      # ±0.5% matching tolerance

# Employee pension minimum (חוק פנסיה חובה)
_PENSION_EMPLOYEE_RATE_MIN_P8: float = 0.06   # 6% — mandatory minimum


def _check_income_tax_rule(
    gross: "float | None",
    income_tax: "float | None",
    credit_points: "float | None",
    gross_taxable: "float | None" = None,
    provident_funds_deduction: "float | None" = None,
) -> "object | None":
    """
    Phase 8: Smart income-tax presence check — 2025 multi-bracket + Section 45a credit.

    Formula (Israeli 2025):
      Bracket 1: gross_base × 10%        (up to ₪7,010/month)
      Bracket 2: (gross_base − ₪7,010) × 14%  (₪7,011–₪10,060/month)
      Less: credit_points × 258          (credit point monthly value)
      Less: provident_funds × 35%        (Section 45a pension tax credit)

    Uses gross_taxable if available; falls back to gross otherwise.

    Returns:
      - None    if income_tax is detected (no problem)
      - Info    if estimated_tax ≤ 0  (zero tax is EXPECTED — below threshold)
      - None    if 0 < estimated_tax ≤ ₪20  (borderline — not worth flagging)
      - Warning if estimated_tax > ₪20 and income_tax is None
      - Warning if gross is None and income_tax is None  (can't estimate; flag generically)
    """
    from app.models.schemas import Anomaly, AnomalySeverity

    # Income tax detected → no anomaly
    if income_tax is not None:
        return None

    # No gross → can't estimate threshold; emit generic warning
    gross_base = gross_taxable if gross_taxable is not None else gross
    if gross_base is None:
        return Anomaly(
            id="ano_missing_income_tax",
            severity=AnomalySeverity.WARNING,
            what_we_found="לא זוהה מס הכנסה בתלוש",
            why_suspicious=(
                "לא ניתן לאמת את סף המס כי ברוטו לא זוהה. "
                "בתלוש שכר רגיל, מס הכנסה צריך להופיע."
            ),
            what_to_do="בדוק שורת מס הכנסה בתלוש הפיזי. אם היא קיימת, ייתכן שהמערכת לא זיהתה אותה.",
            ask_payroll="מה גובה ניכוי מס ההכנסה שלי לחודש זה?",
            related_line_item_ids=[],
        )

    effective_credits = credit_points or 0.0
    pension_credit = abs(provident_funds_deduction or 0.0) * _PENSION_TAX_CREDIT_RATE  # Section 45a
    # Phase 8: two-bracket computation
    tax_before_credits = (
        min(gross_base, _INCOME_TAX_BRACKET_1_MAX) * _INCOME_TAX_MARGINAL_RATE
        + max(0.0, min(gross_base, _INCOME_TAX_BRACKET_2_MAX) - _INCOME_TAX_BRACKET_1_MAX)
          * _INCOME_TAX_BRACKET_2_RATE
    )
    estimated_tax = max(0.0,
        tax_before_credits
        - effective_credits * _CREDIT_POINT_TAX_VALUE
        - pension_credit
    )

    if estimated_tax <= 0:
        # Zero income tax is fully explained — emit a reassuring Info
        pension_note = (
            f", וזיכוי סעיף 45א (הפרשה לפנסיה ₪{abs(provident_funds_deduction or 0.0):,.0f} × 35%)"
            if provident_funds_deduction
            else ""
        )
        return Anomaly(
            id="ano_below_tax_threshold",
            severity=AnomalySeverity.INFO,
            what_we_found=(
                "תקין: לא נוכה מס הכנסה מכיוון שהשכר מתחת לסף המס "
                "בהתחשב בנקודות הזיכוי וההפרשות לפנסיה."
            ),
            why_suspicious=(
                f"לפי נקודות הזיכוי ({effective_credits:.2f} × ₪{_CREDIT_POINT_TAX_VALUE:.0f})"
                f"{pension_note}, "
                f"השכר הברוטו ({gross_base:,.0f}₪) אינו עובר את סף המס. "
                "מס הכנסה אפס הוא תקין לחלוטין."
            ),
            what_to_do="אין צורך בפעולה — מס הכנסה אפס הוא צפוי עבור שכר ונקודות זיכוי אלה.",
            ask_payroll="האם אני אכן פטור ממס הכנסה לפי הנקודות, הפנסיה ורמת השכר שלי?",
            related_line_item_ids=[],
        )

    if estimated_tax > _INCOME_TAX_NOISE_FLOOR:
        # Above threshold and tax is missing → Warning
        return Anomaly(
            id="ano_missing_income_tax",
            severity=AnomalySeverity.WARNING,
            what_we_found=(
                f"לא זוהה מס הכנסה — צפוי כ-₪{estimated_tax:,.0f} "
                f"(ברוטו {gross_base:,.0f}₪, {effective_credits:.2f} נקודות זיכוי)"
            ),
            why_suspicious=(
                f"לפי מדרגות מס הכנסה 2025 (10% עד ₪7,010 + 14% עד ₪10,060) "
                f"פחות זיכויי נקודות וזיכוי פנסיה סעיף 45א, "
                f"מס ההכנסה הצפוי הוא כ-₪{estimated_tax:,.0f} לחודש. "
                "אי-הופעת מס הכנסה בתלוש עשויה להצביע על שגיאה בחישוב או בזיהוי."
            ),
            what_to_do=(
                "בדוק שורת מס הכנסה בתלוש הפיזי. "
                "אם היא קיימת, ייתכן שהמערכת לא זיהתה אותה — תקן את הערך ידנית. "
                "אם היא אינה קיימת, פנה למחלקת שכר."
            ),
            ask_payroll=f"מדוע לא מופיע מס הכנסה בתלוש? הצפוי כ-₪{estimated_tax:,.0f}.",
            related_line_item_ids=[],
        )

    # Estimated in (0, ₪20] — borderline, not worth flagging
    return None


# ---------------------------------------------------------------------------
# Phase 4: Extended rule-engine checks (Warning / Info severity)
# ---------------------------------------------------------------------------

def _run_extended_checks(
    gross: float | None,
    net: float | None,
    income_tax: float | None,
    national_ins: float | None,
    health: float | None,
    credit_points: float | None,
    net_salary: float | None,
    net_to_pay: float | None,
    line_items: list,
    answers: object | None,
    gross_taxable: float | None = None,
    provident_funds_deduction: float | None = None,
    gross_ni: float | None = None,           # Phase 8: for exact NI/health bracket calc
    pension_employee: float | None = None,   # Phase 8: reserved
) -> list:
    """
    Extended rule-engine checks (Phase 4 → Phase 8).
    Returns list of Anomaly objects (Warning/Info severity).
    Critical anomalies are still handled by _build_anomalies_from_real_data().

    Rules:
      A  — Income tax: 2025 multi-bracket check w/ Section 45a pension credit (Phase 8)
      A2 — Missing national_ins AND health → Warning (regardless of income_tax)
      B  — Credit points too low (<2.0) → Info; too high (>8.0) → Warning
      C  — net_to_pay vs. net_salary gap > ₪50 → Info
      D  — Pension (employee) rate out of range → Warning
      E  — No gross found → Info (cannot validate math)
      F  — Employer pension contribution rate out of expected range [6.5%–8.5%] → Warning
      G  — NI: exact 2025/2026 bracket check (replaces crude bounds) → Warning
      H  — Health: exact 2025/2026 bracket check (replaces crude bounds) → Warning
      I  — Employee pension minimum 6% vs. "שכר לקצבה"/"שכר בסיס" base → Warning
      J  — Section 14 / Severance detection (employer_contribution פיצויים) → Info
      K  — Convalescence pay rate ≥ ₪418/day legal minimum → Warning
    """
    from app.models.schemas import Anomaly, AnomalySeverity, LineItemCategory

    anomalies: list = []

    # ------------------------------------------------------------------
    # Rule A: Income tax — smart threshold check with Section 45a (Phase 7.1)
    # ------------------------------------------------------------------
    income_tax_anomaly = _check_income_tax_rule(
        gross, income_tax, credit_points,
        gross_taxable=gross_taxable,
        provident_funds_deduction=provident_funds_deduction,
    )
    if income_tax_anomaly is not None:
        anomalies.append(income_tax_anomaly)

    # ------------------------------------------------------------------
    # Rule A2: Missing BOTH national_ins AND health → Warning
    # (These do not have a "below threshold" concept — always mandatory)
    # ------------------------------------------------------------------
    if national_ins is None and health is None:
        anomalies.append(Anomaly(
            id="ano_missing_social_deductions",
            severity=AnomalySeverity.WARNING,
            what_we_found="לא נמצאו ביטוח לאומי ומס בריאות בתלוש",
            why_suspicious=(
                "ביטוח לאומי ומס בריאות הם ניכויי חובה לכל עובד שכיר בישראל. "
                "אי-זיהויים עשוי להצביע על פורמט תלוש לא מוכר."
            ),
            what_to_do=(
                "בדוק שורות ביטוח לאומי ומס בריאות בתלוש הפיזי. "
                "אם הן קיימות, ניתן לתקן את הערכים ידנית."
            ),
            ask_payroll="מדוע לא מופיעים ביטוח לאומי ומס בריאות בתלוש?",
            related_line_item_ids=[],
        ))

    # ------------------------------------------------------------------
    # Rule B: Credit points sanity check
    # ------------------------------------------------------------------
    if credit_points is not None:
        if credit_points < 2.0:
            anomalies.append(Anomaly(
                id="ano_low_credit_points",
                severity=AnomalySeverity.INFO,
                what_we_found=f"נקודות זיכוי נמוכות מהרגיל: {credit_points:.2f} נקודות",
                why_suspicious=(
                    "הנקודות הסטנדרטיות לעובד שכיר (2025): 2.25 נקודות לרווק, 2.75 לנשוי. "
                    f"זוהו רק {credit_points:.2f} נקודות — ייתכן טעות בדיווח או מצב מיוחד."
                ),
                what_to_do=(
                    "בדוק עם מחלקת שכר שנקודות הזיכוי שלך מעודכנות לפי מצבך המשפחתי. "
                    "נקודות חסרות = תשלום מס גבוה מהנדרש."
                ),
                ask_payroll="כמה נקודות זיכוי מדווחות עבורי? האם הן תואמות את מצבי האישי?",
                related_line_item_ids=[],
            ))
        elif credit_points > 8.0:
            anomalies.append(Anomaly(
                id="ano_high_credit_points",
                severity=AnomalySeverity.WARNING,
                what_we_found=f"נקודות זיכוי גבוהות מאוד: {credit_points:.2f} נקודות",
                why_suspicious=(
                    "מספר נקודות זיכוי גבוה מ-8 אינו שכיח. "
                    f"זוהו {credit_points:.2f} נקודות — ייתכן שגיאה בדיווח, "
                    "או מצב חריג (נכות, עולה חדש, הורה לילד עם צרכים מיוחדים)."
                ),
                what_to_do=(
                    "בדוק עם מחלקת שכר שמספר נקודות הזיכוי נכון. "
                    "טעות בנקודות זיכוי עלולה לגרור חוב מס בסוף השנה."
                ),
                ask_payroll="על בסיס מה חושב מספר נקודות הזיכוי שלי?",
                related_line_item_ids=[],
            ))

    # ------------------------------------------------------------------
    # Rule C: net_to_pay vs. net_salary gap > ₪50 → Info
    # ------------------------------------------------------------------
    if net_to_pay is not None and net_salary is not None:
        gap = abs(net_to_pay - net_salary)
        if gap > 50:
            anomalies.append(Anomaly(
                id="ano_net_to_pay_gap",
                severity=AnomalySeverity.INFO,
                what_we_found=(
                    f"הפרש בין שכר נטו ({net_salary:,.0f}₪) "
                    f"לנטו לתשלום ({net_to_pay:,.0f}₪): {gap:,.0f}₪"
                ),
                why_suspicious=(
                    "שכר נטו הוא השכר לאחר ניכויי חובה. "
                    "נטו לתשלום הוא הסכום שהועבר לחשבון הבנק בפועל. "
                    f"פער של {gap:,.0f}₪ מרמז על ניכויים נוספים (הלוואה, עיקול, ביטוח, ועוד)."
                ),
                what_to_do=(
                    "בדוק בתלוש אם יש שורות ניכוי נוספות שאינן בקטגוריית מס חובה. "
                    "הפרש זה הוא לגיטימי אם יש ניכויים מרצון."
                ),
                ask_payroll="מה הסיבה להפרש בין שכר נטו לנטו לתשלום בתלוש שלי?",
                related_line_item_ids=[],
            ))

    # ------------------------------------------------------------------
    # Rule D: Pension (employee) rate out of range → Warning
    # (Checks for pension_employee deduction in line_items vs. gross)
    # ------------------------------------------------------------------
    if gross is not None and gross > 0 and line_items:
        pension_item = next(
            (li for li in line_items
             if getattr(li, "category", None) is not None
             and str(getattr(li, "category", "")) in ("deduction", "LineItemCategory.DEDUCTION")
             and li.value is not None
             and any(kw in getattr(li, "description_hebrew", "")
                     for kw in ("פנסיה", "קרן פנסיה", "קופת גמל", "תגמולים"))),
            None,
        )
        if pension_item is not None and pension_item.value is not None:
            pension_abs = abs(pension_item.value)
            pension_rate = pension_abs / gross
            if pension_rate < 0.055 or pension_rate > 0.08:
                anomalies.append(Anomaly(
                    id="ano_pension_rate_unusual",
                    severity=AnomalySeverity.WARNING,
                    what_we_found=(
                        f"שיעור ניכוי פנסיה חריג: {pension_rate:.1%} מהברוטו "
                        f"({pension_abs:,.0f}₪ מתוך {gross:,.0f}₪)"
                    ),
                    why_suspicious=(
                        "לפי חוק פנסיה חובה בישראל, שיעור תגמולי עובד הוא 6–7% מהשכר. "
                        f"השיעור שנמצא ({pension_rate:.1%}) חורג מהטווח המצופה (5.5%–8%). "
                        "ייתכן שחלק מהפנסיה לא זוהה, או שמדובר בהסכם מיוחד."
                    ),
                    what_to_do=(
                        "בדוק עם מחלקת שכר את שיעור ניכוי הפנסיה הנכון עבורך. "
                        "וודא שהפנסיה מנוכה ומועברת לקרן בפועל."
                    ),
                    ask_payroll="מהו שיעור ניכוי הפנסיה שלי ולאיזו קרן הוא מועבר?",
                    related_line_item_ids=[getattr(pension_item, "id", "")],
                ))

    # ------------------------------------------------------------------
    # Rule F: Employer pension contribution rate out of expected range → Warning
    # (Searches line_items for employer_contribution category with pension keywords)
    # ------------------------------------------------------------------
    if gross is not None and gross > 0 and line_items:
        employer_pension_item = next(
            (li for li in line_items
             if getattr(li, "category", None) is not None
             and str(getattr(li, "category", "")) in (
                 "employer_contribution", "LineItemCategory.EMPLOYER_CONTRIBUTION"
             )
             and li.value is not None
             and any(kw in getattr(li, "description_hebrew", "")
                     for kw in ("פנסיה", "תגמולים", "קרן פנסיה", "קופת גמל"))),
            None,
        )
        if employer_pension_item is not None and employer_pension_item.value is not None:
            ep_abs = abs(employer_pension_item.value)
            ep_rate = ep_abs / gross
            if ep_rate < _PENSION_EMPLOYER_RATE_MIN or ep_rate > _PENSION_EMPLOYER_RATE_MAX:
                anomalies.append(Anomaly(
                    id="ano_employer_pension_rate_unusual",
                    severity=AnomalySeverity.WARNING,
                    what_we_found=(
                        f"הפרשת מעסיק לפנסיה: {ep_rate:.1%} מהשכר "
                        f"({ep_abs:,.0f}₪ מתוך {gross:,.0f}₪) — צפוי 6.5%–8.5%"
                    ),
                    why_suspicious=(
                        "לפי תקנות פנסיה חובה בישראל, המעסיק מחויב להפריש לפחות 6.5% מהשכר. "
                        f"השיעור שנמצא ({ep_rate:.1%}) חורג מהטווח המצופה (6.5%–8.5%). "
                        "שיעור חריג עשוי להצביע על שגיאה בזיהוי או על הסכם מיוחד."
                    ),
                    what_to_do=(
                        "בדוק עם מחלקת שכר את שיעור הפרשת המעסיק לפנסיה. "
                        "וודא שהסכום מועבר לקרן הפנסיה שלך בפועל."
                    ),
                    ask_payroll="מה שיעור הפרשת המעסיק לפנסיה החודש?",
                    related_line_item_ids=[getattr(employer_pension_item, "id", "")],
                ))

    # ------------------------------------------------------------------
    # Rule G: National Insurance — exact 2025/2026 bracket computation (Phase 8)
    # Replaces crude rate-bounds check. Uses gross_ni (preferred) or gross as base.
    # Tolerances: abs diff > ₪20 AND percentage > 5% → warn
    # Anomaly ID: "ano_national_insurance_bracket_mismatch"
    # ------------------------------------------------------------------
    ni_base = gross_ni if gross_ni is not None else gross
    if ni_base is not None and ni_base > 0 and national_ins is not None and national_ins > 0:
        if ni_base <= _NI_BRACKET_THRESHOLD:
            expected_ni = ni_base * _NI_RATE_LOW
        else:
            expected_ni = (
                _NI_BRACKET_THRESHOLD * _NI_RATE_LOW
                + (ni_base - _NI_BRACKET_THRESHOLD) * _NI_RATE_HIGH
            )
        ni_diff = abs(national_ins - expected_ni)
        ni_pct  = ni_diff / expected_ni if expected_ni > 0 else 0.0
        if ni_diff > _INCOME_TAX_NOISE_FLOOR and ni_pct > 0.05:
            bracket_desc = (
                "1.04% (מתחת לתקרה)"
                if ni_base <= _NI_BRACKET_THRESHOLD
                else "1.04% עד ₪7,522 + 7.00% מעל התקרה"
            )
            anomalies.append(Anomaly(
                id="ano_national_insurance_bracket_mismatch",
                severity=AnomalySeverity.WARNING,
                what_we_found=(
                    f"ביטוח לאומי: ₪{national_ins:,.0f} בפועל — צפוי ₪{expected_ni:,.0f} "
                    f"לפי מדרגות 2025/2026 (בסיס ₪{ni_base:,.0f})"
                ),
                why_suspicious=(
                    f"לפי חישוב מדרגות ביטוח לאומי 2025/2026: {bracket_desc}. "
                    f"הפרש: ₪{ni_diff:,.0f} ({ni_pct:.0%}) — חורג מסף הדיוק המותר."
                ),
                what_to_do=(
                    "השווה את סכום ביטוח הלאומי בתלוש הפיזי. "
                    "אם הסכום שגוי, ניתן לתקן אותו ידנית בממשק התיקונים."
                ),
                ask_payroll="האם ניכוי ביטוח הלאומי תואם את חישוב מדרגות 2025/2026?",
                related_line_item_ids=[],
            ))

    # ------------------------------------------------------------------
    # Rule H: Health tax — exact 2025/2026 bracket computation (Phase 8)
    # Same ni_base as Rule G. Anomaly ID: "ano_health_tax_bracket_mismatch"
    # ------------------------------------------------------------------
    if ni_base is not None and ni_base > 0 and health is not None and health > 0:
        if ni_base <= _NI_BRACKET_THRESHOLD:
            expected_health = ni_base * _HEALTH_RATE_LOW
        else:
            expected_health = (
                _NI_BRACKET_THRESHOLD * _HEALTH_RATE_LOW
                + (ni_base - _NI_BRACKET_THRESHOLD) * _HEALTH_RATE_HIGH
            )
        health_diff = abs(health - expected_health)
        health_pct  = health_diff / expected_health if expected_health > 0 else 0.0
        if health_diff > _INCOME_TAX_NOISE_FLOOR and health_pct > 0.05:
            health_bracket_desc = (
                "3.23% (מתחת לתקרה)"
                if ni_base <= _NI_BRACKET_THRESHOLD
                else "3.23% עד ₪7,522 + 5.17% מעל התקרה"
            )
            anomalies.append(Anomaly(
                id="ano_health_tax_bracket_mismatch",
                severity=AnomalySeverity.WARNING,
                what_we_found=(
                    f"מס בריאות: ₪{health:,.0f} בפועל — צפוי ₪{expected_health:,.0f} "
                    f"לפי מדרגות 2025/2026 (בסיס ₪{ni_base:,.0f})"
                ),
                why_suspicious=(
                    f"לפי חישוב מדרגות מס בריאות 2025/2026: {health_bracket_desc}. "
                    f"הפרש: ₪{health_diff:,.0f} ({health_pct:.0%}) — חורג מסף הדיוק המותר."
                ),
                what_to_do=(
                    "השווה את סכום מס הבריאות בתלוש הפיזי. "
                    "אם הסכום שגוי, ניתן לתקן אותו ידנית בממשק התיקונים."
                ),
                ask_payroll="האם ניכוי מס הבריאות תואם את חישוב מדרגות 2025/2026?",
                related_line_item_ids=[],
            ))

    # ------------------------------------------------------------------
    # Rule I: Employee pension minimum (6%) vs. pension base salary (Phase 8)
    # ONLY fires when a "שכר לקצבה" / "שכר בסיס" / "משכורת בסיס" EARNING item is found.
    # Do NOT use gross as base — it includes overtime/travel not in pension base.
    # ------------------------------------------------------------------
    pension_base_item = next(
        (li for li in line_items
         if str(getattr(li, "category", "")) in ("earning", "LineItemCategory.EARNING")
         and any(kw in getattr(li, "description_hebrew", "")
                 for kw in ("שכר לקצבה", "שכר בסיס", "משכורת בסיס"))
         and li.value is not None and li.value > 0),
        None,
    )
    if pension_base_item is not None:
        pension_base_salary = pension_base_item.value
        employee_pension_item = next(
            (li for li in line_items
             if str(getattr(li, "category", "")) in ("deduction", "LineItemCategory.DEDUCTION")
             and any(kw in getattr(li, "description_hebrew", "")
                     for kw in ("פנסיה", "קרן פנסיה", "קופת גמל", "תגמולים"))
             and "השתלמות" not in getattr(li, "description_hebrew", "")
             and li.value is not None),
            None,
        )
        if (employee_pension_item is not None
                and pension_base_salary is not None
                and pension_base_salary > 0):
            emp_pension_abs = abs(employee_pension_item.value)
            emp_pension_rate = emp_pension_abs / pension_base_salary
            if emp_pension_rate < _PENSION_EMPLOYEE_RATE_MIN_P8:
                anomalies.append(Anomaly(
                    id="ano_pension_employee_below_minimum",
                    severity=AnomalySeverity.WARNING,
                    what_we_found=(
                        f"שיעור ניכוי פנסיה עובד: {emp_pension_rate:.1%} משכר הבסיס לקצבה "
                        f"({emp_pension_abs:,.0f}₪ מתוך ₪{pension_base_salary:,.0f}) — "
                        "מינימום חוקי: 6%"
                    ),
                    why_suspicious=(
                        "לפי חוק פנסיה חובה בישראל, שיעור תגמולי עובד הוא לפחות 6% משכר הבסיס לקצבה. "
                        f"השיעור שנמצא ({emp_pension_rate:.1%}) נמוך מהמינימום החוקי."
                    ),
                    what_to_do=(
                        "בדוק עם מחלקת שכר את שיעור ניכוי הפנסיה הנכון עבורך. "
                        "ייתכן שיש הסכם מיוחד, אך בדרך כלל שיעור זה אינו תקין."
                    ),
                    ask_payroll="מדוע שיעור ניכוי הפנסיה שלי נמוך מ-6% משכר הבסיס לקצבה?",
                    related_line_item_ids=[
                        getattr(employee_pension_item, "id", ""),
                        getattr(pension_base_item, "id", ""),
                    ],
                ))
    else:
        pension_base_salary = None  # used by Rule J below

    # ------------------------------------------------------------------
    # Rule J: Section 14 / Severance detection (Phase 8)
    # Looks for employer_contribution item with פיצויים keywords.
    # Emits Info if Section 14 rate (8.33%) or standard rate (6%) detected.
    # If NO severance item found but other employer_contribution items exist → Info.
    # ------------------------------------------------------------------
    severance_item = next(
        (li for li in line_items
         if str(getattr(li, "category", "")) in (
             "employer_contribution", "LineItemCategory.EMPLOYER_CONTRIBUTION"
         )
         and any(kw in getattr(li, "description_hebrew", "")
                 for kw in ("פיצויים", "קרן פיצוי", "הפרשה לפיצויים"))
         and li.value is not None),
        None,
    )
    severance_base = pension_base_salary if pension_base_salary else gross

    if severance_item is not None and severance_item.value is not None and severance_base:
        sev_rate = abs(severance_item.value) / severance_base
        if abs(sev_rate - _SEVERANCE_RATE_8_33) <= _SEVERANCE_RATE_TOLERANCE:
            anomalies.append(Anomaly(
                id="ano_section14_detected",
                severity=AnomalySeverity.INFO,
                what_we_found=(
                    f"הפרשה לפיצויים: {sev_rate:.1%} — מזוהה סעיף 14 לחוק פיצויי פיטורין"
                ),
                why_suspicious=(
                    "הפרשה בשיעור 8.33% מרמזת על חתימה על סעיף 14. "
                    "במסגרת סעיף 14 כספי הפיצויים שייכים לך גם אם תפוטר, "
                    "אולם ייתכן ויתור על תביעות פיצויים נוספות."
                ),
                what_to_do="בדוק אם חתמת על הסכם סעיף 14 ומהן ההשלכות עבורך.",
                ask_payroll="האם אני חתום/ה על הסכם סעיף 14? מה משמעותו עבורי?",
                related_line_item_ids=[getattr(severance_item, "id", "")],
            ))
        elif abs(sev_rate - _SEVERANCE_RATE_6) <= _SEVERANCE_RATE_TOLERANCE:
            anomalies.append(Anomaly(
                id="ano_standard_severance_detected",
                severity=AnomalySeverity.INFO,
                what_we_found=f"הפרשה לפיצויים: {sev_rate:.1%} — שיעור פיצויים סטנדרטי (6%)",
                why_suspicious=(
                    "הפרשה בשיעור 6% היא השיעור המחויב בחוק פיצויי פיטורין. "
                    "אין סימן לסעיף 14."
                ),
                what_to_do="בדוק מה ההסדר שלך לגבי פיצויי פיטורין עם המעסיק.",
                ask_payroll="מה הסדר הפיצויים שלי — האם אני מכוסה/ת בסעיף 14?",
                related_line_item_ids=[getattr(severance_item, "id", "")],
            ))
        # else: non-standard rate — no anomaly (legitimate arrangement)
    else:
        # No severance item found — emit Info only if other employer_contribution items exist
        # (proves the section was parsed; guards against OCR extraction failure)
        has_employer_items = any(
            str(getattr(li, "category", "")) in (
                "employer_contribution", "LineItemCategory.EMPLOYER_CONTRIBUTION"
            )
            for li in line_items
        )
        if has_employer_items:
            anomalies.append(Anomaly(
                id="ano_severance_not_detected",
                severity=AnomalySeverity.INFO,
                what_we_found="לא זוהתה הפרשה לפיצויים בסעיף הפרשות מעסיק",
                why_suspicious=(
                    "הפרשות מעסיק נמצאו בתלוש, אך לא נמצאה שורת פיצויים. "
                    "ייתכן שהמעסיק מפריש לפיצויים תחת שם שונה, או שהתלוש לא מציג זאת."
                ),
                what_to_do=(
                    "בדוק מול מחלקת השכר מה הסדר הפיצויים שלך "
                    "ולאיזו קרן הם מועברים."
                ),
                ask_payroll="היכן ואיך מפורטת ההפרשה לפיצויים שלי? האם אני מכוסה/ת בסעיף 14?",
                related_line_item_ids=[],
            ))

    # ------------------------------------------------------------------
    # Rule K: Convalescence pay rate (דמי הבראה) — ₪418/day legal minimum (Phase 8)
    # Uses LineItem.rate (תעריף) first, then derives from value/quantity (כמות).
    # Skips silently if neither rate nor quantity is available (no false positives).
    # ------------------------------------------------------------------
    convalescence_item = next(
        (li for li in line_items
         if str(getattr(li, "category", "")) in ("earning", "LineItemCategory.EARNING")
         and "הבראה" in getattr(li, "description_hebrew", "")
         and li.value is not None),
        None,
    )
    if convalescence_item is not None:
        conv_value = abs(convalescence_item.value)
        conv_rate_field  = getattr(convalescence_item, "rate", None)
        conv_qty_field   = getattr(convalescence_item, "quantity", None)

        implied_rate: float | None = None
        if conv_rate_field is not None and conv_rate_field > 0:
            implied_rate = conv_rate_field
        elif conv_qty_field is not None and conv_qty_field > 0 and conv_value > 0:
            implied_rate = conv_value / conv_qty_field

        if implied_rate is not None and implied_rate < _CONVALESCENCE_DAILY_RATE_MIN:
            anomalies.append(Anomaly(
                id="ano_convalescence_rate_low",
                severity=AnomalySeverity.WARNING,
                what_we_found=(
                    f"תעריף יום הבראה: ₪{implied_rate:,.2f} — "
                    f"נמוך מהמינימום החוקי (₪{_CONVALESCENCE_DAILY_RATE_MIN:.0f})"
                ),
                why_suspicious=(
                    f"תעריף יום הבראה נמוך מהקבוע בחוק (₪{_CONVALESCENCE_DAILY_RATE_MIN:.0f} "
                    f"למגזר הפרטי). נמצא תעריף של ₪{implied_rate:,.2f}."
                ),
                what_to_do=(
                    f"בדוק עם מחלקת שכר שתעריף ההבראה עדכני. "
                    f"המינימום החוקי הוא ₪{_CONVALESCENCE_DAILY_RATE_MIN:.0f} ליום. "
                    f"לתשומת לבך: אם דמי ההבראה משולמים באופן חודשי יחסי, "
                    f"התעריף עשוי להיראות נמוך מהקבוע בחוק (₪{_CONVALESCENCE_DAILY_RATE_MIN:.0f})."
                ),
                ask_payroll=(
                    f"מה תעריף יום ההבראה שלי? "
                    f"המינימום החוקי הוא ₪{_CONVALESCENCE_DAILY_RATE_MIN:.0f}. "
                    f"האם דמי ההבראה משולמים באופן חודשי יחסי?"
                ),
                related_line_item_ids=[getattr(convalescence_item, "id", "")],
            ))

    # ------------------------------------------------------------------
    # Rule E: No gross found → Info (cannot validate math)
    # ------------------------------------------------------------------
    if gross is None:
        anomalies.append(Anomaly(
            id="ano_no_gross_found",
            severity=AnomalySeverity.INFO,
            what_we_found="לא זוהה שכר ברוטו — לא ניתן לאמת את חישובי התלוש",
            why_suspicious=(
                "בלי שכר ברוטו לא ניתן לבדוק אם הניכויים תקינים "
                "ואם הנטו מחושב נכון. "
                "ייתכן שהפורמט של התלוש שונה מהרגיל."
            ),
            what_to_do=(
                "חפש את שורת 'ברוטו' או 'סה\"כ ברוטו' בתלוש הפיזי "
                "וודא שהסכום מופיע בצורה ברורה."
            ),
            ask_payroll="מהו שכר הברוטו שלי הכולל לחודש זה?",
            related_line_item_ids=[],
        ))

    return anomalies


def _build_anomalies_from_real_data(
    gross: float | None,
    net: float | None,
    integrity_ok: bool,
    integrity_notes: list[str],
    income_tax: float | None = None,
    national_ins: float | None = None,
    health: float | None = None,
    credit_points: float | None = None,
    net_salary: float | None = None,
    net_to_pay: float | None = None,
    line_items: list | None = None,
    answers: object | None = None,
    gross_taxable: float | None = None,
    provident_funds_deduction: float | None = None,
    gross_ni: float | None = None,           # Phase 8: for exact NI/health bracket calc
    pension_employee: float | None = None,   # Phase 8: reserved
) -> list:
    """
    Build Anomaly objects for integrity failures detected from real extracted values.

    Phase 4: also calls _run_extended_checks() to append Warning/Info anomalies.
    Extended kwargs are all optional for full backward compatibility with parse_pdf().
    Returns an empty list when everything is OK.
    """
    from app.models.schemas import Anomaly, AnomalySeverity

    anomalies: list = []

    # Critical anomaly for math mismatch (existing behaviour)
    if not integrity_ok and gross is not None and net is not None:
        anomalies.append(
            Anomaly(
                id="ano_real_net_mismatch",
                severity=AnomalySeverity.CRITICAL,
                what_we_found="פער בין ברוטו פחות ניכויים לנטו: " + "; ".join(integrity_notes),
                why_suspicious=(
                    "ברוטו פחות הניכויים שזיהינו לא מתאים לנטו שמופיע בתלוש. "
                    "ייתכן שיש ניכויים נוספים שלא זוהו (פנסיה, הלוואה, עיקול, ביטוח מנהלים)."
                ),
                what_to_do=(
                    "השווה כל שורת ניכוי בתלוש לסכום הניכויים הכולל. "
                    "בדוק אם יש שורה שלא מופיעה בפירוט."
                ),
                ask_payroll="האם יש ניכוי נוסף שאינו מפורט בתלוש? מהי רשימת כל הניכויים החודשיים שלי?",
                related_line_item_ids=[],
            )
        )

    # Phase 4: extended Warning/Info checks
    anomalies.extend(_run_extended_checks(
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
        pension_employee=pension_employee,
    ))

    return anomalies


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_pdf(file_path: str | Path, answers=None) -> "ParsedSlipPayload":  # type: ignore[name-defined]
    """
    Top-level synchronous parser entry point.

    1. Extract text from PDF via pdfplumber.
    2. If no text layer (or extraction fails): return OCR_REQUIRED payload.
    3. Otherwise: run regex extraction for all known fields and return
       a real ParsedSlipPayload with parse_source="pdf_text_layer".

    Must be called from a thread (via asyncio.to_thread) since pdfplumber is sync.
    """
    from app.models.schemas import (
        Anomaly,
        LineItem,
        LineItemCategory,
        ParsedSlipPayload,
        SectionBlock,
        SlipMeta,
        SummaryTotals,
        TaxCreditsDetected,
    )

    # --- Extract text ---
    pages_text = extract_text_from_pdf(file_path)

    # --- Case 1: no text layer ---
    if not pages_text or not has_text_layer(pages_text):
        logger.info(
            "No text layer detected in %s (%d chars) — returning OCR_REQUIRED",
            file_path,
            sum(len(t.strip()) for t in pages_text.values()),
        )
        return ParsedSlipPayload(
            slip_meta=SlipMeta(pay_month=None, provider_guess="unknown", confidence=0.0),
            summary=SummaryTotals(integrity_ok=True),
            line_items=[],
            anomalies=[],
            blocks=[],
            answers_applied=answers is not None,
            error_code="OCR_REQUIRED",
            parse_source="ocr_required",
        )

    # --- Case 2: text layer present ---
    full_text = "\n".join(pages_text.values())
    logger.info(
        "Text layer found in %s: %d chars across %d page(s)",
        file_path,
        len(full_text.strip()),
        len(pages_text),
    )

    # Extract individual fields
    net_field = extract_field(pages_text, "net_pay", FIELD_PATTERNS["net_pay"])
    gross_field = extract_field(pages_text, "gross_pay", FIELD_PATTERNS["gross_pay"])
    income_tax_field = extract_field(pages_text, "income_tax", FIELD_PATTERNS["income_tax"])
    national_ins_field = extract_field(pages_text, "national_insurance", FIELD_PATTERNS["national_insurance"])
    health_field = extract_field(pages_text, "health_tax", FIELD_PATTERNS["health_tax"])
    credits_field = extract_field(pages_text, "tax_credits", FIELD_PATTERNS["tax_credits"])
    pay_month_result = extract_pay_month(pages_text)
    provider_name, provider_conf = detect_provider(full_text)

    # Resolve scalar values
    net = net_field.value if net_field else None
    gross = gross_field.value if gross_field else None
    income_tax = income_tax_field.value if income_tax_field else None
    national_ins = national_ins_field.value if national_ins_field else None
    health = health_field.value if health_field else None
    credit_points = credits_field.value if credits_field else None
    pay_month = pay_month_result[0] if pay_month_result else None

    # Phase 8.1: Zero-field fallback — if text-layer extraction found neither gross nor net,
    # the text layer is probably present but unreadable (proprietary font encoding, rotated
    # page, unusual layout, etc.). Fall through to OCR rather than returning an empty result.
    # This is belt-and-suspenders: has_text_layer() already filters most encoding problems,
    # but some PDFs may have a few Hebrew chars yet still fail all field patterns.
    if gross is None and net is None:
        logger.info(
            "Text-layer extraction yielded no gross/net for %s — treating as OCR_REQUIRED",
            file_path,
        )
        return ParsedSlipPayload(  # type: ignore[call-arg]
            slip_meta=SlipMeta(provider_guess="unknown"),
            summary=SummaryTotals(),
            line_items=[],
            anomalies=[],
            blocks=[],
            error_code="OCR_REQUIRED",
            parse_source="ocr_required",
            transient=True,
        )

    # Total known deductions
    known_deductions = sum(d for d in [income_tax, national_ins, health] if d is not None)
    total_deductions = known_deductions if known_deductions > 0 else None

    # Integrity check
    integrity_ok, integrity_notes = _run_integrity_checks(
        gross, net, income_tax, national_ins, health
    )

    # Build line items for each extracted field
    line_items: list[LineItem] = []
    if gross_field:
        line_items.append(LineItem(
            id="li_gross",
            category=LineItemCategory.EARNING,
            description_hebrew="ברוטו",
            explanation_hebrew="סך השכר ברוטו כפי שנקרא מהתלוש.",
            value=gross,
            raw_text=gross_field.raw_text,
            confidence=gross_field.confidence,
            page_index=gross_field.source_page,
        ))
    if income_tax_field:
        line_items.append(LineItem(
            id="li_income_tax",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="מס הכנסה",
            explanation_hebrew="ניכוי מס הכנסה מחושב לפי מדרגות המס ונקודות הזיכוי שלך.",
            value=-(income_tax or 0),
            raw_text=income_tax_field.raw_text,
            confidence=income_tax_field.confidence,
            page_index=income_tax_field.source_page,
        ))
    if national_ins_field:
        line_items.append(LineItem(
            id="li_national_ins",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="ביטוח לאומי (עובד)",
            explanation_hebrew="ניכוי ביטוח לאומי חלק העובד — מממן גמלאות נכות, אבטלה ועוד.",
            value=-(national_ins or 0),
            raw_text=national_ins_field.raw_text,
            confidence=national_ins_field.confidence,
            page_index=national_ins_field.source_page,
        ))
    if health_field:
        line_items.append(LineItem(
            id="li_health",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="מס בריאות",
            explanation_hebrew="ניכוי מס בריאות המממן את קופות החולים.",
            value=-(health or 0),
            raw_text=health_field.raw_text,
            confidence=health_field.confidence,
            page_index=health_field.source_page,
        ))

    # Build anomalies from real integrity check (Phase 4: pass extended fields)
    anomalies: list[Anomaly] = _build_anomalies_from_real_data(  # type: ignore[assignment]
        gross, net, integrity_ok, integrity_notes,
        income_tax=income_tax,
        national_ins=national_ins,
        health=health,
        credit_points=credit_points,
        line_items=line_items,
        answers=answers,
    )

    # Overall meta confidence: average of net + gross confidence (or 0.5 if neither found)
    extracted_confs = [f.confidence for f in [net_field, gross_field] if f is not None]
    meta_conf = (sum(extracted_confs) / len(extracted_confs)) if extracted_confs else 0.5
    slip_conf = round(provider_conf * meta_conf, 2) if provider_conf > 0 else round(meta_conf * 0.4, 2)

    # Tax credits
    tax_credits = None
    if credit_points is not None and credits_field is not None:
        tax_credits = TaxCreditsDetected(
            credit_points_detected=credit_points,
            estimated_monthly_value=round(credit_points * CREDIT_POINT_VALUE, 0),
            confidence=credits_field.confidence,
            notes=[f"זוהו {credit_points} נקודות זיכוי בתלוש"],
        )

    # Section blocks: one per page with text
    blocks: list[SectionBlock] = [
        SectionBlock(
            section_name=f"עמוד {page_idx + 1}",
            bbox_json=None,
            page_index=page_idx,
            raw_text_preview=text.strip()[:200],
        )
        for page_idx, text in pages_text.items()
        if text.strip()
    ]

    return ParsedSlipPayload(
        slip_meta=SlipMeta(
            pay_month=pay_month,
            provider_guess=provider_name or "unknown",
            confidence=slip_conf,
            employer_name=None,
            employee_name_redacted=True,
        ),
        summary=SummaryTotals(
            gross=gross,
            gross_confidence=gross_field.confidence if gross_field else 0.0,
            net=net,
            net_confidence=net_field.confidence if net_field else 0.0,
            total_deductions=total_deductions,
            total_employer_contributions=None,
            income_tax=income_tax,
            national_insurance=national_ins,
            health_insurance=health,
            pension_employee=None,
            integrity_ok=integrity_ok,
            integrity_notes=integrity_notes,
        ),
        line_items=line_items,
        anomalies=anomalies,
        insights=_build_insights(gross, net, income_tax, national_ins, health, None, credit_points, line_items),
        blocks=blocks,
        tax_credits_detected=tax_credits,
        answers_applied=answers is not None,
        error_code=None,
        parse_source="pdf_text_layer",
    )


# ---------------------------------------------------------------------------
# Phase 2D.2: OCR line-item extraction from payslip table
# ---------------------------------------------------------------------------

# ── Anchor patterns ──────────────────────────────────────────────────────────
# Start anchor: "פרוט התשלומים" (detail of payments) — marks start of earnings table
_LI_TABLE_START_RE = re.compile(
    r'פרו\S{0,2}\s+ה?תשלומ',
    re.UNICODE | re.IGNORECASE,
)

# Stop anchors: appearance of these means we've left the earnings table region
_LI_TABLE_STOP_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'ניכויי\s+חובה', re.UNICODE | re.IGNORECASE),      # mandatory deductions section header
    re.compile(r'קופות\s+גמל', re.UNICODE | re.IGNORECASE),         # provident funds section
    re.compile(r'ניכויים\s+והפרש', re.UNICODE | re.IGNORECASE),     # deductions & differences header
    re.compile(r'סה["\u05d4]?כ\s+ברוטו', re.UNICODE | re.IGNORECASE),  # gross total
    re.compile(r'ברוטו\s+ל(?:מס|ב\.?ל)', re.UNICODE | re.IGNORECASE),  # gross-for-tax/NI summary box
]

# ── Known-item lookup table ──────────────────────────────────────────────────
# Each entry: (keyword_regex, category, display_name, explanation_hebrew)
# Keywords are searched with re.search() on the OCR line (UNICODE | IGNORECASE).
# Items are tried in order; first match wins.
_LI_KNOWN_ITEMS: list[tuple[str, str, str, str]] = [
    # ── Earnings ────────────────────────────────────────────────────────────
    (
        r'משכורת\s+בסיס|שכר\s+בסיס',
        "earning",
        "משכורת בסיס",
        "שכר הבסיס החודשי שסוכם בחוזה ההעסקה שלך. זהו הרכיב העיקרי של תלוש השכר ממנו מחושבים שאר הרכיבים כגון שעות נוספות, פנסיה ועוד.",
    ),
    (
        r'משכורת\s+שבת|שכר\s+שבת',
        "earning",
        "משכורת שבת",
        "תוספת שכר בגין עבודה בשבת או בחגים, המחושבת לפי תעריף שעתי כפול 150%–200% בהתאם להסכם הקיבוצי או לחוזה האישי.",
    ),
    (
        r'שעות\s+נוספות?|שע[\"\']\s*נ|125%|150%|175%|200%',
        "earning",
        "שעות נוספות",
        "תגמול על שעות עבודה מעבר למכסה החוקית (8 שעות ביום / 45 בשבוע). השעות הנוספות הראשונות מתוגמלות ב-125% ואחריהן ב-150% מהשכר הרגיל.",
    ),
    (
        r'שעות\s+הפסקה|הפסקות',
        "earning",
        "שעות הפסקה",
        "תשלום בגין שעות הפסקה הנכללות בשעות העבודה על-פי חוזה. ייתכן שמדובר בהפסקות בתשלום כחלק מהסכמי העבודה.",
    ),
    (
        r'הבר[אא]ה|דמי\s+הבר',
        "earning",
        "דמי הבראה",
        "תשלום שנתי הניתן לעובדים לאחר שנה ראשונה (מינימום 1 יום הבראה = ₪450 לשנת 2025). מחושב לפי ימי ותק × ערך יום הבראה. ייתכן שמחולק לתשלומים חודשיים.",
    ),
    (
        r'נסיעות|דמי\s+נסיעה|הוצאות\s+נסיעה',
        "earning",
        "דמי נסיעה",
        "החזר הוצאות נסיעה לעבודה ומהעבודה. השיעור המקסימלי הוא לפי כרטיס חופשי חודשי בתחבורה הציבורית. הסכום אינו חייב במס הכנסה עד לתקרה הקבועה.",
    ),
    (
        r'כוננות|תורנות',
        "earning",
        "כוננות / תורנות",
        "תוספת שכר בגין זמינות מחוץ לשעות העבודה הרגילות. הכוננות מחושבת לרוב כאחוז מהשכר הבסיסי ומחויבת במס.",
    ),
    (
        r'בונוס|פרמיה|תגמול|מענק',
        "earning",
        "בונוס / פרמיה",
        "תשלום חד-פעמי מעבר לשכר הרגיל. בונוסים מחויבים במס הכנסה בשיעור שולי ועשויים להשפיע על זכאות לגמלאות.",
    ),
    (
        r'ביגוד|הלבשה',
        "earning",
        "תוספת ביגוד",
        "החזר הוצאות ביגוד לעבודה. עד לתקרה הפטורה ממס — הסכום אינו נכלל בבסיס השכר לפנסיה.",
    ),
    (
        r'חגים|חג\b|יום\s+טוב',
        "earning",
        "תשלום חגים",
        "תשלום בגין ימי חג בתשלום. על-פי חוק, עובד זכאי ל-9 ימי חג בשנה בתשלום.",
    ),
    (
        r'ותק|יובל',
        "earning",
        "תוספת ותק",
        "תוספת שכר הניתנת על-פי מספר שנות הוותק אצל המעסיק או בענף. מחויבת במס ונכללת בבסיס הפנסיה.",
    ),
    (
        r'קצובת\s+מזון|ארוחות|פנסיה\s+אוכל',
        "earning",
        "קצובת מזון",
        "תוספת קצובת מזון. סכומים עד לתקרה הפטורה אינם חייבים במס ואינם נכללים בבסיס הפנסיה.",
    ),
    # ── Deductions ──────────────────────────────────────────────────────────
    (
        r'מס\s+הכנסה|מס\s+הכנס',
        "deduction",
        "מס הכנסה",
        "ניכוי מס הכנסה לפי מדרגות המס הישראלי ונקודות הזיכוי שלך. מחושב על ברוטו חייב במס בניכוי נקודות הזיכוי.",
    ),
    (
        r'ביטוח\s+לאומי|ב\.?לאומ|ב\.ל\b|בטוח\s+לאומי',
        "deduction",
        "ביטוח לאומי (עובד)",
        "חלק העובד בדמי הביטוח הלאומי. מממן גמלאות: אבטלה, נכות, מחלה, אמהות ועוד. שיעור כ-7% משכר (בחלק הנמוך).",
    ),
    (
        r'מס\s+בריאות|ביטוח\s+בריאות',
        "deduction",
        "מס בריאות",
        "דמי ביטוח בריאות ממלכתי. מממן את קופות החולים ומאפשר קבלת שירותי בריאות. שיעור 3.1%–5% משכר.",
    ),
    (
        r'קופ\S{0,3}\s+גמל|ניכוי\s+לקופ|גמל\b',
        "deduction",
        "ניכוי קופת גמל",
        "ניכוי חלק העובד לקרן הפנסיה או קופת הגמל. מינימום 6% (מרכיב תגמולים עובד) שנחסך לטובת הפרישה שלך.",
    ),
    (
        r'פנסי[הה]|קרן\s+פנסי',
        "deduction",
        "ניכוי פנסיה (עובד)",
        "ניכוי חלק העובד לקרן הפנסיה. מינימום 6% משכר, נחסך לטובת קצבת הפרישה.",
    ),
    (
        r'השתלמות|קרן\s+ה?תשתלמות|קרן\s+השתלמ',
        "deduction",
        "ניכוי קרן השתלמות (עובד)",
        "חיסכון לטווח בינוני (ניתן למשיכה לאחר 6 שנים פטורה ממס). חלק עובד: 2.5% משכר, חלק מעסיק: 7.5%.",
    ),
    (
        r'הלוואה|החזר\s+הלוואה',
        "deduction",
        "החזר הלוואה",
        "ניכוי בגין החזר הלוואה שנלקחה מהמעסיק. בדוק את יתרת ההלוואה ותנאי ההחזר מול מחלקת השכר.",
    ),
    (
        r'עיקול|צו\s+עיקול',
        "deduction",
        "ניכוי עיקול",
        "ניכוי בגין צו עיקול שיפוטי. המעסיק מחויב לנכות ולהעביר לרשות המבצעת. בדוק את הצו מול גורם משפטי.",
    ),
    # ── Employer contributions ───────────────────────────────────────────────
    (
        r'הפרש\S{0,3}\s+מעסיק|פנסי\S{0,3}\s+מעסיק|תגמולי\s+מעסיק',
        "employer_contribution",
        "הפרשת מעסיק לפנסיה",
        "חלק המעסיק בקרן הפנסיה. מינימום 6.5%–7.5% משכר. כסף זה שייך לך ונצבר לטובת פרישה.",
    ),
    (
        r'השתלמות\s+מעסיק|קרן\s+השתלמות\s+מעסיק',
        "employer_contribution",
        "הפרשת מעסיק לקרן השתלמות",
        "חלק המעסיק לקרן השתלמות — 7.5% משכר. לאחר 6 שנים ניתן למשיכה פטורה ממס.",
    ),
    (
        r'פיצויים|קרן\s+פיצוי',
        "employer_contribution",
        "הפרשה לפיצויים",
        "הפרשת המעסיק לטובת פיצויי פיטורין — 8.33% משכר. כסף זה שמור על שמך ומגיע לך עם סיום העסקה.",
    ),
    (
        r'בריאות\s+מעסיק|ביטוח\s+חיים\s+מעסיק|ריסק\s+מעסיק',
        "employer_contribution",
        "ביטוח חיים (מעסיק)",
        "ביטוח חיים ונכות שמשלם המעסיק עבורך. מספק כיסוי למקרה מוות או נכות — ערך חשוב שלא תמיד מודעים לו.",
    ),
]

# ── Money-only regex for line-item amount detection ──────────────────────────
# Matches standard decimal amounts: 1,234.56 or 1234.56
_LI_AMOUNT_RE = re.compile(
    r'\b([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2})\b',
    re.UNICODE,
)
# Also match European-comma decimal amounts: "280,50" (OCR artifact for 280.50)
# Only treat NN,NN patterns as currency (2 digits after comma, 1-3 before → plausible amounts)
_LI_EURO_AMOUNT_RE = re.compile(
    r'(?<!\d)([1-9][0-9]{0,3}),([0-9]{2})(?!\d)',
    re.UNICODE,
)

# ── Minimum amount threshold ─────────────────────────────────────────────────
# Amounts below this are assumed to be codes, percentages, or days — not money.
_LI_MIN_AMOUNT = 5.0

# ── Lines to skip inside the table region ───────────────────────────────────
# These are header/separator rows or lines that are known summary/gross rows.
_LI_SKIP_LINE_RE = re.compile(
    r'^\s*$'                                          # blank
    r'|^[-=_|/\\]{3,}$'                               # separator
    r'|קוד\s+תיאור|תיאור\s+קוד'                      # table header
    r'|תעריף|ימים|שעות\s+עבודה'                       # column headers (not יחידות — may appear in fund rows)
    r'|ברוט[ון]\s+ל(?:מס|ב\.?ל|ביטו)'               # gross summary lines
    r'|נקודות\s+זיכוי|נקודות\s*[0-9]'               # tax credits lines
    r'|תשלומים\s+אחרים|תשלומ\S{0,3}\s+[0-9]'         # payment total lines
    r'|סה[\"\'״]?כ\s+ברוטו'                          # gross total
    r'|מצב\s+משפחתי|מרכיבים\s+ותשלומ'               # meta / header rows
    r'|ניכויים?\s+והפרש'                              # deductions & differences header
    r'|אחור\s+מס|אחרים\s+מס'                          # garbled "total other" lines
    r'|EEE|[A-Z]{5,}'                                  # long Latin-char runs (OCR garbage)
    r'|\d{8,}',                                        # very long digit sequences (codes/IDs)
    re.UNICODE | re.IGNORECASE,
)

# ── Noise filter: minimum Hebrew character density ───────────────────────────
# Lines with fewer than _LI_MIN_HEBREW_CHARS Hebrew characters AND less than
# _LI_MIN_HEBREW_RATIO of Hebrew vs total non-space chars are considered too
# noisy to be valid payslip rows (they're Tesseract garbage).
_LI_MIN_HEBREW_CHARS = 3
_LI_MIN_HEBREW_RATIO = 0.25   # 25% of non-space chars must be Hebrew for unknown items
# Known-keyword items bypass this ratio check (their Hebrew is the keyword itself)

# For unknown items, require at least one genuine Hebrew word (≥4 consecutive Hebrew chars).
# Prevents garbled OCR tokens from being treated as payslip rows.
_LI_HEBREW_WORD_RE = re.compile(r'[\u05d0-\u05ea]{4,}', re.UNICODE)

# ── Confidence for table-extracted line items ─────────────────────────────────
_LI_CONF_KNOWN   = 0.70   # keyword matched a known item
_LI_CONF_UNKNOWN = 0.35   # no keyword match — unknown item


def _sign_for_category(category: "LineItemCategory", abs_value: float) -> float:
    """
    Phase 10: Return the correctly-signed value for a given LineItemCategory.
    Deductions are stored as negative values; all other categories are positive.
    """
    from app.models.schemas import LineItemCategory as _LIC
    return -abs_value if category == _LIC.DEDUCTION else abs_value


def _count_hebrew(text: str) -> tuple[int, int]:
    """Return (hebrew_char_count, non_space_char_count) for text."""
    non_space = sum(1 for c in text if not c.isspace())
    hebrew = sum(1 for c in text if '\u05d0' <= c <= '\u05ea')
    return hebrew, non_space


def _line_amounts(line: str) -> list[float]:
    """
    Extract all plausible monetary amounts from a line.
    Handles both standard (1,234.56) and European-comma (280,50) formats.
    Returns a list sorted ascending by position in the line (left to right).
    """
    found: list[tuple[int, float]] = []  # (position, value)

    # Standard decimal amounts
    for m in _LI_AMOUNT_RE.finditer(line):
        val = float(m.group(1).replace(",", ""))
        if val >= _LI_MIN_AMOUNT:
            found.append((m.start(), val))

    # European-comma amounts (only if no standard amount overlaps)
    standard_spans = [m.span() for m in _LI_AMOUNT_RE.finditer(line)]
    for m in _LI_EURO_AMOUNT_RE.finditer(line):
        # Check for overlap with a standard amount
        start, end = m.start(), m.end()
        if any(s <= start < e or s < end <= e for s, e in standard_spans):
            continue
        val = float(f"{m.group(1)}.{m.group(2)}")
        if val >= _LI_MIN_AMOUNT:
            found.append((start, val))

    found.sort(key=lambda t: t[0])
    return [v for _, v in found]


# ---------------------------------------------------------------------------
# Phase 13: OCR description sanitization helper
# ---------------------------------------------------------------------------

# Compiled once at import time for efficiency.
# Matches isolated Latin-only "words" — these are Tesseract artifacts produced
# when reading Hebrew text that has been partially covered by redaction markers.
# Examples seen in real payslips: "DANN", "NAD", "DNN", "MAN", "ANN", "NN".
# We allow short Latin tokens that look like abbreviations (1-2 uppercase letters)
# only when surrounded by Hebrew, but strip anything ≥3 Latin chars or pure Latin.
_RE_LATIN_ARTIFACT = re.compile(
    r'\b[A-Za-z]{3,}\b',   # Latin word of 3+ characters (guaranteed artifact)
    re.ASCII,
)
# Dates in various formats inserted by Tesseract when reading table header cells
_RE_OCR_DATE_ARTIFACT = re.compile(
    r'\b\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}\b',
)
# Sequences of 4+ consecutive digits that are not amounts (numeric codes, IDs)
_RE_NUMERIC_CODE = re.compile(
    r'(?<![,\d])\d{4,}(?![,\d])',  # 4+ digits not adjacent to comma-separated amounts
)


def _sanitize_ocr_description(raw: str) -> str:
    """
    Strip common OCR artifacts from a raw OCR description string before it
    is stored as description_hebrew.

    Artifacts removed (in order):
      1. Latin words of 3+ characters (e.g., "DANN", "NAD", "DNN") — produced
         by Tesseract misreading redacted Hebrew text.
      2. Date strings in DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY format.
      3. Isolated numeric codes of 4+ digits not adjacent to decimal amounts.
      4. Resulting extra whitespace is collapsed.

    Hebrew characters, digits, punctuation, and short Latin abbreviations
    (1–2 chars) that might be legitimate (e.g., "VIP", currency codes) are
    preserved.

    Returns the sanitized string, or the original string if sanitization
    would make it empty.
    """
    cleaned = raw
    cleaned = _RE_LATIN_ARTIFACT.sub(" ", cleaned)
    cleaned = _RE_OCR_DATE_ARTIFACT.sub(" ", cleaned)
    cleaned = _RE_NUMERIC_CODE.sub(" ", cleaned)
    # Collapse multiple spaces / strip edges
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned).strip()
    # Safety: if the whole description was noise, return the original (truncated)
    if not cleaned or not re.search(r'[\u0590-\u05ff\d]', cleaned):
        return raw.strip()
    return cleaned


def _classify_line_item(
    line: str,
    item_index: int,
    page_index: int,
    default_category: "LineItemCategory | None" = None,
) -> "LineItem | None":
    """
    Try to classify one OCR table row as a LineItem.

    Steps:
      1. Skip blank / separator / header / summary lines.
      2. Try known-keyword matching (bypasses noise filter for labeled items).
      3. Noise filter: skip lines with too few Hebrew characters (for unknown items).
      4. Find the best monetary amount on the line using _line_amounts().
      5. If keyword matched: return labeled LineItem.
      6. If no keyword matches: return 'unknown' item (only when Hebrew present).
      7. Returns None if no money amount is found.

    Phase 10: default_category — when provided by the section-scanning engine,
    unknown items (step 6) are assigned this category instead of defaulting to
    EARNING. Known-keyword items always use the category from _LI_KNOWN_ITEMS.
    """
    from app.models.schemas import LineItem, LineItemCategory  # local import

    # Hard-reject patterns that ALWAYS apply (even to known-keyword lines).
    # These are summary/gross lines that may contain known Hebrew keywords but
    # represent aggregate values, not individual payslip row items.
    # e.g. "ברוטן למס הכנסה 6,463.00" matches "מס הכנסה" but is NOT an income tax row.
    _HARD_REJECT = re.compile(
        r'ברוט[ון]\s+ל(?:מס|ב\.?ל|ביטו)'   # gross-for-tax / gross-for-NI summary
        r'|סה["\u05d4]?כ\s+ברוטו'            # total gross
        r'|^\s*$|^[-=_|/\\]{3,}$',           # blank / separator
        re.UNICODE | re.IGNORECASE,
    )
    if _HARD_REJECT.search(line):
        return None

    # Step 2: check known keywords BEFORE skip filter.
    # Known items bypass skip-line patterns that might accidentally block valid rows
    # (e.g. קרן השתלמות row that contains "יחידות מס" — "יחידות" is a column-header token).
    matched_keyword: tuple | None = None
    for entry in _LI_KNOWN_ITEMS:
        keyword_pattern = entry[0]
        if re.search(keyword_pattern, line, re.UNICODE | re.IGNORECASE):
            matched_keyword = entry
            break

    # Skip-line check only applies to lines that didn't match a known keyword.
    if matched_keyword is None and _LI_SKIP_LINE_RE.search(line):
        return None

    # Step 3: noise filter — only applies to unknown items
    if matched_keyword is None:
        heb_count, non_space = _count_hebrew(line)
        if heb_count < _LI_MIN_HEBREW_CHARS:
            return None
        if non_space > 0 and (heb_count / non_space) < _LI_MIN_HEBREW_RATIO:
            return None
        # Require at least one genuine Hebrew word (≥3 consecutive Hebrew chars)
        # to distinguish real payslip rows from Tesseract garbage with scattered Hebrew chars.
        if not _LI_HEBREW_WORD_RE.search(line):
            return None

    # Step 4: find the best monetary amount on the line.
    # Israeli payslip tables have columns: DESCRIPTION | CODE | QTY | RATE | TOTAL
    # In RTL layout, TOTAL is rightmost. Tesseract's RTL reading can render these
    # in inconsistent order. Using the LARGEST amount is the most robust heuristic:
    # the total column is nearly always the largest value on the line.
    amounts = _line_amounts(line)
    if not amounts:
        return None

    # Use the largest amount found on the line (most robust for RTL table columns).
    # Edge case: if the largest is ≥ 4× bigger than the second largest, it may be
    # an annual accumulation column — in that case prefer the second largest.
    amounts_sorted = sorted(amounts, reverse=True)
    amount = amounts_sorted[0]
    if (len(amounts_sorted) >= 2
            and amounts_sorted[0] >= 4 * amounts_sorted[1]
            and amounts_sorted[0] > 1000):
        # Likely an annual accumulation — use the next largest
        amount = amounts_sorted[1]

    # Phase 8: attempt to extract quantity (כמות) and rate (תעריף) from multi-column rows.
    # Israeli payslip column order (RTL, as Tesseract renders it):
    #   TOTAL | RATE | QTY | CODE | DESCRIPTION
    # amounts_sorted[0] = total (largest), [1] = rate, [2] = qty.
    quantity: float | None = None
    rate_val: float | None = None
    if len(amounts_sorted) >= 3:
        # 3+ amounts: layout is [total, rate, qty]
        rate_candidate = amounts_sorted[1]
        qty_candidate  = amounts_sorted[2]
        # Sanity: qty must be in plausible range [0.01, 365]
        if 0.01 <= qty_candidate <= 365:
            quantity = qty_candidate
            rate_val = rate_candidate
    elif len(amounts_sorted) == 2:
        # 2 amounts: [total, X] — X could be qty or rate
        second = amounts_sorted[1]
        if second > 0 and amounts_sorted[0] > 0:
            implied_qty = amounts_sorted[0] / second
            if 0.01 <= implied_qty <= 365:
                quantity = round(implied_qty, 3)
                rate_val = second

    # Step 5: return labeled item if keyword matched
    if matched_keyword is not None:
        _kw_pattern, category_str, display_name, explanation = matched_keyword
        cat_map = {
            "earning": LineItemCategory.EARNING,
            "deduction": LineItemCategory.DEDUCTION,
            "employer_contribution": LineItemCategory.EMPLOYER_CONTRIBUTION,
            "benefit_in_kind": LineItemCategory.BENEFIT_IN_KIND,
            "balance": LineItemCategory.BALANCE,
        }
        category = cat_map[category_str]
        # Deductions are stored as negative values.
        value = -amount if category == LineItemCategory.DEDUCTION else amount
        return LineItem(
            id=f"li_ocr_{item_index}",
            category=category,
            description_hebrew=display_name,
            explanation_hebrew=explanation,
            value=value,
            raw_text=line.strip()[:120],
            confidence=_LI_CONF_KNOWN,
            page_index=page_index,
            is_unknown=False,
            quantity=quantity,
            rate=rate_val,
        )

    # Step 6: unknown item — build best-guess guesses from keywords present
    guesses: list[str] = []
    if re.search(r'ביטוח|בית', line, re.UNICODE | re.IGNORECASE):
        guesses.append("ביטוח כלשהו")
    if re.search(r'קרן|קופ', line, re.UNICODE | re.IGNORECASE):
        guesses.append("קרן / קופת חיסכון")
    if re.search(r'הפרש|תגמול', line, re.UNICODE | re.IGNORECASE):
        guesses.append("הפרשה מעסיק")
    if re.search(r'ניכ', line, re.UNICODE | re.IGNORECASE):
        guesses.append("ניכוי כלשהו")
    if not guesses:
        guesses = ["תשלום / ניכוי לא מזוהה"]

    return LineItem(
        id=f"li_ocr_unk_{item_index}",
        category=default_category or LineItemCategory.EARNING,   # Phase 10: section context wins; fallback EARNING
        description_hebrew=_sanitize_ocr_description(line[:80])[:40] or "שורה לא מזוהה",  # Phase 13: strip OCR artifacts
        explanation_hebrew=(
            "שורה זו לא זוהתה על-ידי המערכת. "
            "ייתכן שמדובר בתשלום חד-פעמי, תוספת חוזית מיוחדת, "
            "או רכיב שכר שאינו נפוץ. בדוק מול מחלקת השכר מהו רכיב זה."
        ),
        value=amount,
        raw_text=line.strip()[:120],
        confidence=_LI_CONF_UNKNOWN,
        page_index=page_index,
        is_unknown=True,
        unknown_guesses=guesses,
        unknown_question="מהו רכיב שכר זה ומה הוא מייצג? כיצד הוא מחושב?",
        quantity=quantity,
        rate=rate_val,
    )


def extract_line_items_ocr(
    pages_text: dict[int, str],
    adapter: "ProviderAdapter | None" = None,  # type: ignore[name-defined]
) -> "list[LineItem]":
    """
    Legacy entry point — delegates to extract_line_items_by_sections().

    Phase 10: The original anchor→collect→stop implementation has been replaced
    by the generic section-scanning engine in extract_line_items_by_sections().
    This wrapper is preserved for backward compatibility with existing tests and
    any external callers that reference extract_line_items_ocr by name.
    """
    return extract_line_items_by_sections(pages_text, adapter)


def extract_line_items_by_sections(
    pages_text: dict[int, str],
    adapter: "ProviderAdapter | None" = None,
) -> "list[LineItem]":
    """
    Phase 10: Universal section-scanning line-item extractor.

    Replaces the anchor→collect→stop pattern of extract_line_items_ocr() with a
    top-to-bottom page scan that detects ALL section headers and assigns a
    LineItemCategory to every row captured under each header.

    Algorithm (per page):
      1. Maintain current_section_def (SectionDef | None).
      2. For each line:
         a. If _line_amounts(line) is empty (no monetary amounts → likely a header):
            - Check the line (and `prev_line + " " + line` for OCR-split headers)
              against every SectionDef.header_patterns in adapter.SECTION_DEFINITIONS.
            - First match → set current_section_def to that SectionDef; skip row.
         b. If current_section_def is not None:
            - Call _classify_line_item(line, ..., default_category=section_category)
              to get enrichment (display name, explanation) from _LI_KNOWN_ITEMS.
            - Override the returned item's category and value sign to match the
              active section (section context always wins over keyword category).
            - Deduplicate by (description_hebrew, rounded_abs_value) and append.
         c. If current_section_def is None (no section header encountered yet):
            - Call _classify_line_item(line, ...) without default_category.
            - Only emit the item if it is NOT is_unknown (known-keyword fallback).
            - This preserves backward compat for layouts with no section headers.
      3. prev_line tracks the previous line for two-line sliding-window header check.

    The function preserves the same signature as extract_line_items_ocr() so it
    can be called as a drop-in replacement. extract_line_items_ocr() delegates here.

    Args:
        pages_text: {page_index: ocr_text} dict from ocr_file().
        adapter:    Provider-specific adapter. Falls back to GenericAdapter when None.

    Returns:
        list[LineItem], potentially empty.
    """
    from app.services.adapters import GenericAdapter as _GenericAdapter
    from app.models.schemas import LineItem, LineItemCategory

    _adapter = adapter if adapter is not None else _GenericAdapter()
    section_defs = _adapter.SECTION_DEFINITIONS

    # Map category string → enum (local, no circular import)
    _CAT_MAP: dict[str, LineItemCategory] = {
        "earning":               LineItemCategory.EARNING,
        "deduction":             LineItemCategory.DEDUCTION,
        "employer_contribution": LineItemCategory.EMPLOYER_CONTRIBUTION,
        "benefit_in_kind":       LineItemCategory.BENEFIT_IN_KIND,
        "balance":               LineItemCategory.BALANCE,
    }

    items: list[LineItem] = []
    seen: set[tuple[str, float]] = set()
    item_counter = 0

    for page_idx, text in pages_text.items():
        lines = text.splitlines()
        current_section_def = None   # SectionDef | None
        prev_line = ""

        for line in lines:
            # ── Step a: header detection ───────────────────────────────────
            # Only consider lines that have NO monetary amounts as potential headers.
            # A row like "ניכויי חובה 50.00" is a data row, not a section header.
            if not _line_amounts(line):
                matched_def = None
                # Check the line alone, then a two-line window (OCR split headers)
                for candidate in (line, prev_line + " " + line):
                    for sec_def in section_defs:
                        if any(p.search(candidate) for p in sec_def.header_patterns):
                            matched_def = sec_def
                            break
                    if matched_def is not None:
                        break
                if matched_def is not None:
                    current_section_def = matched_def
                    prev_line = line
                    continue   # header line itself is not a data row

            # ── Step b: inside a recognized section ───────────────────────
            if current_section_def is not None:
                section_category = _CAT_MAP.get(
                    current_section_def.category_str, LineItemCategory.EARNING
                )
                item = _classify_line_item(
                    line, item_counter, page_idx,
                    default_category=section_category,
                )
                if item is not None:
                    # Section context always determines category and value sign.
                    signed_value = _sign_for_category(
                        section_category, abs(item.value or 0)
                    )
                    item = item.model_copy(update={
                        "category": section_category,
                        "value": signed_value,
                    })
                    key = (item.description_hebrew, round(abs(signed_value), 0))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                        item_counter += 1

            # ── Step c: no section header seen yet — known-keyword fallback ──
            else:
                item = _classify_line_item(line, item_counter, page_idx)
                if item is not None and not item.is_unknown:
                    key = (item.description_hebrew, round(abs(item.value or 0), 0))
                    if key not in seen:
                        seen.add(key)
                        items.append(item)
                        item_counter += 1

            prev_line = line

    return items


# ---------------------------------------------------------------------------
# Phase 3: Generic block detection, YTD extraction, balance extraction
# ---------------------------------------------------------------------------

def detect_section_blocks(
    pages_text: dict[int, str],
    adapter: "ProviderAdapter",  # type: ignore[name-defined]
) -> "list[SectionBlock]":
    """
    Detect semantic sections in OCR text using adapter anchor patterns.

    Sections detected (section_type values):
      - "earnings_table"       → TABLE_START_PATTERNS anchor found
      - "deductions_section"   → TABLE_STOP_PATTERNS anchor found
      - "contributions_section"→ CONTRIBUTIONS_ANCHOR_PATTERNS anchor found
      - "ytd_section"          → YTD_ANCHOR_PATTERNS anchor found
      - "balances_section"     → BALANCE_ANCHOR_PATTERNS anchor found
      - "summary_box"          → SUMMARY_BOX_PATTERNS anchor found

    Falls back to one "page" block per non-empty page if no anchors found.
    raw_text_preview is always None (privacy — OCR text is never logged).

    Returns a list of SectionBlock objects (at least 1 if any text exists).
    """
    from app.models.schemas import SectionBlock
    _CONFIGS = [
        ("earnings_table",        "רכיבי שכר",          adapter.TABLE_START_PATTERNS),
        ("deductions_section",    "ניכויים",             adapter.TABLE_STOP_PATTERNS),
        ("contributions_section", "הפרשות מעסיק",        adapter.CONTRIBUTIONS_ANCHOR_PATTERNS),
        ("ytd_section",           "נתונים מצטברים",      adapter.YTD_ANCHOR_PATTERNS),
        ("balances_section",      "יתרות",               adapter.BALANCE_ANCHOR_PATTERNS),
        ("summary_box",           "תיבת סיכום",          adapter.SUMMARY_BOX_PATTERNS),
    ]
    blocks: list = []
    found: set[str] = set()

    for page_idx, text in pages_text.items():
        for sec_type, sec_name, patterns in _CONFIGS:
            if sec_type in found:
                continue  # already recorded this section from an earlier page
            for pat in patterns:
                if pat.search(text):
                    blocks.append(SectionBlock(
                        section_name=sec_name,
                        section_type=sec_type,
                        bbox_json=None,
                        page_index=page_idx,
                        raw_text_preview=None,
                    ))
                    found.add(sec_type)
                    break

    # Fallback: one block per non-empty page
    if not blocks:
        for page_idx, text in pages_text.items():
            if text.strip():
                blocks.append(SectionBlock(
                    section_name=f"עמוד {page_idx + 1}",
                    section_type="page",
                    bbox_json=None,
                    page_index=page_idx,
                    raw_text_preview=None,
                ))
    return blocks


def extract_ytd_ocr(
    pages_text: dict[int, str],
    adapter: "ProviderAdapter",  # type: ignore[name-defined]
) -> "YTDMetrics | None":
    """
    Extract year-to-date accumulated totals from the payslip YTD section.

    Returns None when no YTD anchor is found (most payslips don't have a YTD
    section, or it uses a layout we haven't yet seen).  Returns a YTDMetrics
    object (with at least one non-None field) when values are extracted.

    Privacy: reads only the joined text of all pages; never logs raw OCR output.
    """
    full_text = "\n".join(pages_text.values())

    # First check: is there even a YTD section anchor?
    if not any(p.search(full_text) for p in adapter.YTD_ANCHOR_PATTERNS):
        return None

    _FIELDS: list[tuple[str, re.Pattern]] = [
        ("gross_ytd",              re.compile(r'מצטבר\s+ברוטו[:\s]*([\d,\.]+)', re.UNICODE)),
        ("net_ytd",                re.compile(r'מצטבר\s+נטו[:\s]*([\d,\.]+)', re.UNICODE)),
        ("income_tax_ytd",         re.compile(r'מצטבר\s+מס\s+הכנסה[:\s]*([\d,\.]+)', re.UNICODE)),
        ("national_insurance_ytd", re.compile(r'מצטבר\s+ביטוח\s+לאומי[:\s]*([\d,\.]+)', re.UNICODE)),
        ("health_ytd",             re.compile(r'מצטבר\s+מס\s+בריאות[:\s]*([\d,\.]+)', re.UNICODE)),
        ("pension_ytd",            re.compile(r'מצטבר\s+פנסיה[:\s]*([\d,\.]+)', re.UNICODE)),
        ("training_fund_ytd",      re.compile(r'מצטבר\s+השתלמות[:\s]*([\d,\.]+)', re.UNICODE)),
    ]

    vals: dict[str, float] = {}
    for fname, pat in _FIELDS:
        m = pat.search(full_text)
        if m:
            v = _parse_number(m.group(1))
            if v is not None:
                vals[fname] = v

    if not vals:
        return None  # Anchor found but no parseable values — return None, not empty object

    from app.models.schemas import YTDMetrics
    return YTDMetrics(**vals, confidence=0.65)


def extract_balances_ocr(
    pages_text: dict[int, str],
    adapter: "ProviderAdapter",  # type: ignore[name-defined]
) -> "list[BalanceItem]":
    """
    Extract carry-forward balance items: vacation days, sick days, training-fund
    balance in ILS, etc.

    Returns an empty list when no balance patterns are found (common — many
    payslips don't include a balance section at all).

    Privacy: reads only the joined text of all pages; never logs raw OCR output.
    """
    full_text = "\n".join(pages_text.values())

    _PATTERNS: list[tuple[str, str, str, re.Pattern]] = [
        ("bal_vacation_days",   "יתרת ימי חופש",   "days",
         re.compile(r'יתרת\s+(?:ימי\s+)?חופש[:\s]*([\d]+\.?[\d]*)', re.UNICODE | re.IGNORECASE)),

        ("bal_sick_days",       "יתרת ימי מחלה",   "days",
         re.compile(r'יתרת\s+(?:ימי\s+)?מחלה[:\s]*([\d]+\.?[\d]*)', re.UNICODE | re.IGNORECASE)),

        ("bal_vacation_hours",  "יתרת שעות חופש",  "hours",
         re.compile(r'יתרת\s+שעות\s+חופש[:\s]*([\d]+\.?[\d]*)', re.UNICODE | re.IGNORECASE)),

        ("bal_training_fund",   "יתרת קרן השתלמות","ils",
         re.compile(r'יתרת\s+(?:קרן\s+)?השתלמות[:\s]*([\d,\.]+)', re.UNICODE | re.IGNORECASE)),
    ]

    from app.models.schemas import BalanceItem
    result: list[BalanceItem] = []

    for bid, name, unit, pat in _PATTERNS:
        m = pat.search(full_text)
        if m:
            v = _parse_number(m.group(1))
            if v is not None:
                result.append(BalanceItem(
                    id=bid,
                    name_hebrew=name,
                    balance_value=v,
                    unit=unit,
                    confidence=0.70,
                    raw_text=None,   # privacy: never store raw OCR text
                ))

    return result


# ---------------------------------------------------------------------------
# OCR debug preview helper
# ---------------------------------------------------------------------------

_OCR_KEYWORDS = ["נטו", "ברוטו", "מס הכנסה", "ביטוח לאומי", "מס בריאות", "נקודות זיכוי"]


def _build_ocr_debug_preview(pages_text: dict[int, str]) -> str:
    """
    Build a local-dev-only debug preview string from OCR output.
    Called only when DEBUG_OCR_PREVIEW=true AND transient=true.

    - Redacts digit sequences of 3+ digits → '***'
    - Includes total char count, first 30 non-empty lines, keyword hits
    - Truncated to max 2000 chars
    - Does NOT store full raw OCR text
    """
    full_text = "\n".join(pages_text.values())
    total_chars = len(full_text.strip())

    # Redact digit sequences of 3+ digits
    redacted = re.sub(r'\d{3,}', '***', full_text)

    # First 30 non-empty lines from redacted text
    lines = [ln for ln in redacted.splitlines() if ln.strip()][:30]

    # Keyword hit detection on original text
    hits = [kw for kw in _OCR_KEYWORDS if kw in full_text]

    preview = f"[OCR DEBUG] total_chars={total_chars}\n"
    preview += f"keywords_found={hits}\n"
    preview += "---\n"
    preview += "\n".join(lines)

    return preview[:2000]


# ---------------------------------------------------------------------------
# Phase 7: Gross/net fallback helper (pure, testable)
# ---------------------------------------------------------------------------

def _apply_gross_net_fallback(
    gross: "float | None",
    net: "float | None",
    gross_field_confidence: "float | None",
    net_field_confidence: "float | None",
    gross_taxable_value: "float | None",
    gross_taxable_confidence: "float | None",
    gross_ni_value: "float | None",
    gross_ni_confidence: "float | None",
    net_to_pay_value: "float | None",
    net_to_pay_confidence: "float | None",
    net_salary_value: "float | None",
    net_salary_confidence: "float | None",
    total_payments_other_value: "float | None" = None,
    total_payments_other_confidence: "float | None" = None,
) -> "tuple[float | None, float, str | None, float | None, float, str | None]":
    """
    Phase 7: Apply smart fallback when main gross/net extraction failed.

    Priority order for gross (Phase 12 update):
      1. total_payments_other — סה"כ תשלומים: the true employee gross pay
         (excludes tax-inflated gross_taxable which includes employer-side items)
      2. gross_taxable        — ברוטו למס הכנסה: tax gross (may be higher than actual pay)
      3. gross_ni             — ברוטו לביטוח לאומי

    Priority order for net:
      net_to_pay → net_salary  (confidence × 0.85 penalty)

    Returns:
      (resolved_gross, gross_confidence, gross_fallback_note,
       resolved_net,   net_confidence,   net_fallback_note)
    """
    # --- Gross ---
    if gross is None:
        if total_payments_other_value is not None and total_payments_other_confidence is not None:
            # Phase 12: prefer סה"כ תשלומים — it is the actual total pay to the employee
            resolved_gross: float | None = total_payments_other_value
            gross_confidence = round(total_payments_other_confidence * 0.85, 3)
            gross_fallback_note: str | None = 'ברוטו חושב מ-סה"כ תשלומים'
        elif gross_taxable_value is not None and gross_taxable_confidence is not None:
            resolved_gross = gross_taxable_value
            gross_confidence = round(gross_taxable_confidence * 0.85, 3)
            gross_fallback_note = "ברוטו חושב מ-ברוטו למס הכנסה"
        elif gross_ni_value is not None and gross_ni_confidence is not None:
            resolved_gross = gross_ni_value
            gross_confidence = round(gross_ni_confidence * 0.85, 3)
            gross_fallback_note = "ברוטו חושב מ-ברוטו לביטוח לאומי"
        else:
            resolved_gross = None
            gross_confidence = 0.0
            gross_fallback_note = None
    else:
        resolved_gross = gross
        gross_confidence = gross_field_confidence if gross_field_confidence is not None else 0.0
        gross_fallback_note = None

    # --- Net ---
    if net is None:
        if net_to_pay_value is not None and net_to_pay_confidence is not None:
            resolved_net: float | None = net_to_pay_value
            net_confidence = round(net_to_pay_confidence * 0.85, 3)
            net_fallback_note: str | None = "נטו חושב מ-נטו לתשלום"
        elif net_salary_value is not None and net_salary_confidence is not None:
            resolved_net = net_salary_value
            net_confidence = round(net_salary_confidence * 0.85, 3)
            net_fallback_note = "נטו חושב מ-שכר נטו"
        else:
            resolved_net = None
            net_confidence = 0.0
            net_fallback_note = None
    else:
        resolved_net = net
        net_confidence = net_field_confidence if net_field_confidence is not None else 0.0
        net_fallback_note = None

    return (
        resolved_gross, gross_confidence, gross_fallback_note,
        resolved_net,   net_confidence,   net_fallback_note,
    )


# ---------------------------------------------------------------------------
# OCR entry point
# ---------------------------------------------------------------------------

def parse_with_ocr(
    file_path: "str | Path",
    mime_type: str,
    answers=None,
    transient: bool = False,
) -> "ParsedSlipPayload":  # type: ignore[name-defined]
    """
    Top-level OCR parser entry point.

    1. Check OCR system deps via check_ocr_deps().
    2. Run ocr_file() to get {page_index: text}.
    3. If insufficient text extracted: return OCR_UNAVAILABLE payload.
    4. Otherwise: run same regex extraction pipeline as parse_pdf(),
       using OCR-scaled confidences (FIELD_PATTERNS_OCR).
    5. Return ParsedSlipPayload with parse_source="ocr".

    Must be called from a thread (via asyncio.to_thread) since pytesseract is sync.
    """
    from app.models.schemas import (
        Anomaly,
        LineItem,
        LineItemCategory,
        ParsedSlipPayload,
        SectionBlock,
        SlipMeta,
        SummaryTotals,
        TaxCreditsDetected,
    )
    # Inline imports to avoid circular dependency and import-time failures
    from app.services.ocr import check_ocr_deps, ocr_file

    # --- Dependency check ---
    available, missing = check_ocr_deps()
    if not available:
        logger.info(
            "OCR deps unavailable for %s (missing: %s) — returning OCR_UNAVAILABLE",
            file_path,
            missing,
        )
        return ParsedSlipPayload(
            slip_meta=SlipMeta(pay_month=None, provider_guess="unknown", confidence=0.0),
            summary=SummaryTotals(integrity_ok=True),
            line_items=[],
            anomalies=[],
            blocks=[],
            answers_applied=answers is not None,
            error_code="OCR_UNAVAILABLE",
            parse_source="ocr_unavailable",
        )

    # --- Run OCR ---
    pages_text = ocr_file(Path(str(file_path)), mime_type)

    if not pages_text or not has_text_layer(pages_text):
        total_chars = sum(len(t.strip()) for t in pages_text.values())
        logger.info(
            "OCR produced insufficient text for %s (%d chars) — returning OCR_UNAVAILABLE",
            file_path,
            total_chars,
        )
        return ParsedSlipPayload(
            slip_meta=SlipMeta(pay_month=None, provider_guess="unknown", confidence=0.0),
            summary=SummaryTotals(integrity_ok=True),
            line_items=[],
            anomalies=[],
            blocks=[],
            answers_applied=answers is not None,
            error_code="OCR_UNAVAILABLE",
            parse_source="ocr_unavailable",
        )

    # --- Case: OCR produced readable text — run extraction pipeline ---
    full_text = "\n".join(pages_text.values())
    logger.info(
        "OCR text layer for %s: %d chars across %d page(s)",
        file_path,
        len(full_text.strip()),
        len(pages_text),
    )

    # Phase 11.1: Debug — always print the first 500 OCR chars to the terminal
    # so we can see exactly what Tesseract read before the gatekeeper evaluates it.
    _ocr_preview = full_text.strip()[:500].replace("\n", "↵")
    logger.warning("OCR RAW PREVIEW (first 500 chars): %s", _ocr_preview)

    # Phase 11: Gatekeeper — reject non-payslip documents immediately
    if not is_valid_payslip(full_text):
        logger.warning("Gatekeeper: document rejected as non-payslip for %s", file_path)
        return ParsedSlipPayload(
            slip_meta=SlipMeta(pay_month=None, provider_guess="unknown", confidence=0.0),
            summary=SummaryTotals(integrity_ok=True),
            line_items=[],
            anomalies=[],
            blocks=[],
            answers_applied=answers is not None,
            error_code="INVALID_DOCUMENT",
            parse_source="ocr",
        )

    # Phase 14: LLM Intelligence Layer — attempt Gemini extraction first.
    # If GEMINI_API_KEY is set, send the OCR text to Gemini for structured JSON
    # extraction.  On any failure (missing key, network error, parse/validation error),
    # we log a warning and fall through to the existing regex pipeline below.
    try:
        from app.services.llm_parser import llm_extract
        logger.info(
            "LLM: attempting Gemini extraction for %s (%d chars)",
            Path(file_path).name,
            len(full_text),
        )
        _llm_payload = llm_extract(full_text, answers)
        logger.info(
            "LLM: extraction successful (%d line items, gross=%s, net=%s)",
            len(_llm_payload.line_items),
            _llm_payload.summary.gross,
            _llm_payload.summary.net,
        )
        return _llm_payload
    except Exception as _llm_exc:
        logger.warning(
            "LLM: extraction failed (%s) — falling back to regex pipeline",
            _llm_exc,
        )
        # Fall through to existing regex pipeline below

    # --- Debug preview (local dev only — never stored in prod) ---
    debug_preview: str | None = None
    if transient and os.environ.get("DEBUG_OCR_PREVIEW", "").lower() == "true":
        debug_preview = _build_ocr_debug_preview(pages_text)

    # Use OCR-scaled confidence patterns
    net_field = extract_field(pages_text, "net_pay", FIELD_PATTERNS_OCR["net_pay"])
    gross_field = extract_field(pages_text, "gross_pay", FIELD_PATTERNS_OCR["gross_pay"])
    # income_tax: filtered to reject table-row lines AND "ברוטו למס הכנסה" lines
    # (which contain "מס הכנסה" but represent gross_taxable, not the deduction)
    income_tax_field = extract_field_filtered(
        pages_text, "income_tax", FIELD_PATTERNS_OCR["income_tax"],
        reject_tokens=_INCOME_TAX_REJECT,
    )
    national_ins_field = extract_field(pages_text, "national_insurance", FIELD_PATTERNS_OCR["national_insurance"])
    health_field = extract_field(pages_text, "health_tax", FIELD_PATTERNS_OCR["health_tax"])
    credits_field = extract_field(pages_text, "tax_credits", FIELD_PATTERNS_OCR["tax_credits"])
    # Use OCR-specific month extractor (Hebrew month-word + typo tolerance), falls back to numeric
    pay_month_result = extract_pay_month_ocr(pages_text)
    provider_name, provider_conf = detect_provider(full_text)
    # Phase 3: resolve provider-specific adapter for section anchors
    from app.services.adapters import get_adapter as _get_adapter
    adapter = _get_adapter(provider_name)

    # New summary-box fields — direct regex extraction
    total_payments_other_field   = extract_field(pages_text, "total_payments_other",   FIELD_PATTERNS_OCR.get("total_payments_other", []))
    provident_funds_field        = extract_field(pages_text, "provident_funds_deduction", FIELD_PATTERNS_OCR.get("provident_funds_deduction", []))
    net_salary_field             = extract_field(pages_text, "net_salary",              FIELD_PATTERNS_OCR.get("net_salary", []))
    gross_taxable_field          = extract_field(pages_text, "gross_taxable",           FIELD_PATTERNS_OCR.get("gross_taxable", []))
    gross_ni_field               = extract_field(pages_text, "gross_ni",                FIELD_PATTERNS_OCR.get("gross_ni", []))

    # Three fields where OCR frequently splits label and amount across lines.
    # Each uses a field-specific extraction strategy.
    _mtt_direct      = extract_field(pages_text, "mandatory_taxes_total",  FIELD_PATTERNS_OCR.get("mandatory_taxes_total", []))
    _oded_direct     = extract_field(pages_text, "other_deductions",        FIELD_PATTERNS_OCR.get("other_deductions", []))
    _ntp_direct      = extract_field(pages_text, "net_to_pay",              FIELD_PATTERNS_OCR.get("net_to_pay", []))
    mandatory_taxes_total_field  = extract_mandatory_taxes_ocr(pages_text, _mtt_direct)
    other_deductions_field       = extract_other_deductions_ocr(pages_text, _oded_direct)
    # net_to_pay: returns (field, extra_notes) — extra_notes non-empty when value was computed
    net_to_pay_field, _ntp_extra_notes = extract_net_to_pay_ocr(
        pages_text, _ntp_direct, net_salary_field, other_deductions_field
    )

    # Resolve scalar values
    net = net_field.value if net_field else None
    gross = gross_field.value if gross_field else None

    # Phase 7: Smart fallback via pure helper — if main patterns failed, use summary-box equivalents.
    (
        gross, gross_confidence, _gross_fallback_note,
        net,   net_confidence,   _net_fallback_note,
    ) = _apply_gross_net_fallback(
        gross=gross,
        net=net,
        gross_field_confidence=gross_field.confidence if gross_field else None,
        net_field_confidence=net_field.confidence if net_field else None,
        gross_taxable_value=gross_taxable_field.value if gross_taxable_field else None,
        gross_taxable_confidence=gross_taxable_field.confidence if gross_taxable_field else None,
        gross_ni_value=gross_ni_field.value if gross_ni_field else None,
        gross_ni_confidence=gross_ni_field.confidence if gross_ni_field else None,
        net_to_pay_value=net_to_pay_field.value if net_to_pay_field else None,
        net_to_pay_confidence=net_to_pay_field.confidence if net_to_pay_field else None,
        net_salary_value=net_salary_field.value if net_salary_field else None,
        net_salary_confidence=net_salary_field.confidence if net_salary_field else None,
        total_payments_other_value=total_payments_other_field.value if total_payments_other_field else None,
        total_payments_other_confidence=total_payments_other_field.confidence if total_payments_other_field else None,
    )

    income_tax = income_tax_field.value if income_tax_field else None
    national_ins = national_ins_field.value if national_ins_field else None
    health = health_field.value if health_field else None
    credit_points = credits_field.value if credits_field else None
    pay_month = pay_month_result[0] if pay_month_result else None

    known_deductions = sum(d for d in [income_tax, national_ins, health] if d is not None)
    total_deductions = known_deductions if known_deductions > 0 else None

    integrity_ok, integrity_notes = _run_integrity_checks(
        gross, net, income_tax, national_ins, health
    )
    # Append any notes from net_to_pay computed fallback
    if _ntp_extra_notes:
        integrity_notes = integrity_notes + _ntp_extra_notes
    # Phase 7: Append transparency note when gross/net were filled from fallback fields
    if _gross_fallback_note:
        integrity_notes = integrity_notes + [_gross_fallback_note]
    if _net_fallback_note:
        integrity_notes = integrity_notes + [_net_fallback_note]

    # Build line items — Phase 2D.2: real table extraction + summary-box supplements
    # Step 1: Phase 10 — generic section-scanning engine replaces anchor→stop approach.
    line_items: list[LineItem] = extract_line_items_by_sections(pages_text, adapter)

    # Step 2: supplement with summary-box-derived deduction items if not already
    # captured from the earnings table (income_tax, national_ins, health).
    # These are always present in the summary box even when the table row was garbled.
    existing_descs = {li.description_hebrew for li in line_items}

    if income_tax_field and "מס הכנסה" not in existing_descs:
        line_items.append(LineItem(
            id="li_income_tax",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="מס הכנסה",
            explanation_hebrew="ניכוי מס הכנסה מחושב לפי מדרגות המס ונקודות הזיכוי שלך.",
            value=-(income_tax or 0),
            raw_text=income_tax_field.raw_text,
            confidence=income_tax_field.confidence,
            page_index=income_tax_field.source_page,
        ))
    if national_ins_field and "ביטוח לאומי (עובד)" not in existing_descs:
        line_items.append(LineItem(
            id="li_national_ins",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="ביטוח לאומי (עובד)",
            explanation_hebrew="ניכוי ביטוח לאומי חלק העובד — מממן גמלאות נכות, אבטלה ועוד.",
            value=-(national_ins or 0),
            raw_text=national_ins_field.raw_text,
            confidence=national_ins_field.confidence,
            page_index=national_ins_field.source_page,
        ))
    if health_field and "מס בריאות" not in existing_descs:
        line_items.append(LineItem(
            id="li_health",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="מס בריאות",
            explanation_hebrew="ניכוי מס בריאות המממן את קופות החולים.",
            value=-(health or 0),
            raw_text=health_field.raw_text,
            confidence=health_field.confidence,
            page_index=health_field.source_page,
        ))
    # Add provident-funds deduction from summary box if not in table
    if provident_funds_field and "ניכוי קופת גמל" not in existing_descs:
        line_items.append(LineItem(
            id="li_provident_funds",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="ניכוי קופת גמל",
            explanation_hebrew=(
                "ניכוי חלק העובד לקרן הפנסיה או קופת הגמל. "
                "מינימום 6% (מרכיב תגמולים עובד) שנחסך לטובת הפרישה שלך."
            ),
            value=-(provident_funds_field.value or 0),
            raw_text=provident_funds_field.raw_text,
            confidence=provident_funds_field.confidence,
            page_index=provident_funds_field.source_page,
        ))
    # Add other-deductions from summary box if present and not in table
    if other_deductions_field and other_deductions_field.value and other_deductions_field.value > 0:
        if "ניכויים שונים" not in existing_descs:
            line_items.append(LineItem(
                id="li_other_deductions",
                category=LineItemCategory.DEDUCTION,
                description_hebrew="ניכויים שונים",
                explanation_hebrew=(
                    "ניכויים שאינם מזוהים בקטגוריה ספציפית — עשויים לכלול "
                    "ביטוחים, הלוואות, ציוד, חניה, ועוד. "
                    "פנה למחלקת השכר לקבלת פירוט."
                ),
                value=-(other_deductions_field.value),
                raw_text=other_deductions_field.raw_text,
                confidence=other_deductions_field.confidence,
                page_index=other_deductions_field.source_page,
            ))
    # When income_tax + national_ins + health are all missing but mandatory_taxes_total
    # is known, add it as a combined "ניכויי חובה — מסים" deduction item.
    no_individual_taxes = (income_tax is None and national_ins is None and health is None)
    if (mandatory_taxes_total_field
            and mandatory_taxes_total_field.value
            and mandatory_taxes_total_field.value > 0
            and no_individual_taxes
            and "ניכויי חובה — מסים" not in existing_descs):
        line_items.append(LineItem(
            id="li_mandatory_taxes",
            category=LineItemCategory.DEDUCTION,
            description_hebrew="ניכויי חובה — מסים",
            explanation_hebrew=(
                "סך ניכויי המס המחויבים: מס הכנסה + ביטוח לאומי + מס בריאות. "
                "אלו הניכויים החוקיים שמנוכים מכל שכר עבודה בישראל. "
                "לפירוט נפרד לכל מס — בדוק את תלוש השכר ישירות."
            ),
            value=-(mandatory_taxes_total_field.value),
            raw_text=mandatory_taxes_total_field.raw_text,
            confidence=mandatory_taxes_total_field.confidence,
            page_index=mandatory_taxes_total_field.source_page,
        ))
    # Add gross / total-payments earning from summary box when not enough individual items.
    # When we have fewer than 5 earnings items, add the "סה״כ תשלומים" total as a summary row.
    # This ensures the פירוט מלא tab has enough items for a meaningful display.
    earning_items_count = sum(1 for li in line_items if li.category == LineItemCategory.EARNING)
    if (total_payments_other_field
            and total_payments_other_field.value
            and total_payments_other_field.value > 0
            and earning_items_count < 5
            and 'סה"כ תשלומים' not in existing_descs):
        line_items.append(LineItem(
            id="li_total_payments",
            category=LineItemCategory.EARNING,
            description_hebrew='סה"כ תשלומים',
            explanation_hebrew=(
                "סך כל רכיבי השכר המשולמים לעובד, כולל שכר בסיס, שעות נוספות, "
                "תוספות ותשלומים חד-פעמיים. נחשב כבסיס לחישוב ניכויים. "
                "סכום זה כולל את כל ההכנסות ממנו מנוכים מסים ותשלומים שונים."
            ),
            value=total_payments_other_field.value,
            raw_text=total_payments_other_field.raw_text,
            confidence=total_payments_other_field.confidence,
            page_index=total_payments_other_field.source_page,
        ))

    # Phase 9: back-fill summary scalars from extracted line items when pattern extraction failed.
    # This ensures income_tax / national_ins / health appear in summary cards even when
    # OCR summary-box patterns miss them but the earnings table extraction succeeded.
    if income_tax is None:
        _li_tax = next((li for li in line_items if li.id == "li_income_tax"
                        or "מס הכנסה" in getattr(li, "description_hebrew", "")), None)
        if _li_tax and _li_tax.value is not None:
            income_tax = abs(_li_tax.value)
    if national_ins is None:
        _li_ni = next((li for li in line_items
                       if li.id == "li_national_ins"
                       or "ביטוח לאומי" in getattr(li, "description_hebrew", "")
                       and getattr(li, "category", None) in (LineItemCategory.DEDUCTION, "deduction")), None)
        if _li_ni and _li_ni.value is not None:
            national_ins = abs(_li_ni.value)
    if health is None:
        _li_health = next((li for li in line_items
                           if li.id == "li_health"
                           or ("בריאות" in getattr(li, "description_hebrew", "") or "מס בריאות" in getattr(li, "description_hebrew", ""))
                           and getattr(li, "category", None) in (LineItemCategory.DEDUCTION, "deduction")), None)
        if _li_health and _li_health.value is not None:
            health = abs(_li_health.value)

    # Recompute known_deductions / total_deductions after any back-fill promotions above
    known_deductions = sum(d for d in [income_tax, national_ins, health] if d is not None)
    if known_deductions > 0:
        total_deductions = known_deductions

    # Phase 4: resolve net_salary / net_to_pay scalars for extended anomaly checks
    _net_salary_val  = net_salary_field.value  if net_salary_field  else None
    _net_to_pay_val  = net_to_pay_field.value  if net_to_pay_field  else None
    # Phase 7.1: pass gross_taxable and provident_funds_deduction for Section 45a pension credit
    _gross_taxable_val      = gross_taxable_field.value      if gross_taxable_field      else None
    _provident_funds_val    = provident_funds_field.value    if provident_funds_field     else None
    # Phase 8: pass gross_ni for exact NI/health bracket computation in Rules G/H
    _gross_ni_val           = gross_ni_field.value           if gross_ni_field           else None

    anomalies: list[Anomaly] = _build_anomalies_from_real_data(  # type: ignore[assignment]
        gross, net, integrity_ok, integrity_notes,
        income_tax=income_tax,
        national_ins=national_ins,
        health=health,
        credit_points=credit_points,
        net_salary=_net_salary_val,
        net_to_pay=_net_to_pay_val,
        line_items=line_items,
        answers=answers,
        gross_taxable=_gross_taxable_val,
        provident_funds_deduction=_provident_funds_val,
        gross_ni=_gross_ni_val,
    )

    extracted_confs = [f.confidence for f in [net_field, gross_field] if f is not None]
    meta_conf = (sum(extracted_confs) / len(extracted_confs)) if extracted_confs else 0.5
    slip_conf = round(provider_conf * meta_conf, 2) if provider_conf > 0 else round(meta_conf * 0.4, 2)

    tax_credits = None
    if credit_points is not None and credits_field is not None:
        tax_credits = TaxCreditsDetected(
            credit_points_detected=credit_points,
            estimated_monthly_value=round(credit_points * CREDIT_POINT_VALUE, 0),
            confidence=credits_field.confidence,
            notes=[f"זוהו {credit_points} נקודות זיכוי בתלוש (OCR)"],
        )

    # Phase 3: detect semantic section blocks using provider adapter
    blocks: list[SectionBlock] = detect_section_blocks(pages_text, adapter)

    # Phase 3: extract YTD metrics and balance items
    ytd = extract_ytd_ocr(pages_text, adapter)
    balances = extract_balances_ocr(pages_text, adapter)

    return ParsedSlipPayload(
        slip_meta=SlipMeta(
            pay_month=pay_month,
            provider_guess=provider_name or "unknown",
            confidence=slip_conf,
            employer_name=None,
            employee_name_redacted=True,
        ),
        summary=SummaryTotals(
            gross=gross,
            gross_confidence=gross_confidence,
            net=net,
            net_confidence=net_confidence,
            total_deductions=total_deductions,
            total_employer_contributions=None,
            income_tax=income_tax,
            national_insurance=national_ins,
            health_insurance=health,
            pension_employee=provident_funds_field.value if provident_funds_field else None,  # Phase 9: promote provident_funds → pension summary card
            integrity_ok=integrity_ok,
            integrity_notes=integrity_notes,
            total_payments_other=total_payments_other_field.value if total_payments_other_field else None,
            mandatory_taxes_total=mandatory_taxes_total_field.value if mandatory_taxes_total_field else None,
            provident_funds_deduction=provident_funds_field.value if provident_funds_field else None,
            other_deductions=other_deductions_field.value if other_deductions_field else None,
            net_salary=net_salary_field.value if net_salary_field else None,
            net_to_pay=net_to_pay_field.value if net_to_pay_field else None,
            gross_taxable=gross_taxable_field.value if gross_taxable_field else None,
            gross_ni=gross_ni_field.value if gross_ni_field else None,
            credit_points=credit_points,
        ),
        line_items=line_items,
        anomalies=anomalies,
        insights=_build_insights(
            gross, net, income_tax, national_ins, health,
            provident_funds_field.value if provident_funds_field else None,
            credit_points, line_items,
        ),
        blocks=blocks,
        tax_credits_detected=tax_credits,
        answers_applied=answers is not None,
        error_code=None,
        parse_source="ocr",
        ocr_debug_preview=debug_preview,
        ytd=ytd,
        balances=balances,
    )
