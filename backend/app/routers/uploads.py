"""
Upload routes (Phase 2A – DB-backed):
  POST /api/uploads                      – store file + DB row → awaiting_questions
  GET  /api/uploads/{upload_id}          – read from DB → status/progress/result
  POST /api/uploads/{upload_id}/answers  – upsert answers in DB → trigger processing
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.crud import (
    create_upload,
    create_upload_file,
    get_result,
    get_upload,
    update_upload_status,
    upsert_quick_answers,
)
from app.db.database import get_db
from app.models.schemas import (
    AnswersResponse,
    ParsedSlipPayload,
    QuickAnswers,
    StatusResponse,
    UploadResponse,
    UploadStatus,
)
from app.services.processor import run_processing_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024   # 20 MB

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
    "application/pdf",
}

EXTENSION_MAP = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/heif": "heic",
    "application/pdf": "pdf",
}

# Physical storage root (.data/uploads/ inside backend/)
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_UPLOADS_DIR = _BACKEND_ROOT / ".data" / "uploads"


def _upload_dir(upload_id: str) -> Path:
    d = _UPLOADS_DIR / upload_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file_dest(upload_id: str, extension: str) -> Path:
    return _upload_dir(upload_id) / f"original.{extension}"


# ---------------------------------------------------------------------------
# POST /api/uploads
# ---------------------------------------------------------------------------

@router.post("", response_model=UploadResponse, status_code=201)
async def create_upload_endpoint(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    transient: bool = Form(True),
    redact: bool = Form(True),
    save_consent: bool = Form(False),
):
    """
    Accept a payslip file (JPG/PNG/HEIC/PDF, <= 20 MB).
    - Validates MIME type and size.
    - Writes file to .data/uploads/{id}/original.<ext>.
    - Inserts Upload + UploadFile rows in DB.
    - In transient mode: sets expires_at = now + 1h (controlled by TRANSIENT_TTL_HOURS env).
    - Returns upload_id with status awaiting_questions.
    """
    # -- MIME validation --
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail={
                "error": "סוג קובץ לא נתמך",
                "detail": f"הקובץ חייב להיות JPG, PNG, HEIC, או PDF. התקבל: {content_type}",
            },
        )

    # -- Read + size check --
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": "הקובץ גדול מדי",
                "detail": f"גודל מקסימלי הוא 20MB. הועלה: {len(file_bytes) / (1024*1024):.1f}MB",
            },
        )
    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=400,
            detail={"error": "הקובץ ריק", "detail": "לא ניתן להעלות קובץ ריק."},
        )

    upload_id = str(uuid.uuid4())
    extension = EXTENSION_MAP.get(content_type, "bin")
    safe_filename = Path(file.filename or "upload").name   # strip any path components
    sha256 = hashlib.sha256(file_bytes).hexdigest()

    # Log only metadata – never file bytes or content
    logger.info(
        "New upload: upload_id=%s filename=%s size=%d mime=%s transient=%s",
        upload_id, safe_filename, len(file_bytes), content_type, transient,
    )

    # -- Write file to disk --
    dest = _file_dest(upload_id, extension)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(file_bytes)

    # -- Insert DB rows (commit handled by get_db dependency) --
    await create_upload(
        db,
        upload_id=upload_id,
        filename=safe_filename,
        mime_type=content_type,
        file_size_bytes=len(file_bytes),
        transient=transient,
        redact=redact,
        save_consent=save_consent,
    )
    await create_upload_file(
        db,
        upload_id=upload_id,
        path=str(dest),
        sha256=sha256,
    )

    return UploadResponse(upload_id=upload_id, status=UploadStatus.AWAITING_QUESTIONS)


# ---------------------------------------------------------------------------
# GET /api/uploads/{upload_id}
# ---------------------------------------------------------------------------

@router.get("/{upload_id}", response_model=StatusResponse)
async def get_upload_status(
    upload_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Poll upload processing status from DB.
    When status == done: returns the full parsed result.
    When transient TTL has passed: returns 410 Gone with Hebrew expired message.
    """
    row = await get_upload(db, upload_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "העלאה לא נמצאה", "detail": f"upload_id {upload_id} לא קיים."},
        )

    # -- Check for expired transient upload --
    if row.transient and row.expires_at is not None:
        now = datetime.now(timezone.utc)
        exp = row.expires_at
        # SQLite may return naive datetime; force UTC
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            raise HTTPException(
                status_code=410,
                detail={
                    "error": "פג תוקף",
                    "detail": "התלוש הזה נמחק אוטומטית מטעמי פרטיות",
                },
            )

    # -- Deserialize result if done --
    result_payload: ParsedSlipPayload | None = None
    if row.status == "done":
        result_dict = await get_result(db, upload_id)
        if result_dict:
            try:
                result_payload = ParsedSlipPayload.model_validate(result_dict)
            except Exception as exc:
                logger.error("Failed to parse result for %s: %s", upload_id, exc)

    return StatusResponse(
        upload_id=row.id,
        status=UploadStatus(row.status),
        progress={"stage": row.progress_stage, "pct": row.progress_pct},
        result=result_payload,
        error=row.error_message,
    )


# ---------------------------------------------------------------------------
# POST /api/uploads/{upload_id}/answers
# ---------------------------------------------------------------------------

@router.post("/{upload_id}/answers", response_model=AnswersResponse)
async def submit_answers(
    upload_id: str,
    answers: QuickAnswers,
    background_tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Upsert quick-answers in DB and trigger background processing.
    Transitions status: awaiting_questions → processing → done / failed.
    """
    row = await get_upload(db, upload_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "העלאה לא נמצאה", "detail": f"upload_id {upload_id} לא קיים."},
        )

    if row.status == "processing":
        raise HTTPException(
            status_code=409,
            detail={"error": "כבר בעיבוד", "detail": "התלוש כבר בתהליך עיבוד. אנא המתן."},
        )

    # -- Upsert answers + set status to processing --
    answers_dict = answers.model_dump(exclude_none=True)
    await upsert_quick_answers(db, upload_id=upload_id, answers_dict=answers_dict)
    await update_upload_status(
        db, upload_id,
        status="processing",
        progress_stage="מתחיל עיבוד…",
        progress_pct=0,
    )
    # commit() is called by get_db() dependency after handler returns

    logger.info("Answers saved for upload_id=%s — dispatching processing job", upload_id)

    # -- Kick off background job (non-blocking) --
    background_tasks.add_task(run_processing_job, upload_id)

    return AnswersResponse(
        upload_id=upload_id,
        status=UploadStatus.PROCESSING,
        message="התשובות נשמרו, העיבוד החל",
    )
