/**
 * QuestionsPage – "שנייה לפני שמנתחים" quick-answers form.
 * 6 required questions + collapsible "עוד שאלות".
 * On submit: POST /api/uploads/:id/answers → navigate to /results/:id.
 */

import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { submitAnswers, type QuickAnswers } from "../lib/api";

// ---- Helper sub-components ----

interface OptionButtonsProps {
  options: { value: string; label: string }[];
  selected: string;
  onChange: (v: string) => void;
}
function OptionButtons({ options, selected, onChange }: OptionButtonsProps) {
  return (
    <div className="flex flex-wrap gap-2 mt-2">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`px-4 py-2 rounded-lg text-sm font-medium border transition-all duration-150
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

interface QuestionBlockProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
}
function QuestionBlock({ label, hint, children }: QuestionBlockProps) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-1">
      <p className="font-semibold text-gray-800">{label}</p>
      {hint && <p className="text-xs text-gray-400">{hint}</p>}
      {children}
    </div>
  );
}

// ---- Main page ----

export function QuestionsPage() {
  const { uploadId } = useParams<{ uploadId: string }>();
  const navigate = useNavigate();

  // Required questions state
  const [salaryType, setSalaryType] = useState("unknown");
  const [jobScope, setJobScope] = useState("unknown");
  const [multipleEmployers, setMultipleEmployers] = useState("unknown");
  const [hasBenefitInKind, setHasBenefitInKind] = useState("unknown");
  const [hasPension, setHasPension] = useState("unknown");
  const [hasTrainingFund, setHasTrainingFund] = useState("unknown");
  const [bigChange, setBigChange] = useState("no");
  const [bigChangeDesc, setBigChangeDesc] = useState("");

  // Optional extended questions
  const [showMore, setShowMore] = useState(false);
  const [hasShifts, setHasShifts] = useState("unknown");
  const [hasTravel, setHasTravel] = useState("unknown");
  const [hasBonus, setHasBonus] = useState("unknown");
  const [isStudent, setIsStudent] = useState("unknown");
  const [isFirstMonth, setIsFirstMonth] = useState("unknown");

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---- submit ----
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!uploadId) return;
    setError(null);
    setSubmitting(true);

    const answers: QuickAnswers = {
      salary_type: salaryType,
      job_scope_pct: jobScope,
      multiple_employers: multipleEmployers,
      has_benefit_in_kind: hasBenefitInKind,
      has_pension: hasPension,
      has_training_fund: hasTrainingFund,
      big_change_this_month: bigChange,
      big_change_description: bigChangeDesc || undefined,
      ...(showMore && {
        has_shifts: hasShifts,
        has_travel: hasTravel,
        has_bonus: hasBonus,
        is_student: isStudent,
        is_first_month: isFirstMonth,
      }),
    };

    try {
      await submitAnswers(uploadId, answers);
      navigate(`/results/${uploadId}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "שגיאה בשליחת התשובות. נסה שוב.");
    } finally {
      setSubmitting(false);
    }
  }

  const UNKNOWN_LABEL = "לא יודע/ת";

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm sticky top-0 z-10">
        <div className="max-w-2xl mx-auto px-6 py-4 flex items-center gap-3">
          <button
            onClick={() => navigate(-1)}
            className="text-blue-600 hover:text-blue-800 text-sm font-medium"
          >
            ← חזרה
          </button>
          <span className="text-lg font-bold text-gray-800">שאלות לפני הניתוח</span>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-6 py-8">
        {/* Intro */}
        <div className="mb-6">
          <h1 className="text-2xl font-extrabold text-gray-900 mb-1">
            שנייה לפני שמנתחים — כמה שאלות כדי לדייק
          </h1>
          <p className="text-gray-500 text-sm">
            זה עוזר לנו להבין אם התלוש תקין ולזהות פספוסים.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">

          {/* Q1 – Salary type */}
          <QuestionBlock label="1. מה סוג השכר שלך?">
            <OptionButtons
              selected={salaryType}
              onChange={setSalaryType}
              options={[
                { value: "monthly", label: "חודשי" },
                { value: "hourly", label: "שעתי" },
                { value: "daily", label: "יומי" },
                { value: "unknown", label: UNKNOWN_LABEL },
              ]}
            />
          </QuestionBlock>

          {/* Q2 – Job scope */}
          <QuestionBlock label="2. מה היקף המשרה שלך?">
            <OptionButtons
              selected={jobScope}
              onChange={setJobScope}
              options={[
                { value: "100", label: "100%" },
                { value: "75", label: "75%" },
                { value: "50", label: "50%" },
                { value: "other", label: "אחר" },
                { value: "unknown", label: UNKNOWN_LABEL },
              ]}
            />
          </QuestionBlock>

          {/* Q3 – Multiple employers */}
          <QuestionBlock
            label="3. האם עבדת ביותר ממעסיק אחד השנה?"
            hint="משפיע על חישוב נקודות הזיכוי ותיאום מס"
          >
            <OptionButtons
              selected={multipleEmployers}
              onChange={setMultipleEmployers}
              options={[
                { value: "yes", label: "כן" },
                { value: "no", label: "לא" },
                { value: "unknown", label: UNKNOWN_LABEL },
              ]}
            />
          </QuestionBlock>

          {/* Q4 – Benefits in kind / car */}
          <QuestionBlock
            label="4. יש לך רכב צמוד או שווי אחר?"
            hint="שווי רכב, מגורים, ביגוד — כל הטבה שמחויבת במס כהכנסה"
          >
            <OptionButtons
              selected={hasBenefitInKind}
              onChange={setHasBenefitInKind}
              options={[
                { value: "yes", label: "כן" },
                { value: "no", label: "לא" },
                { value: "unknown", label: UNKNOWN_LABEL },
              ]}
            />
          </QuestionBlock>

          {/* Q5 – Pension + Training fund (same screen) */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-4">
            <p className="font-semibold text-gray-800">5. הפרשות לחיסכון פנסיוני</p>

            <div>
              <p className="text-sm text-gray-700 mb-1">האם יש לך פנסיה?</p>
              <OptionButtons
                selected={hasPension}
                onChange={setHasPension}
                options={[
                  { value: "yes", label: "כן" },
                  { value: "no", label: "לא" },
                  { value: "unknown", label: UNKNOWN_LABEL },
                ]}
              />
            </div>

            <div>
              <p className="text-sm text-gray-700 mb-1">האם יש קרן השתלמות?</p>
              <OptionButtons
                selected={hasTrainingFund}
                onChange={setHasTrainingFund}
                options={[
                  { value: "yes", label: "כן" },
                  { value: "no", label: "לא" },
                  { value: "unknown", label: UNKNOWN_LABEL },
                ]}
              />
            </div>
          </div>

          {/* Q6 – Big change this month */}
          <div className="bg-white rounded-xl border border-gray-200 p-5 space-y-3">
            <p className="font-semibold text-gray-800">
              6. האם היה שינוי גדול בחודש הזה?
            </p>
            <p className="text-xs text-gray-400">
              לדוגמה: העלאת שכר, עיקול, יציאה/חזרה מחל"ד, שינוי היקף משרה
            </p>
            <OptionButtons
              selected={bigChange}
              onChange={setBigChange}
              options={[
                { value: "yes", label: "כן" },
                { value: "no", label: "לא" },
              ]}
            />

            {bigChange === "yes" && (
              <textarea
                placeholder="מה השתנה? (אופציונלי)"
                value={bigChangeDesc}
                onChange={(e) => setBigChangeDesc(e.target.value)}
                rows={2}
                className="w-full mt-2 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 resize-none"
              />
            )}
          </div>

          {/* Collapsible "More questions" */}
          <div className="border border-gray-200 rounded-xl overflow-hidden">
            <button
              type="button"
              onClick={() => setShowMore(!showMore)}
              className="w-full flex justify-between items-center px-5 py-4 bg-gray-50 hover:bg-gray-100 transition-colors text-sm font-medium text-gray-700"
            >
              <span>עוד שאלות (אופציונלי — משפר דיוק)</span>
              <span className="text-gray-400">{showMore ? "▲" : "▼"}</span>
            </button>

            {showMore && (
              <div className="p-5 space-y-4 bg-white">
                {/* Shifts */}
                <div>
                  <p className="text-sm font-medium text-gray-700 mb-1">האם יש לך עבודת משמרות?</p>
                  <OptionButtons
                    selected={hasShifts}
                    onChange={setHasShifts}
                    options={[
                      { value: "yes", label: "כן" },
                      { value: "no", label: "לא" },
                      { value: "unknown", label: UNKNOWN_LABEL },
                    ]}
                  />
                </div>

                {/* Travel */}
                <div>
                  <p className="text-sm font-medium text-gray-700 mb-1">האם יש החזר נסיעות?</p>
                  <OptionButtons
                    selected={hasTravel}
                    onChange={setHasTravel}
                    options={[
                      { value: "yes", label: "כן" },
                      { value: "no", label: "לא" },
                      { value: "unknown", label: UNKNOWN_LABEL },
                    ]}
                  />
                </div>

                {/* Bonus */}
                <div>
                  <p className="text-sm font-medium text-gray-700 mb-1">האם קיבלת בונוס החודש?</p>
                  <OptionButtons
                    selected={hasBonus}
                    onChange={setHasBonus}
                    options={[
                      { value: "yes", label: "כן" },
                      { value: "no", label: "לא" },
                      { value: "unknown", label: UNKNOWN_LABEL },
                    ]}
                  />
                </div>

                {/* Student */}
                <div>
                  <p className="text-sm font-medium text-gray-700 mb-1">האם אתה/את סטודנט/ית?</p>
                  <OptionButtons
                    selected={isStudent}
                    onChange={setIsStudent}
                    options={[
                      { value: "yes", label: "כן" },
                      { value: "no", label: "לא" },
                      { value: "unknown", label: UNKNOWN_LABEL },
                    ]}
                  />
                </div>

                {/* First / last month */}
                <div>
                  <p className="text-sm font-medium text-gray-700 mb-1">
                    האם זה החודש הראשון או האחרון אצל המעסיק?
                  </p>
                  <OptionButtons
                    selected={isFirstMonth}
                    onChange={setIsFirstMonth}
                    options={[
                      { value: "first", label: "ראשון" },
                      { value: "last", label: "אחרון" },
                      { value: "no", label: "לא" },
                      { value: "unknown", label: UNKNOWN_LABEL },
                    ]}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              ⚠️ {error}
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={submitting}
            className={`w-full py-4 rounded-xl font-bold text-lg transition-all duration-200
              ${!submitting
                ? "bg-blue-600 hover:bg-blue-700 text-white shadow-md hover:shadow-lg active:scale-95"
                : "bg-blue-400 text-white cursor-not-allowed"
              }`}
          >
            {submitting ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                שולח…
              </span>
            ) : (
              "התחל ניתוח →"
            )}
          </button>

          <p className="text-center text-xs text-gray-400 pb-4">
            לצרכים חינוכיים בלבד · אינו מהווה ייעוץ מס או משפטי
          </p>
        </form>
      </main>
    </div>
  );
}
