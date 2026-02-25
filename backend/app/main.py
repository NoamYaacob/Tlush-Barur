"""
Talush Barur - Israeli Payslip Analyzer
FastAPI application entry point (Phase 2A).

Changes from Phase 1:
  - Async lifespan: init_db() on startup (SQLite auto-create tables),
    then spawn cleanup loop task.
  - Rate limiting via slowapi.
  - CORS for Vite dev server.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ---------------------------------------------------------------------------
# Load .env FIRST — before any other app imports that read os.environ.
# This ensures GEMINI_API_KEY (and other secrets) are available at module
# import time for all services, including llm_parser._GEMINI_API_KEY.
# ---------------------------------------------------------------------------
_ENV_FILE = Path(__file__).parent.parent / ".env"
load_dotenv(_ENV_FILE, override=False)  # override=False: real env vars take priority

from app.db.database import DATABASE_URL, init_db  # noqa: E402 (must follow load_dotenv)
from app.routers import uploads                      # noqa: E402
from app.services.cleanup import run_cleanup_loop    # noqa: E402

# ---------------------------------------------------------------------------
# Logging - no PII
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rate limiter (per IP)
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])

# ---------------------------------------------------------------------------
# App metadata
# ---------------------------------------------------------------------------
APP_NAME = "תלוש ברור"
APP_VERSION = "1.0.0"

CORS_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]

# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:
      1. init_db() - for SQLite, create tables if they don't exist.
         (For Postgres, Alembic migrations are used instead.)
      2. Spawn the cleanup background loop.
    Shutdown:
      Cancel the cleanup task cleanly.
    """
    dialect = DATABASE_URL.split(":")[0]
    logger.info("Starting Talush Barur (dialect=%s)", dialect)

    # Phase 16.3/16.6: Startup diagnostic — confirm whether Groq key was loaded.
    # Prints True/False so developer can immediately confirm .env was read.
    _groq_key_present = bool(os.environ.get("GROQ_API_KEY"))
    logger.info("DEBUG: Groq Key found: %s", _groq_key_present)
    if not _groq_key_present:
        logger.warning(
            "GROQ_API_KEY is not set — LLM extraction will be unavailable. "
            "Add GROQ_API_KEY to backend/.env to enable Groq-powered parsing."
        )

    # Create tables on SQLite (no-op for Postgres - use alembic upgrade head)
    if "sqlite" in dialect:
        await init_db()
        logger.info("SQLite tables ensured via init_db()")
    else:
        logger.info("PostgreSQL mode - run 'alembic upgrade head' to apply migrations")

    # Check OCR system deps (inline import avoids import-time failure if deps missing)
    try:
        from app.services.ocr import check_ocr_deps
        ocr_ok, ocr_missing = check_ocr_deps()
        if ocr_ok:
            logger.info("OCR deps available (tesseract+heb+poppler)")
        else:
            logger.warning("OCR deps missing — OCR will be unavailable: %s", ocr_missing)
    except Exception as exc:
        logger.warning("OCR dep check failed: %s", exc)

    # Start cleanup loop as background asyncio task
    cleanup_task = asyncio.create_task(run_cleanup_loop())
    logger.info("Cleanup loop started")

    yield  # app is running

    # Graceful shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Talush Barur shutdown complete")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Israeli payslip analyzer API",
    lifespan=lifespan,
)

# Rate limiter state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS - must be added before routes
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(uploads.router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
@limiter.exempt
async def health_check() -> dict:
    """Health check - no PII in response or logs."""
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}


# ---------------------------------------------------------------------------
# Global error handlers - Hebrew messages
# ---------------------------------------------------------------------------

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=404,
        content={"error": "הנתיב לא נמצא", "detail": str(request.url)},
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc: Exception):
    logger.error("Unhandled server error on %s: %s", request.url, exc)
    return JSONResponse(
        status_code=500,
        content={"error": "שגיאה פנימית בשרת", "detail": "אנא נסה שוב מאוחר יותר."},
    )
