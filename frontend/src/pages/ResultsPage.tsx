/**
 * ResultsPage – tabbed results view.
 * Tabs: סיכום | פירוט מלא | חריגות ובדיקות | נקודות זיכוי | מצטברים | ייצוא
 * Polls GET /api/uploads/:id every 1.5s until done/failed.
 * Anomaly severity emojis: Critical=🚨 Warning=⚠️ Info=ℹ️
 */

import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  type ParsedSlipPayload,
  type LineItem,
  type Anomaly,
  type CreditWizardRequest,
  type CreditWizardResult,
  submitCreditWizard,
} from "../lib/api";
import { useUploadStatus } from "../hooks/useUploadStatus";
import { ProgressBar } from "../components/ProgressBar";
import { ConfidenceBadge } from "../components/ConfidenceBadge";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number | null | undefined, prefix = "₪"): string {
  if (n == null) return "—";
  return `${prefix}${Math.abs(n).toLocaleString("he-IL", { maximumFractionDigits: 2 })}`;
}

const SEVERITY_EMOJI: Record<string, string> = {
  Critical: "🚨",
  Warning: "⚠️",
  Info: "ℹ️",
};

const SEVERITY_COLOR: Record<string, string> = {
  Critical: "border-red-300 bg-red-50",
  Warning: "border-yellow-300 bg-yellow-50",
  Info: "border-blue-200 bg-blue-50",
};

const SEVERITY_LABEL_COLOR: Record<string, string> = {
  Critical: "bg-red-100 text-red-800",
  Warning: "bg-yellow-100 text-yellow-800",
  Info: "bg-blue-100 text-blue-800",
};

const CATEGORY_LABEL: Record<string, string> = {
  earning: "הכנסות",
  deduction: "ניכויים",
  employer_contribution: "הפרשות מעסיק",
  benefit_in_kind: "שווי/זקיפות",
  balance: "יתרות",
};

const TABS = [
  { id: "summary", label: "סיכום" },
  { id: "breakdown", label: "פירוט מלא" },
  { id: "anomalies", label: "חריגות ובדיקות" },
  { id: "credits", label: "נקודות זיכוי" },
  { id: "ytd", label: "מצטברים" },
  { id: "export", label: "ייצוא" },
] as const;

type TabId = (typeof TABS)[number]["id"];

// ---------------------------------------------------------------------------
// Tab sub-views
// ---------------------------------------------------------------------------

function SummaryTab({ result }: { result: ParsedSlipPayload }) {
  const { summary, slip_meta } = result;

  return (
    <div className="space-y-4">
      {/* Slip meta */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="font-bold text-gray-700 mb-3">פרטי התלוש</h3>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span className="text-gray-500">חודש שכר: </span>
            <span className="font-medium">
              {slip_meta.pay_month ?? "לא זוהה"}
            </span>
          </div>
          <div>
            <span className="text-gray-500">ספק: </span>
            <span className="font-medium">{slip_meta.provider_guess}</span>
            <ConfidenceBadge value={slip_meta.confidence} />
          </div>
          <div>
            <span className="text-gray-500">מעסיק: </span>
            <span className="font-medium">{slip_meta.employer_name ?? "לא זוהה"}</span>
          </div>
          <div>
            <span className="text-gray-500">שם עובד: </span>
            <span className="text-gray-400 text-xs">מושחת לפרטיות</span>
          </div>
          {/* Parse source badge */}
          {result.parse_source === "pdf_text_layer" && (
            <div className="col-span-2 mt-1">
              <span className="inline-flex items-center gap-1 text-xs bg-green-100 text-green-800 px-2 py-0.5 rounded-full font-medium">
                ✅ נקרא מטקסט ה-PDF
              </span>
            </div>
          )}
          {result.parse_source === "mock" && (
            <div className="col-span-2 mt-1">
              <span className="inline-flex items-center gap-1 text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full font-medium">
                🔬 ניתוח לדוגמה (demo)
              </span>
            </div>
          )}
          {result.parse_source === "ocr" && (
            <div className="col-span-2 mt-1">
              <span className="inline-flex items-center gap-1 text-xs bg-blue-100 text-blue-800 px-2 py-0.5 rounded-full font-medium">
                ✅ נקרא באמצעות OCR
              </span>
            </div>
          )}
          {result.ocr_debug_preview && (
            <div className="col-span-2 mt-2">
              <details className="text-xs bg-yellow-50 border border-yellow-200 rounded-lg">
                <summary className="px-3 py-1.5 cursor-pointer font-mono text-yellow-800 font-semibold select-none">
                  🔍 OCR Debug Preview
                </summary>
                <pre className="px-3 pb-3 pt-1 text-gray-700 whitespace-pre-wrap break-all font-mono leading-relaxed overflow-auto max-h-60">
                  {result.ocr_debug_preview}
                </pre>
              </details>
            </div>
          )}
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-3">
        {[
          { label: "ברוטו", value: summary.gross, conf: summary.gross_confidence, color: "bg-green-50 border-green-200" },
          { label: "נטו לתשלום", value: summary.net, conf: summary.net_confidence, color: "bg-blue-50 border-blue-200" },
          { label: "סה״כ ניכויים", value: summary.total_deductions, conf: 0.9, color: "bg-orange-50 border-orange-200" },
          { label: "הפרשות מעסיק", value: summary.total_employer_contributions, conf: 0.9, color: "bg-purple-50 border-purple-200" },
        ].map((c) => (
          <div key={c.label} className={`rounded-xl border p-4 ${c.color}`}>
            <p className="text-xs text-gray-500 mb-1">{c.label}</p>
            <p className="text-2xl font-bold text-gray-800" dir="ltr">{fmt(c.value)}</p>
            <ConfidenceBadge value={c.conf} />
          </div>
        ))}
      </div>

      {/* Breakdown row */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <h3 className="font-bold text-gray-700 mb-3">פירוט ניכויים עיקריים</h3>
        <div className="space-y-2 text-sm">
          {[
            { label: "מס הכנסה", value: summary.income_tax },
            { label: "ביטוח לאומי", value: summary.national_insurance },
            { label: "ביטוח בריאות", value: summary.health_insurance },
            { label: "פנסיה (עובד)", value: summary.pension_employee },
          ].map((r) => (
            <div key={r.label} className="flex justify-between items-center border-b border-gray-100 pb-1">
              <span className="text-gray-600">{r.label}</span>
              <span className="font-medium text-gray-800" dir="ltr">
                {r.value != null ? `(${fmt(r.value)})` : "—"}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Extended OCR summary box — only shown when at least one field is populated */}
      {(summary.gross_taxable != null ||
        summary.gross_ni != null ||
        summary.total_payments_other != null ||
        summary.mandatory_taxes_total != null ||
        summary.provident_funds_deduction != null ||
        summary.other_deductions != null ||
        summary.net_salary != null ||
        summary.net_to_pay != null ||
        summary.credit_points != null) && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="font-bold text-gray-700 mb-3">תיבות סיכום (OCR)</h3>
          <div className="space-y-2 text-sm">
            {[
              { label: "ברוטו למס הכנסה",       value: summary.gross_taxable,             deduction: false },
              { label: "ברוטו לביטוח לאומי",    value: summary.gross_ni,                  deduction: false },
              { label: "סה״כ תשלומים אחרים",    value: summary.total_payments_other,      deduction: false },
              { label: "ניכויי חובה — מסים",    value: summary.mandatory_taxes_total,     deduction: true  },
              { label: "ניכוי קופות גמל",        value: summary.provident_funds_deduction, deduction: true  },
              { label: "ניכויים שונים",           value: summary.other_deductions,          deduction: true  },
              { label: "שכר נטו",                value: summary.net_salary,                deduction: false },
              { label: "נטו לתשלום",             value: summary.net_to_pay,                deduction: false },
              { label: "נקודות זיכוי",           value: summary.credit_points,             deduction: false },
            ]
              .filter((r) => r.value != null)
              .map((r) => (
                <div key={r.label} className="flex justify-between items-center border-b border-gray-100 pb-1">
                  <span className="text-gray-600">{r.label}</span>
                  <span className="font-medium text-gray-800" dir="ltr">
                    {r.deduction ? `(${fmt(r.value)})` : fmt(r.value!)}
                  </span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Integrity check */}
      <div className={`rounded-xl border p-4 ${summary.integrity_ok ? "bg-green-50 border-green-200" : "bg-red-50 border-red-200"}`}>
        <p className="font-semibold text-sm mb-1">
          {summary.integrity_ok ? "✅ בדיקת תקינות: תקין" : "⚠️ בדיקת תקינות: נמצאו בעיות"}
        </p>
        {summary.integrity_notes.map((note, i) => (
          <p key={i} className="text-xs text-gray-600">{note}</p>
        ))}
        {summary.integrity_ok && (
          <p className="text-xs text-gray-500">ברוטו פחות ניכויים ≈ נטו (בסבילות נורמלית)</p>
        )}
      </div>
    </div>
  );
}

function BreakdownTab({ result }: { result: ParsedSlipPayload }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  // Group by category
  const categories = ["earning", "deduction", "employer_contribution", "benefit_in_kind", "balance"] as const;

  return (
    <div className="space-y-4">
      {/* Answers applied banner */}
      {result.answers_applied && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-2 text-sm text-blue-700">
          ✨ הניתוח הותאם לפי התשובות שלך
        </div>
      )}

      {categories.map((cat) => {
        const items = result.line_items.filter((li) => li.category === cat);
        if (items.length === 0) return null;
        return (
          <div key={cat} className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="bg-gray-50 px-5 py-3 font-bold text-sm text-gray-700 border-b border-gray-200">
              {CATEGORY_LABEL[cat]}
            </div>
            {items.map((li: LineItem) => (
              <div key={li.id} className="border-b border-gray-100 last:border-0">
                <button
                  type="button"
                  onClick={() => toggle(li.id)}
                  className="w-full flex justify-between items-start px-5 py-3 hover:bg-gray-50 transition-colors text-right"
                >
                  <div className="flex items-center gap-2">
                    <span className={`font-medium text-sm ${li.is_unknown ? "text-orange-600" : "text-gray-800"}`}>
                      {li.description_hebrew}
                    </span>
                    {li.is_unknown && (
                      <span className="text-xs bg-orange-100 text-orange-700 px-2 py-0.5 rounded-full">
                        לא מזוהה
                      </span>
                    )}
                    <ConfidenceBadge value={li.confidence} />
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0 mr-2">
                    <span className="font-bold text-gray-800 text-sm" dir="ltr">
                      {li.value != null
                        ? (li.category === "deduction" ? `(${fmt(li.value)})` : fmt(li.value))
                        : "—"}
                    </span>
                    <span className="text-gray-400 text-xs">{expanded.has(li.id) ? "▲" : "▼"}</span>
                  </div>
                </button>

                {expanded.has(li.id) && (
                  <div className="px-5 pb-4 text-sm text-gray-600 bg-gray-50 space-y-2">
                    <p className="leading-relaxed">{li.explanation_hebrew}</p>
                    {li.raw_text && (
                      <p className="text-xs text-gray-400 font-mono bg-white px-2 py-1 rounded border">
                        טקסט מקורי: {li.raw_text}
                      </p>
                    )}
                    {li.is_unknown && li.unknown_guesses.length > 0 && (
                      <div>
                        <p className="font-medium text-orange-700 mb-1">ניחושים אפשריים:</p>
                        <div className="flex flex-wrap gap-1">
                          {li.unknown_guesses.map((g) => (
                            <span key={g} className="bg-orange-50 border border-orange-200 text-orange-700 text-xs px-2 py-0.5 rounded">
                              {g}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {li.unknown_question && (
                      <p className="text-xs text-blue-600 font-medium">
                        💬 {li.unknown_question}
                      </p>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}

function AnomaliesTab({ result }: { result: ParsedSlipPayload }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  if (result.anomalies.length === 0) {
    return (
      <div className="text-center py-12 text-gray-400">
        <div className="text-4xl mb-3">✅</div>
        <p>לא זוהו חריגות בתלוש זה.</p>
      </div>
    );
  }

  // Sort: Critical first, then Warning, then Info
  const sorted = [...result.anomalies].sort((a, b) => {
    const order = { Critical: 0, Warning: 1, Info: 2 };
    return (order[a.severity] ?? 3) - (order[b.severity] ?? 3);
  });

  return (
    <div className="space-y-3">
      {sorted.map((ano: Anomaly) => (
        <div
          key={ano.id}
          className={`rounded-xl border ${SEVERITY_COLOR[ano.severity]} overflow-hidden`}
        >
          <button
            type="button"
            onClick={() => toggle(ano.id)}
            className="w-full flex justify-between items-start px-5 py-4 text-right hover:opacity-90 transition-opacity"
          >
            <div className="flex items-start gap-3">
              <span className="text-2xl flex-shrink-0">{SEVERITY_EMOJI[ano.severity]}</span>
              <div>
                <span className={`inline-block text-xs font-bold px-2 py-0.5 rounded-full mb-1 ${SEVERITY_LABEL_COLOR[ano.severity]}`}>
                  {ano.severity === "Critical" ? "קריטי" : ano.severity === "Warning" ? "אזהרה" : "מידע"}
                </span>
                <p className="font-semibold text-gray-800 text-sm leading-snug">{ano.what_we_found}</p>
              </div>
            </div>
            <span className="text-gray-400 text-xs flex-shrink-0 mt-1">
              {expanded.has(ano.id) ? "▲" : "▼"}
            </span>
          </button>

          {expanded.has(ano.id) && (
            <div className="px-5 pb-5 space-y-3 text-sm border-t border-gray-200">
              <div>
                <p className="font-bold text-gray-600 mb-0.5">למה זה חשוד?</p>
                <p className="text-gray-700 leading-relaxed">{ano.why_suspicious}</p>
              </div>
              <div>
                <p className="font-bold text-gray-600 mb-0.5">מה עושים עכשיו?</p>
                <p className="text-gray-700 leading-relaxed">{ano.what_to_do}</p>
              </div>
              <div className="bg-white/70 border border-current/10 rounded-lg px-3 py-2">
                <p className="font-bold text-gray-600 mb-0.5">💬 מה לשאול את השכר?</p>
                <p className="text-gray-800 font-medium">{ano.ask_payroll}</p>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ---- OptionButtons helper (reused from QuestionsPage pattern) ----
interface WizardOptionButtonsProps {
  options: { value: string; label: string }[];
  selected: string;
  onChange: (v: string) => void;
}
function WizardOptionButtons({ options, selected, onChange }: WizardOptionButtonsProps) {
  return (
    <div className="flex flex-wrap gap-2 mt-1">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-all duration-150
            ${selected === o.value
              ? "bg-blue-600 text-white border-blue-600 shadow-sm"
              : "bg-white text-gray-700 border-gray-300 hover:border-blue-400 hover:bg-blue-50"
            }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ---- Gap indicator pill ----
function GapPill({ direction, gap }: { direction: string; gap: number | null }) {
  if (direction === "ok")
    return <span className="inline-flex items-center gap-1 text-green-700 bg-green-100 border border-green-300 rounded-full px-3 py-1 text-sm font-medium">✅ תואם</span>;
  if (direction === "under" && gap !== null)
    return <span className="inline-flex items-center gap-1 text-amber-700 bg-amber-100 border border-amber-300 rounded-full px-3 py-1 text-sm font-medium">⚠️ חסרות {gap.toFixed(2)} נקודות</span>;
  if (direction === "over" && gap !== null)
    return <span className="inline-flex items-center gap-1 text-red-700 bg-red-100 border border-red-300 rounded-full px-3 py-1 text-sm font-medium">🚨 עודף {Math.abs(gap).toFixed(2)} נקודות</span>;
  return <span className="inline-flex items-center gap-1 text-gray-500 bg-gray-100 border border-gray-300 rounded-full px-3 py-1 text-sm font-medium">לא ידוע</span>;
}

// ---- Main TaxCreditsTab ----
function TaxCreditsTab({ result, uploadId }: { result: ParsedSlipPayload; uploadId: string }) {
  const tc = result.tax_credits_detected;
  const UNKNOWN = "unknown";

  // Wizard form state
  const [wizardOpen, setWizardOpen] = useState(false);
  const [maritalStatus, setMaritalStatus] = useState(UNKNOWN);
  const [numChildren, setNumChildren] = useState(0);
  const [hasDegree, setHasDegree] = useState(UNKNOWN);
  const [hasArmyService, setHasArmyService] = useState(UNKNOWN);
  const [isNewImmigrant, setIsNewImmigrant] = useState(UNKNOWN);
  const [isDisabled, setIsDisabled] = useState(UNKNOWN);

  // Wizard result state
  const [wizardLoading, setWizardLoading] = useState(false);
  const [wizardResult, setWizardResult] = useState<CreditWizardResult | null>(null);
  const [wizardError, setWizardError] = useState<string | null>(null);

  async function handleWizardSubmit(e: React.FormEvent) {
    e.preventDefault();
    setWizardError(null);
    setWizardLoading(true);
    try {
      const req: CreditWizardRequest = {
        marital_status: maritalStatus,
        num_children: numChildren,
        has_degree: hasDegree,
        has_army_service: hasArmyService,
        is_new_immigrant: isNewImmigrant,
        is_disabled: isDisabled,
      };
      const res = await submitCreditWizard(uploadId, req);
      setWizardResult(res);
    } catch (err: unknown) {
      setWizardError(err instanceof Error ? err.message : "שגיאה בשליחת השאלון. נסה שוב.");
    } finally {
      setWizardLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Disclaimer */}
      <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-4 text-sm text-yellow-800">
        ⚠️ <strong>הצהרת גילוי נאות:</strong> הנתונים המוצגים כאן הם אומדן חינוכי בלבד.
        לייעוץ מס מוסמך פנה לרואה חשבון.
      </div>

      {/* Detected credits (from slip) */}
      {tc ? (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="font-bold text-gray-700 mb-3">נקודות זיכוי שזוהו בתלוש</h3>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
              <p className="text-xs text-gray-500">נקודות שזוהו</p>
              <p className="text-2xl font-bold text-blue-700" dir="ltr">{tc.credit_points_detected ?? "—"}</p>
              <ConfidenceBadge value={tc.confidence} />
            </div>
            <div className="bg-green-50 border border-green-200 rounded-lg p-3">
              <p className="text-xs text-gray-500">שווי חודשי משוער</p>
              <p className="text-2xl font-bold text-green-700" dir="ltr">{fmt(tc.estimated_monthly_value)}</p>
            </div>
          </div>
          <ul className="mt-4 space-y-1">
            {tc.notes.map((n, i) => (
              <li key={i} className="text-sm text-gray-600 flex gap-2">
                <span>•</span><span>{n}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 text-sm text-gray-500">
          לא זוהו נקודות זיכוי בתלוש זה — השתמש בשאלון למטה לאמדן.
        </div>
      )}

      {/* ---- Tax Credits Wizard ---- */}
      <div className="border border-gray-200 rounded-xl overflow-hidden">
        {/* Collapsible header */}
        <button
          type="button"
          onClick={() => setWizardOpen(!wizardOpen)}
          className="w-full flex justify-between items-center px-5 py-4 bg-gray-50 hover:bg-gray-100 transition-colors text-sm font-semibold text-gray-700"
        >
          <span>🔍 בדוק אם מגיע לך יותר — שאלון נקודות זיכוי</span>
          <span className="text-gray-400">{wizardOpen ? "▲" : "▼"}</span>
        </button>

        {wizardOpen && (
          <form onSubmit={handleWizardSubmit} className="p-5 bg-white space-y-4">

            {/* Q1 – Marital status */}
            <div>
              <p className="text-sm font-medium text-gray-700 mb-1">מה מצבך המשפחתי?</p>
              <WizardOptionButtons
                selected={maritalStatus}
                onChange={setMaritalStatus}
                options={[
                  { value: "single", label: "רווק/ה" },
                  { value: "married", label: "נשוי/אה" },
                  { value: "divorced", label: "גרוש/ה" },
                  { value: "widowed", label: "אלמן/ה" },
                  { value: "unknown", label: "לא יודע/ת" },
                ]}
              />
            </div>

            {/* Q2 – Number of children */}
            <div>
              <p className="text-sm font-medium text-gray-700 mb-1">כמה ילדים מתחת לגיל 18?</p>
              <div className="flex items-center gap-3 mt-1">
                <button
                  type="button"
                  onClick={() => setNumChildren(Math.max(0, numChildren - 1))}
                  className="w-8 h-8 rounded-lg border border-gray-300 bg-white text-gray-700 font-bold hover:bg-gray-50 flex items-center justify-center"
                >−</button>
                <span className="text-lg font-bold text-gray-800 w-6 text-center" dir="ltr">{numChildren}</span>
                <button
                  type="button"
                  onClick={() => setNumChildren(numChildren + 1)}
                  className="w-8 h-8 rounded-lg border border-gray-300 bg-white text-gray-700 font-bold hover:bg-gray-50 flex items-center justify-center"
                >+</button>
              </div>
            </div>

            {/* Q3 – Academic degree */}
            <div>
              <p className="text-sm font-medium text-gray-700 mb-1">יש לך תואר אקדמי (BA/BSc ומעלה)?</p>
              <WizardOptionButtons
                selected={hasDegree}
                onChange={setHasDegree}
                options={[
                  { value: "yes", label: "כן" },
                  { value: "no", label: "לא" },
                  { value: "unknown", label: "לא יודע/ת" },
                ]}
              />
            </div>

            {/* Q4 – Army / national service */}
            <div>
              <p className="text-sm font-medium text-gray-700 mb-1">האם סיימת שירות צבאי / לאומי מלא?</p>
              <WizardOptionButtons
                selected={hasArmyService}
                onChange={setHasArmyService}
                options={[
                  { value: "yes", label: "כן" },
                  { value: "no", label: "לא" },
                  { value: "unknown", label: "לא יודע/ת" },
                ]}
              />
            </div>

            {/* Q5 – New immigrant */}
            <div>
              <p className="text-sm font-medium text-gray-700 mb-1">האם אתה/את עולה חדש/ה (פחות מ-3.5 שנים בישראל)?</p>
              <WizardOptionButtons
                selected={isNewImmigrant}
                onChange={setIsNewImmigrant}
                options={[
                  { value: "yes", label: "כן" },
                  { value: "no", label: "לא" },
                  { value: "unknown", label: "לא יודע/ת" },
                ]}
              />
            </div>

            {/* Q6 – Disability (informational) */}
            <div>
              <p className="text-sm font-medium text-gray-700 mb-1">האם יש לך נכות מוכרת (90% ומעלה)?</p>
              <p className="text-xs text-gray-400 mb-1">מידע בלבד — הטבות נכות מורכבות ודורשות ייעוץ מקצועי</p>
              <WizardOptionButtons
                selected={isDisabled}
                onChange={setIsDisabled}
                options={[
                  { value: "yes", label: "כן" },
                  { value: "no", label: "לא" },
                  { value: "unknown", label: "לא יודע/ת" },
                ]}
              />
            </div>

            {/* Error */}
            {wizardError && (
              <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
                ⚠️ {wizardError}
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={wizardLoading}
              className={`w-full py-3 rounded-xl font-bold text-sm transition-all duration-200
                ${!wizardLoading
                  ? "bg-blue-600 hover:bg-blue-700 text-white shadow-sm hover:shadow-md active:scale-95"
                  : "bg-blue-400 text-white cursor-not-allowed"
                }`}
            >
              {wizardLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                  </svg>
                  מחשב…
                </span>
              ) : (
                "בדוק נקודות זיכוי →"
              )}
            </button>

            {/* ---- Wizard Result ---- */}
            {wizardResult && (
              <div className="mt-4 space-y-3 border-t border-gray-100 pt-4">

                {/* Expected vs. detected summary */}
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <div className="bg-purple-50 border border-purple-200 rounded-lg p-3">
                    <p className="text-xs text-gray-500">נקודות זיכוי צפויות</p>
                    <p className="text-2xl font-bold text-purple-700" dir="ltr">{wizardResult.expected_points}</p>
                    <ConfidenceBadge value={wizardResult.confidence} />
                  </div>
                  <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
                    <p className="text-xs text-gray-500">נקודות שזוהו בתלוש</p>
                    <p className="text-2xl font-bold text-blue-700" dir="ltr">{wizardResult.detected_points ?? "—"}</p>
                  </div>
                </div>

                {/* Gap pill */}
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-600 font-medium">תוצאה:</span>
                  <GapPill direction={wizardResult.gap_direction} gap={wizardResult.gap} />
                </div>

                {/* Components breakdown */}
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
                  <p className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">פירוט נקודות צפויות</p>
                  <div className="space-y-1 text-sm">
                    {wizardResult.components.map((c, i) => (
                      <div key={i} className="flex justify-between items-center">
                        <span className={c.applied ? "text-gray-700" : "text-gray-400 line-through"}>
                          {c.label_hebrew}
                        </span>
                        <span className={`font-medium tabular-nums ${c.applied ? "text-gray-800" : "text-gray-400"}`} dir="ltr">
                          {c.applied ? `+${c.points}` : `(${c.points})`}
                        </span>
                      </div>
                    ))}
                    <div className="flex justify-between items-center border-t border-gray-200 pt-1 mt-1 font-bold text-gray-800">
                      <span>סה״כ צפוי</span>
                      <span dir="ltr">{wizardResult.expected_points}</span>
                    </div>
                  </div>
                </div>

                {/* Mismatch reasons */}
                {wizardResult.mismatch_reasons.length > 0 && (
                  <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                    <p className="text-xs font-semibold text-amber-700 mb-2">סיבות אפשריות לפער:</p>
                    <ul className="space-y-1">
                      {wizardResult.mismatch_reasons.map((r, i) => (
                        <li key={i} className="text-sm text-amber-800 flex gap-2">
                          <span>•</span><span>{r}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* What to do */}
                {wizardResult.what_to_do && (
                  <div className="bg-white border border-gray-200 rounded-lg p-4">
                    <p className="text-xs font-semibold text-gray-500 mb-1">מה עושים?</p>
                    <p className="text-sm text-gray-700">{wizardResult.what_to_do}</p>
                  </div>
                )}

                {/* Disclaimer */}
                <p className="text-xs text-gray-400 text-center">{wizardResult.disclaimer}</p>
              </div>
            )}
          </form>
        )}
      </div>
    </div>
  );
}

function YtdTab({ result }: { result: ParsedSlipPayload }) {
  const { ytd, balances } = result;

  // Empty state: no YTD section in this payslip (common — most slips don't have one)
  if (!ytd && balances.length === 0) {
    return (
      <div className="text-center py-14 text-gray-400">
        <div className="text-4xl mb-3">📊</div>
        <p className="font-medium">לא נמצאו נתונים מצטברים בתלוש זה</p>
        <p className="text-xs mt-2 text-gray-300">
          נתונים מצטברים מופיעים רק בחלק מהתלושים
        </p>
      </div>
    );
  }

  const ytdRows = [
    { label: "ברוטו מצטבר",          value: ytd?.gross_ytd },
    { label: "נטו מצטבר",             value: ytd?.net_ytd },
    { label: "מס הכנסה מצטבר",       value: ytd?.income_tax_ytd },
    { label: "ביטוח לאומי מצטבר",    value: ytd?.national_insurance_ytd },
    { label: "מס בריאות מצטבר",      value: ytd?.health_ytd },
    { label: "פנסיה מצטברת",          value: ytd?.pension_ytd },
    { label: "קרן השתלמות מצטברת",   value: ytd?.training_fund_ytd },
  ].filter((r) => r.value != null);

  return (
    <div className="space-y-4">
      {/* YTD totals table */}
      {ytd && ytdRows.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="font-bold text-gray-700 mb-3 text-sm">מצטבר מתחילת השנה</h3>
          <div className="space-y-2 text-sm">
            {ytdRows.map((r) => (
              <div
                key={r.label}
                className="flex justify-between items-center border-b border-gray-100 pb-1 last:border-0"
              >
                <span className="text-gray-600">{r.label}</span>
                <span className="font-medium text-gray-800" dir="ltr">
                  {fmt(r.value!)}
                </span>
              </div>
            ))}
          </div>
          <div className="mt-3">
            <ConfidenceBadge value={ytd.confidence} />
          </div>
        </div>
      )}

      {/* Balance / carry-forward items */}
      {balances.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="font-bold text-gray-700 mb-3 text-sm">יתרות ואצירות</h3>
          <div className="space-y-2 text-sm">
            {balances.map((b) => (
              <div
                key={b.id}
                className="flex justify-between items-center border-b border-gray-100 pb-1 last:border-0"
              >
                <span className="text-gray-600">{b.name_hebrew}</span>
                <span className="font-medium text-gray-800" dir="ltr">
                  {b.balance_value != null
                    ? `${b.balance_value.toLocaleString("he-IL")} ${
                        b.unit === "days"
                          ? "ימים"
                          : b.unit === "hours"
                          ? "שעות"
                          : "₪"
                      }`
                    : "—"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Disclaimer */}
      <div className="bg-yellow-50 border border-yellow-200 rounded-xl p-3 text-xs text-yellow-800">
        נתונים מצטברים עשויים להיות חלקיים — לפי מה שמופיע בתלוש בלבד
      </div>
    </div>
  );
}

function ExportTab() {
  return (
    <div className="text-center py-14 text-gray-400">
      <div className="text-4xl mb-3">📥</div>
      <p>ייצוא PDF ו-Excel יהיה זמין בשלב הבא.</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function ResultsPage() {
  const { uploadId } = useParams<{ uploadId: string }>();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TabId>("summary");

  const { data, error, expired, loading } = useUploadStatus(uploadId ?? null);

  // ---- Transient TTL expired (410 Gone) ----
  if (expired) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="bg-white border border-gray-200 rounded-2xl p-8 text-center max-w-md shadow-sm">
          <div className="text-5xl mb-4">🔒</div>
          <p className="font-bold text-gray-800 text-lg mb-2">
            התלוש הזה נמחק אוטומטית מטעמי פרטיות
          </p>
          <p className="text-sm text-gray-500 mb-6">
            תלושים במצב אנונימי נמחקים אוטומטית לאחר שעה.
            כדי לנתח את התלוש שוב, יש להעלות אותו מחדש.
          </p>
          <button
            onClick={() => navigate("/upload")}
            className="bg-blue-600 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:bg-blue-700 transition-colors"
          >
            העלאת תלוש חדש
          </button>
        </div>
      </div>
    );
  }

  // ---- Loading / awaiting_questions ----
  if (!data && loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <div className="text-4xl mb-4 animate-pulse">⏳</div>
          <p className="text-gray-600">טוען…</p>
        </div>
      </div>
    );
  }

  // ---- Error state ----
  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="bg-white border border-red-200 rounded-2xl p-8 text-center max-w-md">
          <div className="text-4xl mb-3">❌</div>
          <p className="font-bold text-gray-800 mb-2">הבקשה נכשלה</p>
          <p className="text-sm text-gray-500 mb-4">{error}</p>
          <button
            onClick={() => navigate("/upload")}
            className="bg-blue-600 text-white px-6 py-2 rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            נסה שוב
          </button>
        </div>
      </div>
    );
  }

  // ---- Processing / awaiting_questions ----
  if (
    !data ||
    data.status === "awaiting_questions" ||
    data.status === "processing"
  ) {
    return (
      <div className="min-h-screen bg-gray-50">
        <header className="bg-white shadow-sm sticky top-0 z-10">
          <div className="max-w-2xl mx-auto px-6 py-4">
            <span className="text-lg font-bold text-gray-800">מעבד את התלוש…</span>
          </div>
        </header>
        <main className="max-w-2xl mx-auto px-6 py-16">
          <div className="bg-white rounded-2xl border border-gray-200 p-8 space-y-6">
            <div className="text-center">
              <div className="text-5xl mb-4 animate-spin inline-block">⚙️</div>
              <p className="text-gray-700 font-medium">
                {data?.status === "awaiting_questions"
                  ? "ממתין לתשובות…"
                  : "מנתח את התלוש שלך…"}
              </p>
              <p className="text-sm text-gray-400 mt-1">הניתוח לוקח כמה שניות</p>
            </div>
            {data && (
              <ProgressBar
                pct={data.progress.pct}
                stage={data.progress.stage}
              />
            )}
          </div>
        </main>
      </div>
    );
  }

  // ---- Failed ----
  if (data.status === "failed") {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="bg-white border border-red-200 rounded-2xl p-8 text-center max-w-md">
          <div className="text-4xl mb-3">❌</div>
          <p className="font-bold text-gray-800 mb-2">הניתוח נכשל</p>
          <p className="text-sm text-gray-500 mb-4">{data.error ?? "שגיאה לא ידועה"}</p>
          <button
            onClick={() => navigate("/upload")}
            className="bg-blue-600 text-white px-6 py-2 rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            העלה שוב
          </button>
        </div>
      </div>
    );
  }

  // ---- OCR unavailable (system deps missing — scanned PDF or image upload) ----
  if (data.status === "done" && data.result?.error_code === "OCR_UNAVAILABLE") {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
        <div className="bg-white border border-orange-200 rounded-2xl p-8 text-center max-w-md shadow-sm">
          <div className="text-5xl mb-4">⚙️</div>
          <p className="font-bold text-gray-800 text-lg mb-2">
            נדרשת הגדרה נוספת לקריאת תמונות
          </p>
          <p className="text-sm text-gray-600 mb-4 leading-relaxed">
            התלוש שהעלית הוא תמונה סרוקה או קובץ תמונה.
            כדי לקרוא אותו נדרש Tesseract OCR עם תמיכה בעברית.
          </p>
          <div className="bg-gray-50 border border-gray-200 rounded-lg px-4 py-3 text-sm text-right mb-6 font-mono text-gray-700">
            <p className="font-sans font-semibold text-gray-600 mb-2">התקנה (macOS):</p>
            <p>brew install tesseract-lang</p>
            <p>brew install poppler</p>
          </div>
          <button
            onClick={() => navigate("/upload")}
            className="bg-blue-600 text-white px-6 py-3 rounded-xl text-sm font-semibold hover:bg-blue-700 transition-colors w-full"
          >
            העלאת תלוש חדש
          </button>
        </div>
      </div>
    );
  }

  // ---- Done ----
  const result = data.result!;

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-6 py-4 flex justify-between items-center">
          <span className="text-lg font-bold text-blue-700">תלוש ברור</span>
          <button
            onClick={() => navigate("/upload")}
            className="text-sm text-blue-600 hover:text-blue-800"
          >
            ← ניתוח חדש
          </button>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 py-6">
        {/* Adapted banner */}
        {result.answers_applied && (
          <div className="flex justify-between items-center bg-blue-50 border border-blue-200 rounded-xl px-4 py-2 mb-4 text-sm text-blue-700">
            <span>✨ הניתוח הותאם לפי התשובות שלך</span>
            <button
              onClick={() => navigate(`/questions/${uploadId}`)}
              className="underline text-blue-600 hover:text-blue-800 text-xs"
            >
              ערוך תשובות
            </button>
          </div>
        )}

        {/* Anomaly count summary chips */}
        <div className="flex gap-2 flex-wrap mb-4">
          {(["Critical", "Warning", "Info"] as const).map((sev) => {
            const count = result.anomalies.filter((a) => a.severity === sev).length;
            if (count === 0) return null;
            return (
              <button
                key={sev}
                onClick={() => setActiveTab("anomalies")}
                className={`inline-flex items-center gap-1 text-xs font-medium px-3 py-1 rounded-full border ${SEVERITY_LABEL_COLOR[sev]} ${SEVERITY_COLOR[sev]} hover:opacity-80`}
              >
                {SEVERITY_EMOJI[sev]} {count} {sev === "Critical" ? "קריטי" : sev === "Warning" ? "אזהרה" : "מידע"}
              </button>
            );
          })}
        </div>

        {/* Tabs */}
        <div className="flex gap-1 overflow-x-auto pb-1 mb-4 scrollbar-hide">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-shrink-0 px-4 py-2 rounded-lg text-sm font-medium transition-colors
                ${activeTab === tab.id
                  ? "bg-blue-600 text-white shadow-sm"
                  : "bg-white text-gray-600 border border-gray-200 hover:bg-gray-50"
                }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div>
          {activeTab === "summary" && <SummaryTab result={result} />}
          {activeTab === "breakdown" && <BreakdownTab result={result} />}
          {activeTab === "anomalies" && <AnomaliesTab result={result} />}
          {activeTab === "credits" && <TaxCreditsTab result={result} uploadId={uploadId ?? ""} />}
          {activeTab === "ytd" && <YtdTab result={result} />}
          {activeTab === "export" && <ExportTab />}
        </div>
      </main>

      {/* Footer disclaimer */}
      <footer className="text-center text-xs text-gray-400 py-6 px-4">
        לצרכים חינוכיים בלבד · אינו מהווה ייעוץ מס, משפטי, או פיננסי · upload_id: {uploadId}
      </footer>
    </div>
  );
}
