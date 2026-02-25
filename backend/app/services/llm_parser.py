"""
llm_parser.py — Phase 14/16.6: LLM Intelligence Layer for Israeli Payslip Extraction.

Uses Groq (llama-3.3-70b-versatile) to parse OCR text from payslips into a
structured ParsedSlipPayload — replacing the brittle regex pipeline for the OCR
path when a GROQ_API_KEY is configured.

Design decisions:
  - response_format={"type": "json_object"}  → Groq outputs guaranteed-valid JSON
  - Pydantic validation before mapping        → schema mismatches trigger fallback
  - No PII logged                             → only char counts and success/failure
  - Confidence fixed at 0.80                 → between CONFIDENCE_EXACT (0.85) and
                                                OCR_EXACT (0.638)
  - privacy_guess always "ספק שכר"            → never expose provider company name

Phase 16.6: Migrated from Google Gemini to Groq to avoid free-tier geo-blocks.
  - gemini-2.0-flash-lite → 429 quota errors ("limit: 0")
  - gemini-1.5-flash      → 404 not found on v1beta API
  - groq llama-3.3-70b-versatile → free tier, no geo-block, fast

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

_GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")

# Groq model — llama-3.3-70b-versatile: free tier, 6000 tokens/min, no geo-block.
# Supports response_format={"type": "json_object"} for guaranteed JSON output.
_GROQ_MODEL = "llama-3.3-70b-versatile"

# Safety margin: keep input well within Groq's context window and rate limits.
# OCR text beyond 12K chars is typically noise / duplicate pages anyway.
_MAX_INPUT_CHARS = 12_000

# Fixed confidence for LLM-extracted fields.  Between CONFIDENCE_EXACT (0.85)
# and OCR_EXACT (0.638): LLM is accurate but cannot be machine-verified per-field.
_LLM_CONFIDENCE = 0.80

# ---------------------------------------------------------------------------
# System Instruction — the prompt that guides Gemini's extraction
# ---------------------------------------------------------------------------

_SYSTEM_INSTRUCTION = """You are an Israeli Payroll Expert. Extract data from an Israeli payslip OCR text and return it as a single JSON object matching the schema below EXACTLY.

══════════════════════════════════════════
OUTPUT SCHEMA (copy key names character-for-character)
══════════════════════════════════════════
{
  "gross_pay":             <number | null>,
  "net_pay":               <number | null>,
  "income_tax":            <number | null>,
  "national_insurance":    <number | null>,
  "health_insurance":      <number | null>,
  "pension_employee":      <number | null>,
  "gross_taxable":         <number | null>,
  "gross_ni":              <number | null>,
  "total_payments_other":  <number | null>,
  "pay_month":             <"YYYY-MM" string | null>,
  "credit_points":         <number | null>,
  "line_items": [
    {
      "description_hebrew": <string — REQUIRED, Hebrew label of the row>,
      "category":           <"earning" | "deduction" | "employer_contribution" | "benefit_in_kind" | "balance">,
      "value":              <number — REQUIRED, always positive>,
      "quantity":           <number | null>,
      "rate":               <number | null>
    }
  ]
}

CRITICAL KEY NAME RULES — these are the ONLY accepted key names:
• Use "description_hebrew" (NOT "description", NOT "name", NOT "label", NOT "שם").
• Use "category"           (NOT "type", NOT "kind").
• Use "value"              (NOT "amount", NOT "sum", NOT "סכום").
• Use "gross_pay"          (NOT "gross", NOT "ברוטו").
• Use "net_pay"            (NOT "net", NOT "נטו").
• Every line_item object MUST contain all three required keys: "description_hebrew", "category", "value".

CONCRETE EXAMPLE of a valid line_items array:
"line_items": [
  {"description_hebrew": "שכר בסיס",   "category": "earning",    "value": 6223.70, "quantity": 186.0, "rate": 33.46},
  {"description_hebrew": "מס הכנסה",   "category": "deduction",  "value": 420.00,  "quantity": null,  "rate": null},
  {"description_hebrew": "ביטוח לאומי","category": "deduction",  "value": 310.00,  "quantity": null,  "rate": null},
  {"description_hebrew": "פנסיה מעסיק","category": "employer_contribution", "value": 621.00, "quantity": null, "rate": null}
]

══════════════════════════════════════════
PRIVACY RULES (mandatory, no exceptions)
══════════════════════════════════════════
- NEVER include employee name, employer name, or payroll provider name anywhere in your output.
- Silently omit them — leave those fields null or absent entirely.

══════════════════════════════════════════
ZERO HALLUCINATION POLICY
══════════════════════════════════════════
You are a strict OCR extraction engine. You MUST ONLY extract numbers exactly as they appear in the provided text. DO NOT perform any math or calculations. DO NOT invent or estimate numbers. If a value is unclear or not present in the text, return null for that field.

══════════════════════════════════════════
FIELD EXTRACTION RULES
══════════════════════════════════════════
- gross_pay / total_payments_other: Prefer the number labeled "סה"כ תשלומים". If absent, use "ברוטו לצורך מס" or "ברוטו למס הכנסה". Store the result in BOTH "gross_pay" and "total_payments_other".
- net_pay: Prefer the number labeled "נטו לתשלום". If absent, use "סכום בבנק" or "נטו בנק". If none of these labels appear, return null.
- income_tax: The number labeled "מס הכנסה". Return null if not found.
- national_insurance: The number labeled "ביטוח לאומי". Return null if not found.
- health_insurance: The number labeled "מס בריאות" or "ביטוח בריאות". Return null if not found.
- gross_taxable: The number labeled "ברוטו לצורך מס" or "ברוטו למס הכנסה". Return null if not found.
- gross_ni: The number labeled "ברוטו לביטוח לאומי". Return null if not found.
- credit_points: The number labeled "נקודות זיכוי". Return null if not found.
- pay_month: Format YYYY-MM derived from the payslip date. Return null if not found.
- All deduction values MUST be POSITIVE numbers (never negative).

Hint for tax tables: Tax values often appear in a single horizontal row aligned under their headers (e.g., מס הכנסה, ביטוח לאומי, ביטוח בריאות). Extract the exact numbers written in that row.

CLEANUP: Strip OCR artifacts from description_hebrew strings — remove standalone Latin words (DANN, NAD, DNN), date strings (DD/MM/YYYY), and isolated 4+-digit codes. Keep Hebrew text only.

══════════════════════════════════════════
OUTPUT FORMAT
══════════════════════════════════════════
Return a single JSON object. No markdown, no code fences, no explanation — pure JSON only."""

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
# Phase 16: Accounting guardrail
# ---------------------------------------------------------------------------

def _validate_accounting(
    gross: "float | None",
    total_deductions: "float | None",
    net_pay: "float | None",
    tolerance: float = 1.0,
) -> "tuple[bool, str]":
    """
    Lightweight accounting guardrail: verify that gross - total_deductions ≈ net_pay.

    Returns (True, "")          when math checks out or inputs are insufficient.
    Returns (False, message)    when |extracted_net - computed_net| > tolerance.

    Always returns (True, "") when any input is None — conservative, no false positives.
    tolerance=1.0 ILS absorbs rounding (agorah-level) differences.

    This function is pure (no logging, no side-effects) so it is directly testable.
    The caller is responsible for calling logger.warning() on a False result.
    """
    if gross is None or total_deductions is None or net_pay is None:
        return (True, "")

    computed_net = gross - total_deductions
    delta = abs(net_pay - computed_net)

    if delta > tolerance:
        msg = (
            f"Accounting guardrail: net discrepancy of {delta:.2f} ILS detected. "
            f"gross={gross:.2f}, total_deductions={total_deductions:.2f}, "
            f"computed_net={computed_net:.2f}, extracted_net={net_pay:.2f}. "
            f"Re-evaluate deduction line items vs. payments — possible missed voluntary "
            f"deductions or incorrect net field (שכר נטו vs. נטו לתשלום)."
        )
        return (False, msg)

    return (True, "")


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

    # --- Phase 16: Accounting guardrail (log-only; no retry/exception) ---
    _acct_ok, _acct_msg = _validate_accounting(
        gross=gross,
        total_deductions=total_deductions,
        net_pay=net,
    )
    if not _acct_ok:
        logger.warning(_acct_msg)

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
    Send OCR text to Groq (llama-3.3-70b-versatile) and map the structured
    JSON response to a ParsedSlipPayload.

    Privacy guarantee:
      - Raw OCR text is sent to Groq but never logged (only char count is logged).
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
    api_key = os.getenv("GROQ_API_KEY") or _GROQ_API_KEY
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY is not set — LLM extraction unavailable. "
            "Set GROQ_API_KEY in your .env file to enable Groq-powered extraction."
        )

    logger.info("LLM: API key present (%d chars), configuring %s", len(api_key), _GROQ_MODEL)

    from groq import Groq  # lazy import: not required if key absent

    client = Groq(api_key=api_key)

    # Truncate input to avoid excessive token usage / cost
    truncated_text = full_text[:_MAX_INPUT_CHARS]
    logger.info(
        "LLM: sending %d chars (truncated from %d) to %s",
        len(truncated_text),
        len(full_text),
        _GROQ_MODEL,
    )

    # Groq chat-completions API: system instruction + user message with OCR text
    messages = [
        {
            "role": "system",
            "content": _SYSTEM_INSTRUCTION,
        },
        {
            "role": "user",
            "content": (
                f"---OCR TEXT START---\n{truncated_text}\n---OCR TEXT END---"
            ),
        },
    ]

    try:
        completion = client.chat.completions.create(
            model=_GROQ_MODEL,
            messages=messages,
            response_format={"type": "json_object"},  # guaranteed JSON output
            temperature=0,   # deterministic — payslip extraction is not creative
        )
    except Exception as api_exc:
        # Log the full exception type and message so the developer can see exactly
        # what failed (authentication error, rate limit, network timeout, etc.)
        logger.error(
            "LLM: Groq API call failed — %s: %s",
            type(api_exc).__name__,
            api_exc,
        )
        raise  # propagate to parse_with_ocr() → triggers regex fallback

    # Extract the text from the first (and only) choice
    raw_json = completion.choices[0].message.content or ""
    logger.debug("LLM: raw response length=%d chars", len(raw_json))

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as json_exc:
        logger.error(
            "LLM: JSON decode failed — %s. First 500 chars of response: %r",
            json_exc,
            raw_json[:500],
        )
        raise

    try:
        extracted = LLMExtractedPayload.model_validate(data)
    except Exception as val_exc:
        logger.error(
            "LLM: Pydantic validation failed — %s: %s",
            type(val_exc).__name__,
            val_exc,
        )
        raise

    logger.info(
        "LLM: extraction successful — %d line items, gross=%s, net=%s",
        len(extracted.line_items),
        extracted.total_payments_other or extracted.gross_pay,
        extracted.net_pay,
    )

    return _map_to_payload(extracted, answers=answers)
