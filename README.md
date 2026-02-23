# תלוש ברור — Israeli Payslip Analyzer

A web application for parsing and explaining Israeli payslips (תלושי שכר).

## Prerequisites

- Node.js v20+
- Python 3.12+
- git

No Docker required.

## Project Structure

```
tlush-barur/
├── frontend/    # Vite + React + TypeScript + Tailwind CSS
└── backend/     # FastAPI (Python 3.12)
```

## Local Development Setup

### 1. Backend setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Frontend setup

```bash
cd frontend
npm install
```

## OCR Setup (optional)

To analyze scanned PDFs and image files (JPG, PNG, HEIC), install:

```bash
brew install tesseract-lang   # Hebrew OCR support (heb.traineddata)
brew install poppler           # PDF→image rasterizer (pdftoppm)
```

The backend auto-detects OCR availability on startup and logs the result.
Without these deps, the analyzer works for digital (text-layer) PDFs only.

## Running the Application

### Start the backend (Terminal 1)

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Backend available at: http://127.0.0.1:8000

### Start the frontend (Terminal 2)

```bash
cd frontend
npm run dev
```

Frontend available at: http://127.0.0.1:5173

## Verify backend health

```bash
curl http://127.0.0.1:8000/health
```

Expected response:
```json
{"status": "ok", "app": "תלוש ברור", "version": "1.0.0"}
```

## API Proxy

The Vite dev server proxies `/api` and `/health` requests to the backend at `http://127.0.0.1:8000`.
You can also verify the proxy is working:

```bash
curl http://127.0.0.1:5173/health
```

## License

MIT
