"""
תלוש ברור — FastAPI application entry point.

Phase 0: health endpoint only.
Phase 1: uploads, answers, processing, results.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

# Configure structured logging on startup (no PII ever logged)
configure_logging(settings.log_level)
logger = get_logger(__name__)


def create_app() -> FastAPI:
    """Factory that creates and configures the FastAPI app."""
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/api/docs" if settings.app_env == "development" else None,
        redoc_url="/api/redoc" if settings.app_env == "development" else None,
        openapi_url="/api/openapi.json" if settings.app_env == "development" else None,
    )

    # Allow the Vite dev server to call the API during local development
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    # Health endpoint — required for Vite proxy smoke test                #
    # ------------------------------------------------------------------ #
    @application.get("/health", tags=["ops"])
    async def health() -> dict:
        """Liveness probe. Returns app identity and version."""
        return {
            "status": "ok",
            "app": settings.app_name,
            "version": settings.app_version,
        }

    logger.info("app_ready", env=settings.app_env, version=settings.app_version)
    return application


app = create_app()
