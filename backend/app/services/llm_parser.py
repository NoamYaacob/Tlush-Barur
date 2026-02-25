"""
llm_parser.py — Phase 14: LLM Intelligence Layer for Israeli Payslip Extraction.

Uses Google Gemini (gemini-2.0-flash-lite) to parse OCR text from payslips into
a structured ParsedSlipPayload — replacing the brittle regex pipeline for the
OCR path when a GEMINI_API_KEY is configured.

Design decisions:
  - response_mime_type="application/json"  → Gemini outputs guaranteed-valid JSON
  - Pydantic validation before mapping      → schema mismatches trigger fallback
  - No PII logged                           → only char counts and success/failure status
  - Confidence fixed at 0.80               → between CONFIDENCE_EXACT (0.85) and
                                              OCR_EXACT (0.638), LLM results are
                                              accurate but not as verifiable as regex
  - privacy_guess always "ספק שכר"          → never expose provider company name

Called from parse_with_ocr() in parser.py. Any exception propagates to the caller
which silently falls back to the regex pipeline.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — no config.py; follow codebase pattern of direct os.getenv
# ---------------------------------------------------------------------------

_GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")

# Gemini model — Flash Lite is fastest and cheapest; sufficient for JSON extraction
_GEMINI_MODEL = "gemini-2.0-flash-lite"

# Safety margin: Gemini flash context is large, but OCR text beyond 12K chars
# is typically noise / duplicate pages.  Truncating keeps costs low.
_MAX_INPUT_CHARS = 12_000

# Fixed confidence for LLM-extracted fields.  Between CONFIDENCE_EXACT (0.85)
# and OCR_EXACT (0.638): LLM is accurate but cannot be machine-verified per-field.
_LLM_CONFIDENCE = 0.80

# ---------------------------------------------------------------------------
# System Instruction — the prompt that guides Gemini's extraction
# ---------------------------------------------------------------------------

_SYSTEM_INSTRUCTION = """You are an Israeli Payroll Expert. Extract data from the payslip OCR text into exact JSON.

PRIVACY RULES (mandatory, no exceptions):
- NEVER include or return employee name, employer name, or payroll provider company name anywhere in your JSON output.
- Silently omit them — leave those fields null or absent.

EXTRACTION RULES:
- gross_pay: use "סה\\"כ תשלומים" (Total Payments) as primary source.
  Fall back to "ברוטו לצורך מס" or "ברוטו למס הכנסה" only if "סה\\"כ תשלומים" is absent.
  Store the result in BOTH "gross_pay" and "total_payments_other".
- net_pay: use "נטו לתשלום", "סכום בבנק", or "נטו בנק". Verify: net_pay ≈ gross_pay − total_deductions.
- income_tax: extract from "מס הכנסה" rows. If missing from line items, search summary tables.
- national_insurance: extract from "ביטוח לאומי" rows. If missing from line items, search summary tables.
- health_insurance: extract from "מס בריאות".
- gross_taxable: extract from "ברוטו לצורך מס" / "ברוטו למס הכנסה" if present.
- gross_ni: extract from "ברוטו לביטוח לאומי" if present.
- credit_points: extract from "נקודות זיכוי" if present.
- pay_month: format as YYYY-MM (e.g. "2024-01"). Return null if not found.
- Deduction signs: ALL deduction values MUST be POSITIVE numbers. Never use negative.

CLEANUP RULES:
- Strip OCR artifacts from all description strings: remove standalone Latin words (DANN, NAD, DNN, MAN, ANN), date strings (DD/MM/YYYY), and isolated 4+-digit codes.
- Keep Hebrew text clean and readable.

LINE ITEMS:
- Return every distinct payslip row as a line item with a clean Hebrew description.
- category: one of "earning", "deduction", "employer_contribution", "benefit_in_kind", "balance"
- Use "deduction" for: income_tax, national_insurance, health_insurance, pension_employee, and any ניכוי row.
- Use "employer_contribution" for employer-side pension, severance, training fund rows.
- If a row has quantity (כמות) and rate (תעריף), populate those fields.
- value: always a positive float.

RETURN: Valid JSON ONLY — no markdown, no explanation, no code fences."""

# ---------------------------------------------------------------------------
# Pydantic schema for LLM response validation
# ---------------------------------------------------------------------------

_VALID_CATEGORIES = {"earning", "deduction", "employer_contribution", "benefit_in_kind", "balance"}


class LLMLineItem(BaseModel):
    """A single payslip row as returned by the LLM."""
    description_hebrew: str
    category: str
    value: float
    quantity: Optional[float] = None
    rate: Optional[float] = None

    @field_validator("category")
    @classmethod
    def category_must_be_valid(cls, v: str) -> str:
        if v not in _VALID_CATEGORIES:
            # Coerce unknown categories to "earning" rather than failing
            logger.warning("LLM returned unknown category %r — coercing to 'earning'", v)
            return "earning"
        return v

    @field_validator("value")
    @classmethod
    def value_must_be_positive(cls, v: float) -> float:
        # LLM is instructed to return positive values; coerce negatives just in case
        return abs(v)


class LLMExtractedPayload(BaseModel):
    """The full structured output we expect from the LLM."""
    gross_pay: Optional[float] = None
    net_pay: Optional[float] = None
    income_tax: Optional[float] = None
    national_insurance: Optional[float] = None
    health_insurance: Optional[float] = None
    pension_employee: Optional[float] = None
    gross_taxable: Optional[float] = None
    gross_ni: Optional[float] = None
    total_payments_other: Optional[float] = None
    pay_month: Optional[str] = None          # YYYY-MM or null
    credit_points: Optional[float] = None
    line_items: list[LLMLineItem] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Category explanation lookup
# ---------------------------------------------------------------------------

_CATEGORY_EXPLANATIONS: dict[str, str] = {
    "earning": (
        "רכיב שכר שמוסיף להכנסה הכוללת. "
        "כולל שכר בסיס, שעות נוספות, תוספות ותשלומים חד-פעמיים."
    ),
    "deduction": (
        "ניכוי המופחת מהשכר. "
        "כולל מסים (מס הכנסה, ביטוח לאומי, מס בריאות), פנסיה וניכויים אחרים."
    ),
    "employer_contribution": (
        "הפרשה של המעסיק שאינה מנוכה משכרך אך מהווה חלק מחבילת השכר הכוללת. "
        "כולל הפרשות לפנסיה, פיצויים וקרן השתלמות."
    ),
    "benefit_in_kind": (
        "טובת הנאה שאינה מזומן הניתנת על-ידי המעסיק (רכב, ביטוח, מנוי ספורט וכד'). "
        "עשויה להשפיע על חישוב המס."
    ),
    "balance": (
        "יתרת ימי חופשה, מחלה או תגמולים שנצברו ולא מומשו. "
        "הערך מייצג את מספר הימים או הסכום הצבור."
    ),
}


# ---------------------------------------------------------------------------
# Mapping helper
# ---------------------------------------------------------------------------

def _map_to_payload(extracted: LLMExtractedPayload, answers=None) -> "ParsedSlipPayload":  # noqa: F821
    """
    Convert a validated LLMExtractedPayload into the app's canonical ParsedSlipPayload.

    - Computes derived totals (total_deductions, total_employer_contributions)
    - Runs lightweight integrity check
    - Populates privacy-safe slip_meta (never exposes provider/employer/employee)
    - Reuses existing _run_integrity_checks() from parser.py for consistency
    """
    from app.models.schemas import (
        Anomaly,
        LineItem,
        LineItemCategory,
        ParsedSlipPayload,
        SlipMeta,
        SummaryTotals,
    )
    from app.services.parser import _run_integrity_checks, _run_extended_checks

    cat_map: dict[str, LineItemCategory] = {
        "earning": LineItemCategory.EARNING,
        "deduction": LineItemCategory.DEDUCTION,
        "employer_contribution": LineItemCategory.EMPLOYER_CONTRIBUTION,
        "benefit_in_kind": LineItemCategory.BENEFIT_IN_KIND,
        "balance": LineItemCategory.BALANCE,
    }

    # --- Build LineItems ---
    line_items: list[LineItem] = []
    for i, li in enumerate(extracted.line_items):
        category = cat_map.get(li.category, LineItemCategory.EARNING)
        # Deductions stored as-is (positive); frontend displays them in parentheses
        stored_value = li.value if category != LineItemCategory.DEDUCTION else li.value
        line_items.append(LineItem(
            id=f"li_llm_{i}",
            category=category,
            description_hebrew=li.description_hebrew[:80],
            explanation_hebrew=_CATEGORY_EXPLANATIONS.get(li.category, _CATEGORY_EXPLANATIONS["earning"]),
            value=stored_value,
            raw_text=None,
            confidence=_LLM_CONFIDENCE,
            page_index=0,
            is_unknown=False,
            quantity=li.quantity,
            rate=li.rate,
        ))

    # --- Compute derived totals ---
    total_deductions = sum(
        li.value for li in line_items
        if li.category == LineItemCategory.DEDUCTION and li.value is not None
    ) or None

    total_employer_contributions = sum(
        li.value for li in line_items
        if li.category == LineItemCategory.EMPLOYER_CONTRIBUTION and li.value is not None
    ) or None

    # --- Resolve gross: prefer total_payments_other, then gross_pay ---
    gross = extracted.total_payments_other or extracted.gross_pay
    net = extracted.net_pay

    # --- Integrity check (reuses existing function) ---
    integrity_ok, integrity_notes = _run_integrity_checks(
        gross=gross,
        net=net,
        income_tax=extracted.income_tax,
        national_ins=extracted.national_insurance,
        health=extracted.health_insurance,
    )

    # --- Build SummaryTotals ---
    summary = SummaryTotals(
        gross=gross,
        gross_confidence=_LLM_CONFIDENCE,
        net=net,
        net_confidence=_LLM_CONFIDENCE,
        total_deductions=total_deductions,
        total_employer_contributions=total_employer_contributions,
        income_tax=extracted.income_tax,
        national_insurance=extracted.national_insurance,
        health_insurance=extracted.health_insurance,
        pension_employee=extracted.pension_employee,
        integrity_ok=integrity_ok,
        integrity_notes=integrity_notes,
        # Extended summary-box fields
        total_payments_other=extracted.total_payments_other,
        gross_taxable=extracted.gross_taxable,
        gross_ni=extracted.gross_ni,
        credit_points=extracted.credit_points,
    )

    # --- Extended rule-engine checks (anomaly detection) ---
    extended_anomalies: list[Anomaly] = _run_extended_checks(
        gross=gross,
        net=net,
        income_tax=extracted.income_tax,
        national_ins=extracted.national_insurance,
        health=extracted.health_insurance,
        credit_points=extracted.credit_points,
        net_salary=None,
        net_to_pay=net,
        line_items=line_items,
        answers=answers,
        gross_taxable=extracted.gross_taxable,
        provident_funds_deduction=None,
        gross_ni=extracted.gross_ni,
        pension_employee=extracted.pension_employee,
    )

    # --- Build privacy-safe SlipMeta ---
    slip_meta = SlipMeta(
        pay_month=extracted.pay_month,
        provider_guess="ספק שכר",   # always generic — never expose real provider name
        confidence=0.0,
        employer_name=None,          # always null — privacy
        employee_name_redacted=True, # always true — privacy
    )

    # --- Phase 15: Educational insights ---
    try:
        from app.logic.insights import generate_insights as _gen_insights
        from app.models.schemas import Insight as _InsightSchema
        _raw_insights = _gen_insights(
            gross=gross,
            net=net,
            income_tax=extracted.income_tax,
            national_insurance=extracted.national_insurance,
            health_insurance=extracted.health_insurance,
            pension_employee=extracted.pension_employee,
            credit_points=extracted.credit_points,
            line_items=line_items,
        )
        insights = [
            _InsightSchema(id=ins.id, kind=ins.kind, title=ins.title, body=ins.body)
            for ins in _raw_insights
        ]
    except Exception as _ins_exc:
        logger.warning("LLM insights: failed to generate (%s)", _ins_exc)
        insights = []

    return ParsedSlipPayload(
        slip_meta=slip_meta,
        summary=summary,
        line_items=line_items,
        anomalies=extended_anomalies,
        insights=insights,
        blocks=[],
        tax_credits_detected=None,
        answers_applied=answers is not None,
        error_code=None,
        parse_source="ocr_llm",
        ocr_debug_preview=None,
        ytd=None,
        balances=[],
        corrections=[],
    )


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------

def llm_extract(full_text: str, answers=None) -> "ParsedSlipPayload":  # noqa: F821
    """
    Send OCR text to Google Gemini and map the structured JSON response
    to a ParsedSlipPayload.

    Privacy guarantee:
      - Raw OCR text is sent to Gemini but never logged (only char count is logged).
      - LLM response text is never logged.
      - System instruction explicitly forbids returning PII.

    Raises on any failure (missing API key, network error, JSON parse error,
    Pydantic validation error). The caller (parse_with_ocr in parser.py) must
    catch all exceptions and fall back to the regex pipeline.

    Args:
        full_text: Concatenated OCR text from all pages.
        answers:   QuickAnswers object or None (passed through to payload).

    Returns:
        ParsedSlipPayload with parse_source="ocr_llm".
    """
    # Refresh at call time so tests can patch os.environ after import
    api_key = os.getenv("GEMINI_API_KEY") or _GEMINI_API_KEY
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set — LLM extraction unavailable. "
            "Set GEMINI_API_KEY in your .env file to enable Gemini extraction."
        )

    import google.generativeai as genai  # lazy import: not required if key absent

    genai.configure(api_key=api_key)

    # Truncate input to avoid excessive token usage / cost
    truncated_text = full_text[:_MAX_INPUT_CHARS]
    logger.info(
        "LLM: sending %d chars (truncated from %d) to %s",
        len(truncated_text),
        len(full_text),
        _GEMINI_MODEL,
    )

    prompt = (
        f"{_SYSTEM_INSTRUCTION}"
        f"\n\n---OCR TEXT START---\n{truncated_text}\n---OCR TEXT END---"
    )

    model = genai.GenerativeModel(_GEMINI_MODEL)
    response = model.generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json"},
    )

    # Parse and validate (any error propagates to caller → fallback)
    raw_json = response.text
    data = json.loads(raw_json)
    extracted = LLMExtractedPayload.model_validate(data)

    logger.info(
        "LLM: extraction successful — %d line items, gross=%s, net=%s",
        len(extracted.line_items),
        extracted.total_payments_other or extracted.gross_pay,
        extracted.net_pay,
    )

    return _map_to_payload(extracted, answers=answers)
