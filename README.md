# תלוש ברור

**Understand your Israeli payslip — privately, in Hebrew.**

תלוש ברור is a privacy-first web app that analyzes Israeli payslips (תלושי שכר), explains every line item in plain Hebrew, and flags anomalies for educational review.

---

## Project Structure

```
Tlush-Barur/
├── backend/          # FastAPI + Python 3.12
├── frontend/         # Vite + React + TypeScript + Tailwind (RTL)
├── README.md
├── LICENSE           # MIT
└── .gitignore
```

---

## Local Development (NO Docker required)

### Prerequisites

- **Python 3.12+** — [python.org](https://www.python.org/downloads/)
- **Node.js 20+** — [nodejs.org](https://nodejs.org/)
- **pip** and **npm** (bundled with the above)

---

### 1. Backend (FastAPI on port 8000)

```bash
cd backend

# Create and activate virtual environment
python3.12 -m venv .venv
source .venv/bin/activate        # Linux / macOS
# .venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Copy env file
cp .env.example .env
# Edit .env if needed (DATABASE_URL etc.)

# Run dev server
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend will be live at: **http://127.0.0.1:8000**

Health check:
```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","app":"תלוש ברור","version":"1.0.0"}
```

---

### 2. Frontend (Vite + React on port 5173)

```bash
cd frontend

# Install dependencies
npm install

# Run dev server
npm run dev
```

Frontend will be live at: **http://127.0.0.1:5173**

> The Vite dev server proxies `/api` and `/health` to `http://127.0.0.1:8000`.

---

### Running Both Together

Open two terminal windows:

**Terminal 1 — Backend:**
```bash
cd backend && source .venv/bin/activate && uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 — Frontend:**
```bash
cd frontend && npm run dev
```

Then open **http://127.0.0.1:5173** in your browser.

---

## Privacy

- **Transient mode (default):** Files are analyzed and auto-deleted within 1 hour. Nothing is saved.
- **Saved mode:** Requires explicit user consent. Sensitive identifiers are auto-redacted by default.
- No PII is ever written to logs.

---

## License

MIT — see [LICENSE](./LICENSE)
