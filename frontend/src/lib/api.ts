/**
 * Typed API client for the Talush Barur backend.
 * All requests go through the Vite proxy (/api → http://127.0.0.1:8000).
 */

// ---------------------------------------------------------------------------
// Types (mirroring backend Pydantic schemas)
// ---------------------------------------------------------------------------

export type UploadStatus =
  | "awaiting_questions"
  | "processing"
  | "done"
  | "failed";

export type AnomalySeverity = "Critical" | "Warning" | "Info";

export type LineItemCategory =
  | "earning"
  | "deduction"
  | "employer_contribution"
  | "benefit_in_kind"
  | "balance";

export interface QuickAnswers {
  salary_type?: string;
  job_scope_pct?: string;
  multiple_employers?: string;
  has_benefit_in_kind?: string;
  has_pension?: string;
  has_training_fund?: string;
  big_change_this_month?: string;
  big_change_description?: string;
  // optional extended
  has_shifts?: string;
  has_travel?: string;
  has_bonus?: string;
  is_student?: string;
  is_first_month?: string;
  is_last_month?: string;
}

export interface SlipMeta {
  pay_month: string | null;
  provider_guess: string;
  confidence: number;
  employer_name: string | null;
  employee_name_redacted: boolean;
}

export interface SummaryTotals {
  gross: number | null;
  gross_confidence: number;
  net: number | null;
  net_confidence: number;
  total_deductions: number | null;
  total_employer_contributions: number | null;
  income_tax: number | null;
  national_insurance: number | null;
  health_insurance: number | null;
  pension_employee: number | null;
  integrity_ok: boolean;
  integrity_notes: string[];
  // Extended summary-box fields (OCR payslips, optional)
  total_payments_other: number | null;        // סה"כ תשלומים אחרים
  mandatory_taxes_total: number | null;       // ניכויי חובה-מסים
  provident_funds_deduction: number | null;   // ניכוי קופות גמל
  other_deductions: number | null;            // ניכויים שונים
  net_salary: number | null;                  // שכר נטו
  net_to_pay: number | null;                  // נטו לתשלום (summary box)
  gross_taxable: number | null;               // ברוטו למס הכנסה
  gross_ni: number | null;                    // ברוטו לביטוח לאומי
  credit_points: number | null;               // נקודות זיכוי
}

export interface LineItem {
  id: string;
  category: LineItemCategory;
  description_hebrew: string;
  explanation_hebrew: string;
  value: number | null;
  raw_text: string | null;
  confidence: number;
  page_index: number;
  is_unknown: boolean;
  unknown_guesses: string[];
  unknown_question: string | null;
  quantity: number | null;   // Phase 8/9: כמות (units/hours/days)
  rate: number | null;       // Phase 8/9: תעריף (rate per unit)
}

export interface Anomaly {
  id: string;
  severity: AnomalySeverity;
  what_we_found: string;
  why_suspicious: string;
  what_to_do: string;
  ask_payroll: string;
  related_line_item_ids: string[];
}

// Phase 15: Educational insight card
export type InsightKind = "info" | "success" | "warning";

export interface Insight {
  id: string;
  kind: InsightKind;   // "info" → blue  |  "success" → green  |  "warning" → yellow
  title: string;       // Short Hebrew headline
  body: string;        // Explanatory Hebrew paragraph
}

export interface SectionBlock {
  section_name: string;
  section_type: string; // "earnings_table" | "deductions_section" | "contributions_section" |
                        // "ytd_section" | "balances_section" | "summary_box" | "page"
  bbox_json: Record<string, number> | null;
  page_index: number;
  raw_text_preview: string | null;
}

// Phase 3: Year-to-date accumulated totals
export interface YTDMetrics {
  gross_ytd: number | null;              // מצטבר ברוטו
  net_ytd: number | null;               // מצטבר נטו
  income_tax_ytd: number | null;        // מצטבר מס הכנסה
  national_insurance_ytd: number | null;// מצטבר ביטוח לאומי
  health_ytd: number | null;            // מצטבר מס בריאות
  pension_ytd: number | null;           // מצטבר פנסיה
  training_fund_ytd: number | null;     // מצטבר קרן השתלמות
  confidence: number;
}

// Phase 3: Carry-forward balance (vacation days, sick days, training fund ILS, etc.)
export interface BalanceItem {
  id: string;
  name_hebrew: string;
  balance_value: number | null;
  unit: string; // "days" | "hours" | "ils" | "unknown"
  confidence: number;
  raw_text: string | null;
}

export interface TaxCreditsDetected {
  credit_points_detected: number | null;
  estimated_monthly_value: number | null;
  confidence: number;
  notes: string[];
}

export interface ParsedSlipPayload {
  slip_meta: SlipMeta;
  summary: SummaryTotals;
  line_items: LineItem[];
  anomalies: Anomaly[];
  insights: Insight[];   // Phase 15: educational insight cards (always present, may be empty)
  blocks: SectionBlock[];
  tax_credits_detected: TaxCreditsDetected | null;
  answers_applied: boolean;
  // Phase 2B/2C: parse provenance
  error_code: string | null;
  // null = normal | "OCR_REQUIRED" (internal transient) | "OCR_UNAVAILABLE" = OCR deps missing
  parse_source: string | null;
  // "pdf_text_layer" | "ocr" | "mock" | "ocr_unavailable"
  ocr_debug_preview: string | null;
  // Local-dev debug only (populated when DEBUG_OCR_PREVIEW=true AND transient=true)
  // Phase 3: YTD metrics and carry-forward balances
  ytd: YTDMetrics | null;
  balances: BalanceItem[];
  // Phase 6: user corrections audit trail (append-only; absent on old payloads → [])
  corrections?: CorrectionEntry[];
}

// Phase 6: User correction audit trail entry
export interface CorrectionEntry {
  field_path: string;           // e.g. "summary.gross" | "line_items[li_id].value"
  original_value: number | null; // value before correction (null if field was absent)
  corrected_value: number | null; // new value (null = cleared)
  corrected_at: string;          // ISO-8601 UTC string
}

// Phase 5: Tax Credits Wizard types
export interface CreditWizardRequest {
  marital_status: string;    // "single" | "married" | "divorced" | "widowed" | "unknown"
  num_children: number;      // 0+
  has_degree: string;        // "yes" | "no" | "unknown"
  has_army_service: string;  // "yes" | "no" | "unknown"
  is_new_immigrant: string;  // "yes" | "no" | "unknown"
  is_disabled: string;       // "yes" | "no" | "unknown"
}

export interface CreditPointComponent {
  label_hebrew: string;
  points: number;
  applied: boolean;
}

export interface CreditWizardResult {
  expected_points: number;
  detected_points: number | null;
  gap: number | null;
  gap_direction: string;  // "ok" | "under" | "over" | "unknown"
  components: CreditPointComponent[];
  mismatch_reasons: string[];
  what_to_do: string;
  confidence: number;
  disclaimer: string;
}

export interface UploadResponse {
  upload_id: string;
  status: UploadStatus;
}

export interface StatusResponse {
  upload_id: string;
  status: UploadStatus;
  progress: { stage: string; pct: number };
  result: ParsedSlipPayload | null;
  error: string | null;
}

export interface AnswersResponse {
  upload_id: string;
  status: UploadStatus;
  message: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Shared fetch wrapper that throws a typed error with Hebrew message. */
async function apiFetch<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const res = await fetch(input, init);
  if (!res.ok) {
    let detail = `שגיאת שרת ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail?.error ?? body?.detail ?? body?.error ?? detail;
    } catch {
      // ignore parse failure
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

/**
 * POST /api/uploads
 * Uploads a payslip file and returns upload_id with status awaiting_questions.
 */
export async function createUpload(
  file: File,
  options: { transient?: boolean; redact?: boolean; saveConsent?: boolean } = {}
): Promise<UploadResponse> {
  const { transient = true, redact = true, saveConsent = false } = options;
  const form = new FormData();
  form.append("file", file);
  form.append("transient", String(transient));
  form.append("redact", String(redact));
  form.append("save_consent", String(saveConsent));
  return apiFetch<UploadResponse>("/api/uploads", { method: "POST", body: form });
}

/**
 * GET /api/uploads/:uploadId
 * Returns current status + result payload when done.
 */
export async function getUploadStatus(uploadId: string): Promise<StatusResponse> {
  return apiFetch<StatusResponse>(`/api/uploads/${uploadId}`);
}

/**
 * POST /api/uploads/:uploadId/answers
 * Submits quick-answers and triggers processing.
 */
export async function submitAnswers(
  uploadId: string,
  answers: QuickAnswers
): Promise<AnswersResponse> {
  return apiFetch<AnswersResponse>(`/api/uploads/${uploadId}/answers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(answers),
  });
}

/**
 * POST /api/uploads/:uploadId/credits-wizard
 * Phase 5: Submit personal data for tax credit point estimation.
 * Returns expected vs. detected comparison with mismatch reasons.
 */
export async function submitCreditWizard(
  uploadId: string,
  request: CreditWizardRequest
): Promise<CreditWizardResult> {
  return apiFetch<CreditWizardResult>(`/api/uploads/${uploadId}/credits-wizard`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

/**
 * PATCH /api/uploads/:uploadId/corrections
 * Phase 6: Apply a single user correction to the parsed payslip.
 * Reruns integrity checks and anomaly rule engine with corrected values.
 * Returns the full updated ParsedSlipPayload including corrections audit trail.
 *
 * @param uploadId   The upload to correct.
 * @param fieldPath  Dot-notation path, e.g. "summary.gross" | "line_items[li_id].value"
 * @param correctedValue  New numeric value, or null to clear the field.
 */
export async function applyCorrections(
  uploadId: string,
  fieldPath: string,
  correctedValue: number | null,
): Promise<ParsedSlipPayload> {
  return apiFetch<ParsedSlipPayload>(`/api/uploads/${uploadId}/corrections`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      corrections: [{ field_path: fieldPath, corrected_value: correctedValue }],
    }),
  });
}

/**
 * GET /api/uploads/:uploadId/pdf
 * Phase 17: Download the professional Hebrew PDF report.
 *
 * Returns a Blob (PDF binary) which the caller triggers as a browser download.
 * Uses a plain fetch (not apiFetch) because we need res.blob(), not res.json().
 */
export async function exportPdf(uploadId: string): Promise<Blob> {
  const res = await fetch(`/api/uploads/${uploadId}/pdf`);
  if (!res.ok) {
    let detail = `שגיאת שרת ${res.status}`;
    try {
      const body = await res.json();
      detail = body?.detail?.error ?? body?.detail ?? body?.error ?? detail;
    } catch {
      // ignore parse failure
    }
    throw new Error(detail);
  }
  return res.blob();
}
