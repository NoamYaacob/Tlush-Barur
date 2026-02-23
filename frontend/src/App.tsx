import React from "react";

/**
 * App root — Phase 0 scaffold.
 * Phase 1 will wire in React Router and full page components.
 */
function App() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-50 p-6">
      {/* RTL Hebrew heading */}
      <h1 className="text-4xl font-bold text-brand-700 mb-4">תלוש ברור</h1>
      <p className="text-xl text-gray-600 mb-2">
        הבן את תלוש השכר שלך — בפרטיות, בעברית
      </p>
      <p className="text-sm text-gray-400">
        Phase 0 — scaffold ready. Backend:{" "}
        <a
          href="/health"
          className="underline text-brand-500 ltr"
          dir="ltr"
        >
          /health
        </a>
      </p>
    </div>
  );
}

export default App;
