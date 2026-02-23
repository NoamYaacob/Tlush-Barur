import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { LandingPage } from "./pages/LandingPage";
import { UploadPage } from "./pages/UploadPage";
import { QuestionsPage } from "./pages/QuestionsPage";
import { ResultsPage } from "./pages/ResultsPage";
import "./App.css";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/questions/:uploadId" element={<QuestionsPage />} />
        <Route path="/results/:uploadId" element={<ResultsPage />} />
        {/* Catch-all → home */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
