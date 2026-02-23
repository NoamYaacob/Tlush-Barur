/**
 * UploadPage – file picker with privacy settings.
 * On submit: POST /api/uploads → navigate to /questions/:upload_id.
 */

import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createUpload } from "../lib/api";

const ACCEPTED = ".jpg,.jpeg,.png,.heic,.pdf";
const MAX_MB = 20;

export function UploadPage() {
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [transient, setTransient] = useState(true);
  const [redact, setRedact] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);

  // ---- file validation ----
  function validateFile(f: File): string | null {
    const allowedTypes = [
      "image/jpeg",
      "image/png",
      "image/heic",
      "image/heif",
      "application/pdf",
    ];
    if (!allowedTypes.includes(f.type) && !f.name.match(/\.(jpg|jpeg|png|heic|pdf)$/i)) {
      return "סוג קובץ לא נתמך. אפשרי: JPG, PNG, HEIC, PDF.";
    }
    if (f.size > MAX_MB * 1024 * 1024) {
      return `הקובץ גדול מ-${MAX_MB}MB.`;
    }
    return null;
  }

  function handleFileChange(f: File) {
    const err = validateFile(f);
    if (err) {
      setError(err);
      setFile(null);
      return;
    }
    setError(null);
    setFile(f);
  }

  // ---- drag-and-drop ----
  function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) handleFileChange(dropped);
  }

  // ---- submit ----
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setError(null);
    setUploading(true);
    try {
      const res = await createUpload(file, { transient, redact });
      navigate(`/questions/${res.upload_id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "שגיאה בהעלאת הקובץ. נסה שוב.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm sticky top-0 z-10">
        <div className="max-w-2xl mx-auto px-6 py-4 flex items-center gap-3">
          <button
            onClick={() => navigate("/")}
            className="text-blue-600 hover:text-blue-800 text-sm font-medium"
          >
            ← חזרה
          </button>
          <span className="text-lg font-bold text-gray-800">העלאת תלוש</span>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-6 py-10">
        <form onSubmit={handleSubmit} className="space-y-6">

          {/* Drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-2xl p-10 text-center cursor-pointer transition-all duration-150
              ${dragging ? "border-blue-500 bg-blue-50" : "border-gray-300 hover:border-blue-400 hover:bg-blue-50"}
              ${file ? "bg-green-50 border-green-400" : ""}`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept={ACCEPTED}
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFileChange(f);
              }}
            />

            {file ? (
              <div>
                <div className="text-4xl mb-2">✅</div>
                <p className="font-semibold text-green-700">{file.name}</p>
                <p className="text-sm text-gray-500 mt-1">
                  {(file.size / 1024).toFixed(0)} KB · לחץ להחלפה
                </p>
              </div>
            ) : (
              <div>
                <div className="text-5xl mb-3">📄</div>
                <p className="font-semibold text-gray-700 text-lg">
                  גרור לכאן או לחץ לבחירת קובץ
                </p>
                <p className="text-sm text-gray-400 mt-1">JPG, PNG, HEIC, PDF · עד 20MB</p>
              </div>
            )}
          </div>

          {/* Error */}
          {error && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
              ⚠️ {error}
            </div>
          )}

          {/* Privacy settings */}
          <div className="bg-white rounded-2xl border border-gray-200 p-6 space-y-4">
            <h2 className="font-bold text-gray-800 text-base">הגדרות פרטיות</h2>

            {/* Transient mode */}
            <label className="flex items-start gap-3 cursor-pointer">
              <div className="relative mt-0.5">
                <input
                  type="checkbox"
                  checked={transient}
                  onChange={(e) => setTransient(e.target.checked)}
                  className="sr-only peer"
                />
                <div className={`w-10 h-6 rounded-full transition-colors ${transient ? "bg-blue-600" : "bg-gray-300"}`} />
                <div className={`absolute top-0.5 right-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${transient ? "translate-x-0" : "-translate-x-4"}`} />
              </div>
              <div>
                <p className="font-medium text-gray-800">מצב אנונימי (מומלץ)</p>
                <p className="text-sm text-gray-500">
                  הקובץ ותוצאות הניתוח נמחקים אוטומטית תוך שעה. לא נשמר כלום לצמיתות.
                </p>
              </div>
            </label>

            {/* Redact toggle */}
            <label className="flex items-start gap-3 cursor-pointer">
              <div className="relative mt-0.5">
                <input
                  type="checkbox"
                  checked={redact}
                  onChange={(e) => setRedact(e.target.checked)}
                  className="sr-only peer"
                />
                <div className={`w-10 h-6 rounded-full transition-colors ${redact ? "bg-blue-600" : "bg-gray-300"}`} />
                <div className={`absolute top-0.5 right-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${redact ? "translate-x-0" : "-translate-x-4"}`} />
              </div>
              <div>
                <p className="font-medium text-gray-800">מסך מידע מזהה אוטומטית</p>
                <p className="text-sm text-gray-500">
                  ת.ז., כתובת, חשבון בנק — ימוסכו לפני הניתוח.
                </p>
              </div>
            </label>

            {/* Privacy notice */}
            <div className="bg-blue-50 border border-blue-100 rounded-lg px-3 py-2 text-xs text-blue-700">
              🔒 לצרכים חינוכיים בלבד. אינו מהווה ייעוץ מס, משפטי, או פיננסי.
            </div>
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={!file || uploading}
            className={`w-full py-4 rounded-xl font-bold text-lg transition-all duration-200
              ${file && !uploading
                ? "bg-blue-600 hover:bg-blue-700 text-white shadow-md hover:shadow-lg active:scale-95"
                : "bg-gray-200 text-gray-400 cursor-not-allowed"
              }`}
          >
            {uploading ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
                מעלה…
              </span>
            ) : (
              "המשך לשאלות ←"
            )}
          </button>
        </form>
      </main>
    </div>
  );
}
