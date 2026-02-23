/**
 * LandingPage – hero, privacy promise, and 3-step explainer.
 */

import { useNavigate } from "react-router-dom";

export function LandingPage() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-gradient-to-b from-blue-50 to-white">
      {/* Navbar */}
      <header className="bg-white shadow-sm sticky top-0 z-10">
        <div className="max-w-4xl mx-auto px-6 py-4 flex justify-between items-center">
          <span className="text-xl font-bold text-blue-700">תלוש ברור</span>
          <span className="text-sm text-gray-400">בטא</span>
        </div>
      </header>

      {/* Hero */}
      <main className="max-w-4xl mx-auto px-6 py-16 text-center">
        <h1 className="text-5xl font-extrabold text-gray-900 mb-4 leading-tight">
          תלוש ברור
        </h1>
        <p className="text-xl text-gray-600 mb-2">
          מנתחים את תלוש השכר שלך — בעברית, בפשטות, בפרטיות מלאה.
        </p>
        <p className="text-sm text-gray-400 mb-10">
          לצרכים חינוכיים בלבד · אינו מהווה ייעוץ משפטי או מס
        </p>

        <button
          onClick={() => navigate("/upload")}
          className="bg-blue-600 hover:bg-blue-700 text-white text-lg font-semibold py-4 px-12 rounded-xl shadow-md transition-all duration-200 hover:shadow-lg active:scale-95"
        >
          העלאת תלוש לניתוח
        </button>

        {/* Privacy promise */}
        <div className="mt-6 inline-flex items-center gap-2 bg-green-50 border border-green-200 rounded-lg px-4 py-2 text-sm text-green-700">
          <span>🔒</span>
          <span>מצב אנונימי פעיל — הקובץ נמחק אוטומטית תוך שעה</span>
        </div>
      </main>

      {/* How it works */}
      <section className="max-w-4xl mx-auto px-6 pb-20">
        <h2 className="text-2xl font-bold text-gray-800 text-center mb-10">
          איך זה עובד?
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          {[
            {
              step: "1",
              icon: "📤",
              title: "מעלים תלוש",
              desc: "JPG, PNG, HEIC או PDF — עד 20MB. התלוש לא נשמר ולא עובר לשום גורם שלישי.",
            },
            {
              step: "2",
              icon: "❓",
              title: "עונים 6 שאלות קצרות",
              desc: "שאלות בסיסיות (שכר שעתי/חודשי, פנסיה, רכב) כדי לדייק את הניתוח.",
            },
            {
              step: "3",
              icon: "📋",
              title: "מקבלים ניתוח מפורט",
              desc: "פירוט כל שורה בעברית, זיהוי חריגות, בדיקת תקינות ונקודות זיכוי.",
            },
          ].map((item) => (
            <div
              key={item.step}
              className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 text-center"
            >
              <div className="text-4xl mb-3">{item.icon}</div>
              <div className="inline-block bg-blue-100 text-blue-700 text-xs font-bold px-2 py-0.5 rounded-full mb-2">
                שלב {item.step}
              </div>
              <h3 className="font-bold text-gray-800 mb-1">{item.title}</h3>
              <p className="text-sm text-gray-500 leading-relaxed">{item.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="text-center text-xs text-gray-400 pb-8">
        תלוש ברור · לצרכים חינוכיים בלבד · אינו מהווה ייעוץ מס, משפטי, או פיננסי
      </footer>
    </div>
  );
}
