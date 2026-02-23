"""
Talush Barur - Israeli Payslip Analyzer
FastAPI application entry point.
"""

from __future__ import annotations

import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.routers import uploads

# ---------------------------------------------------------------------------
# Logging – no PII
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
# App factory
# ---------------------------------------------------------------------------
app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Israeli payslip analyzer API",
)

# Rate limiter state must be on app.state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS – must be added before routes
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
    """Health check – does not log request details to avoid any PII."""
    return {"status": "ok", "app": APP_NAME, "version": APP_VERSION}


# ---------------------------------------------------------------------------
# Global error handler for clean Hebrew error responses
# ---------------------------------------------------------------------------

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=404,
        content={"error": "הנתיב לא נמצא", "detail": str(request.url)},
    )


@app.exception_handler(500)
async def server_error_handler(request: Request, exc: Exception):
    logger.error("Unhandled server error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "שגיאה פנימית בשרת", "detail": "אנא נסה שוב מאוחר יותר."},
    )
