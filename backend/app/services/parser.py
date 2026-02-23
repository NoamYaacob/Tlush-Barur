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
    ],
    "gross_pay": [
        (r'ברוטו\s+לצורך\s+מס[:\s]*₪?\s*([\d,]+\.?\d*)', CONFIDENCE_EXACT),
        (r'סה["\u05d4]\u05db\s+ברוטו[:\s]*₪?\s*([\d,]+\.?\d*)',  CONFIDENCE_EXACT),
        (r'ברוטו[:\s]+₪?\s*([\d,]+\.?\d*)',               CONFIDENCE_AMBIGUOUS),
        (r'ברוטו\s+([\d,]+\.?\d*)',                        CONFIDENCE_AMBIGUOUS),
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
    ],
    "gross_pay": [
        # "ברוטו למס הכנסה" or "ברוטו למס" (no strict punctuation)
        (r'ברוטו\s+למס\s*הכנסה?\s*[:\-]?\s*([0-9][0-9,\.]+)', _CONF_OCR_EXACT),
        # "ברוטו לצורך" with garbled מס
        (r'ברוטו\s+לצורך\s*\S{0,4}\s*([0-9][0-9,\.]+)',        _CONF_OCR_AMBIGUOUS),
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
        # "נקודות זיכוי" with spaces/typos
        (r'נקודו\S{0,3}\s+זיכו\S{0,2}\s*[:\-]?\s*([0-9]+\.?[0-9]*)', _CONF_OCR_EXACT),
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
    Return True if total stripped character count across all pages >= TEXT_MIN_CHARS.
    Avoids false positives from PDFs that have only whitespace/newlines as text.
    """
    total = sum(len(t.strip()) for t in pages_text.values())
    return total >= TEXT_MIN_CHARS


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


def _build_anomalies_from_real_data(
    gross: float | None,
    net: float | None,
    integrity_ok: bool,
    integrity_notes: list[str],
) -> list:
    """
    Build Anomaly objects for integrity failures detected from real extracted values.
    Returns an empty list when integrity is OK.
    """
    from app.models.schemas import Anomaly, AnomalySeverity

    if integrity_ok or gross is None or net is None:
        return []

    return [
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
    ]


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

    # Build anomalies from real integrity check
    anomalies: list[Anomaly] = _build_anomalies_from_real_data(  # type: ignore[assignment]
        gross, net, integrity_ok, integrity_notes
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
        blocks=blocks,
        tax_credits_detected=tax_credits,
        answers_applied=answers is not None,
        error_code=None,
        parse_source="pdf_text_layer",
    )


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

    # --- Debug preview (local dev only — never stored in prod) ---
    debug_preview: str | None = None
    if transient and os.environ.get("DEBUG_OCR_PREVIEW", "").lower() == "true":
        debug_preview = _build_ocr_debug_preview(pages_text)

    # Use OCR-scaled confidence patterns
    net_field = extract_field(pages_text, "net_pay", FIELD_PATTERNS_OCR["net_pay"])
    gross_field = extract_field(pages_text, "gross_pay", FIELD_PATTERNS_OCR["gross_pay"])
    income_tax_field = extract_field(pages_text, "income_tax", FIELD_PATTERNS_OCR["income_tax"])
    national_ins_field = extract_field(pages_text, "national_insurance", FIELD_PATTERNS_OCR["national_insurance"])
    health_field = extract_field(pages_text, "health_tax", FIELD_PATTERNS_OCR["health_tax"])
    credits_field = extract_field(pages_text, "tax_credits", FIELD_PATTERNS_OCR["tax_credits"])
    # Use OCR-specific month extractor (Hebrew month-word + typo tolerance), falls back to numeric
    pay_month_result = extract_pay_month_ocr(pages_text)
    provider_name, provider_conf = detect_provider(full_text)

    # Resolve scalar values
    net = net_field.value if net_field else None
    gross = gross_field.value if gross_field else None
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

    # Build line items
    line_items: list[LineItem] = []
    if gross_field:
        line_items.append(LineItem(
            id="li_gross",
            category=LineItemCategory.EARNING,
            description_hebrew="ברוטו",
            explanation_hebrew="סך השכר ברוטו כפי שנקרא מהתלוש באמצעות OCR.",
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

    anomalies: list[Anomaly] = _build_anomalies_from_real_data(  # type: ignore[assignment]
        gross, net, integrity_ok, integrity_notes
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

    # Section blocks: one per page; raw_text_preview=None for privacy (OCR output not logged)
    blocks: list[SectionBlock] = [
        SectionBlock(
            section_name=f"עמוד {page_idx + 1}",
            bbox_json=None,
            page_index=page_idx,
            raw_text_preview=None,  # intentionally omitted — OCR text is privacy-sensitive
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
        blocks=blocks,
        tax_credits_detected=tax_credits,
        answers_applied=answers is not None,
        error_code=None,
        parse_source="ocr",
        ocr_debug_preview=debug_preview,
    )
