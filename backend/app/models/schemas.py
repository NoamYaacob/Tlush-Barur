"""
Pydantic schemas for the Talush Barur API.
All Hebrew text lives in the data; code/comments stay English.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UploadStatus(str, Enum):
    AWAITING_QUESTIONS = "awaiting_questions"   # file stored, waiting for user answers
    PROCESSING = "processing"                   # answers received, job running
    DONE = "done"                               # job complete, result available
    FAILED = "failed"                           # job failed


class AnomalySeverity(str, Enum):
    CRITICAL = "Critical"
    WARNING = "Warning"
    INFO = "Info"


class LineItemCategory(str, Enum):
    EARNING = "earning"
    DEDUCTION = "deduction"
    EMPLOYER_CONTRIBUTION = "employer_contribution"
    BENEFIT_IN_KIND = "benefit_in_kind"
    BALANCE = "balance"


# ---------------------------------------------------------------------------
# Quick-answers schema (post-upload questions)
# ---------------------------------------------------------------------------

class QuickAnswers(BaseModel):
    salary_type: Optional[str] = Field(None, description="hourly|monthly|daily|unknown")
    job_scope_pct: Optional[str] = Field(None, description="100|75|50|other|unknown")
    multiple_employers: Optional[str] = Field(None, description="yes|no|unknown")
    has_benefit_in_kind: Optional[str] = Field(None, description="yes|no|unknown")
    has_pension: Optional[str] = Field(None, description="yes|no|unknown")
    has_training_fund: Optional[str] = Field(None, description="yes|no|unknown")
    big_change_this_month: Optional[str] = Field(None, description="yes|no")
    big_change_description: Optional[str] = Field(None, description="free text, optional")
    # Optional extended questions
    has_shifts: Optional[str] = None
    has_travel: Optional[str] = None
    has_bonus: Optional[str] = None
    is_student: Optional[str] = None
    is_first_month: Optional[str] = None
    is_last_month: Optional[str] = None


# ---------------------------------------------------------------------------
# Parsed-slip payload sub-schemas
# ---------------------------------------------------------------------------

class SlipMeta(BaseModel):
    pay_month: Optional[str] = Field(None, description="YYYY-MM or null")
    provider_guess: str = "unknown"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    employer_name: Optional[str] = None
    employee_name_redacted: bool = True


class SummaryTotals(BaseModel):
    gross: Optional[float] = None
    gross_confidence: float = 0.0
    net: Optional[float] = None
    net_confidence: float = 0.0
    total_deductions: Optional[float] = None
    total_employer_contributions: Optional[float] = None
    income_tax: Optional[float] = None
    national_insurance: Optional[float] = None
    health_insurance: Optional[float] = None
    pension_employee: Optional[float] = None
    integrity_ok: bool = True
    integrity_notes: list[str] = Field(default_factory=list)
    # Extended summary-box fields (OCR payslips, optional)
    total_payments_other: Optional[float] = None       # סה"כ תשלומים אחרים
    mandatory_taxes_total: Optional[float] = None      # ניכויי חובה-מסים
    provident_funds_deduction: Optional[float] = None  # ניכוי קופות גמל
    other_deductions: Optional[float] = None           # ניכויים שונים
    net_salary: Optional[float] = None                 # שכר נטו
    net_to_pay: Optional[float] = None                 # נטו לתשלום (summary box)
    gross_taxable: Optional[float] = None              # ברוטו למס הכנסה
    gross_ni: Optional[float] = None                   # ברוטו לביטוח לאומי
    credit_points: Optional[float] = None              # נקודות זיכוי


class LineItem(BaseModel):
    id: str
    category: LineItemCategory
    description_hebrew: str
    explanation_hebrew: str
    value: Optional[float] = None
    raw_text: Optional[str] = None
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    page_index: int = 0
    is_unknown: bool = False
    unknown_guesses: list[str] = Field(default_factory=list)
    unknown_question: Optional[str] = None


class Anomaly(BaseModel):
    id: str
    severity: AnomalySeverity
    what_we_found: str        # מה מצאנו
    why_suspicious: str       # למה זה חשוד
    what_to_do: str           # מה עושים עכשיו
    ask_payroll: str          # מה לשאול את השכר?
    related_line_item_ids: list[str] = Field(default_factory=list)


class SectionBlock(BaseModel):
    section_name: str
    section_type: str = "page"   # "earnings_table" | "deductions_section" |
                                  # "contributions_section" | "ytd_section" |
                                  # "balances_section" | "summary_box" | "page"
    bbox_json: Optional[dict[str, Any]] = None
    page_index: int = 0
    raw_text_preview: Optional[str] = None


class TaxCreditsDetected(BaseModel):
    credit_points_detected: Optional[float] = None
    estimated_monthly_value: Optional[float] = None
    confidence: float = 0.0
    notes: list[str] = Field(default_factory=list)


class YTDMetrics(BaseModel):
    """Year-to-date accumulated totals extracted from the payslip YTD section."""
    gross_ytd: Optional[float] = None               # מצטבר ברוטו
    net_ytd: Optional[float] = None                 # מצטבר נטו
    income_tax_ytd: Optional[float] = None          # מצטבר מס הכנסה
    national_insurance_ytd: Optional[float] = None  # מצטבר ביטוח לאומי
    health_ytd: Optional[float] = None              # מצטבר מס בריאות
    pension_ytd: Optional[float] = None             # מצטבר פנסיה
    training_fund_ytd: Optional[float] = None       # מצטבר קרן השתלמות
    confidence: float = 0.0


class BalanceItem(BaseModel):
    """A carry-forward balance: vacation days, sick days, training fund balance, etc."""
    id: str
    name_hebrew: str
    balance_value: Optional[float] = None
    unit: str = "unknown"   # "days" | "hours" | "ils" | "unknown"
    confidence: float = 0.0
    raw_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Full parsed-slip payload (returned when status == done)
# ---------------------------------------------------------------------------

class ParsedSlipPayload(BaseModel):
    slip_meta: SlipMeta
    summary: SummaryTotals
    line_items: list[LineItem]
    anomalies: list[Anomaly]
    blocks: list[SectionBlock]
    tax_credits_detected: Optional[TaxCreditsDetected] = None
    answers_applied: bool = False   # True once quick-answers were used
    # Phase 2B/2C: parse provenance
    error_code: Optional[str] = None
    # None = normal | "OCR_REQUIRED" = internal transient (immediately upgraded to OCR attempt) |
    # "OCR_UNAVAILABLE" = OCR system deps missing | "IMAGE_UNSUPPORTED" = legacy (no longer emitted)
    parse_source: Optional[str] = None
    # "pdf_text_layer" | "ocr" | "mock" | "ocr_unavailable" | "ocr_required"
    ocr_debug_preview: Optional[str] = None
    # Local-dev debug only. Populated when DEBUG_OCR_PREVIEW=true AND transient=true.
    # Contains: char count header + first ~30 OCR lines (digits redacted) + keyword hit list.
    # Max 2000 chars. NEVER contains raw full OCR text.
    # Phase 3: YTD metrics and balance items
    ytd: Optional[YTDMetrics] = None
    balances: list[BalanceItem] = Field(default_factory=list)
    # Phase 6: user corrections audit trail (append-only; old payloads without this key → [])
    corrections: list[CorrectionEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 5: Tax Credits Wizard schemas
# ---------------------------------------------------------------------------

class CreditWizardRequest(BaseModel):
    """User-declared personal data for tax credit point estimation."""
    marital_status: str = "unknown"    # "single" | "married" | "divorced" | "widowed" | "unknown"
    num_children: int = 0              # number of children under 18
    has_degree: str = "unknown"        # "yes" | "no" | "unknown"  (BA/BSc or higher)
    has_army_service: str = "unknown"  # "yes" | "no" | "unknown"  (IDF / national service completed)
    is_new_immigrant: str = "unknown"  # "yes" | "no" | "unknown"  (עולה חדש, arrived < 3.5 yrs ago)
    is_disabled: str = "unknown"       # "yes" | "no" | "unknown"  (≥ 90% disability — informational)


class CreditPointComponent(BaseModel):
    """A single credit point source with its point value and whether it was applied."""
    label_hebrew: str
    points: float
    applied: bool   # True = user qualifies and points are included in expected total


class CreditWizardResult(BaseModel):
    """Result of the tax credits wizard: expected vs. detected comparison."""
    expected_points: float
    detected_points: Optional[float] = None   # from TaxCreditsDetected; None if slip had no data
    gap: Optional[float] = None               # expected - detected (None if no detected)
    gap_direction: str = "unknown"            # "ok" | "under" | "over" | "unknown"
    components: list[CreditPointComponent] = Field(default_factory=list)
    mismatch_reasons: list[str] = Field(default_factory=list)  # Hebrew explanations
    what_to_do: str = ""                      # Hebrew actionable advice
    confidence: float = 0.0                   # 0–1; lower when many fields are "unknown"
    disclaimer: str = "אומדן חינוכי בלבד — לא ייעוץ מס"


# ---------------------------------------------------------------------------
# Phase 6: Corrections schemas
# ---------------------------------------------------------------------------

class CorrectionEntry(BaseModel):
    """
    Single user correction applied to a field in ParsedSlipPayload.
    Embedded in the payload itself for an append-only audit trail.
    field_path uses dot-notation: "summary.gross" | "line_items[li_id].value"
    """
    field_path: str                  # canonical path to the corrected field
    original_value: Optional[float]  # value before correction (None if field was absent)
    corrected_value: Optional[float] # new value (None = clear the field)
    corrected_at: str                # ISO-8601 UTC string (str avoids datetime serialization issues)


class CorrectionsRequest(BaseModel):
    """Request body for PATCH /api/uploads/:id/corrections."""
    corrections: list[dict[str, Any]]
    # Each dict must have: {"field_path": str, "corrected_value": float | None}
    # Example: [{"field_path": "summary.gross", "corrected_value": 12500.0}]


# ---------------------------------------------------------------------------
# Upload state (persisted as JSON per upload_id)
# ---------------------------------------------------------------------------

class UploadState(BaseModel):
    upload_id: str
    original_filename: str
    file_size_bytes: int
    mime_type: str
    status: UploadStatus = UploadStatus.AWAITING_QUESTIONS
    progress_stage: str = "ממתין לעיבוד"
    progress_pct: int = 0
    error_message: Optional[str] = None
    answers: Optional[QuickAnswers] = None
    result: Optional[ParsedSlipPayload] = None
    transient: bool = True


# ---------------------------------------------------------------------------
# API response wrappers
# ---------------------------------------------------------------------------

class UploadResponse(BaseModel):
    upload_id: str
    status: UploadStatus


class StatusResponse(BaseModel):
    upload_id: str
    status: UploadStatus
    progress: dict[str, Any]
    result: Optional[ParsedSlipPayload] = None
    error: Optional[str] = None


class AnswersResponse(BaseModel):
    upload_id: str
    status: UploadStatus
    message: str = "התשובות נשמרו, העיבוד החל"


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
