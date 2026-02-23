"""
Upload routes:
  POST /api/uploads                      – receive file → status: awaiting_questions
  GET  /api/uploads/{upload_id}          – poll status + result
  POST /api/uploads/{upload_id}/answers  – save answers → triggers processing job
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File, Form

from app.models.schemas import (
    AnswersResponse,
    QuickAnswers,
    StatusResponse,
    UploadResponse,
    UploadState,
    UploadStatus,
)
from app.services.processor import run_processing_job
from app.services.storage import (
    ensure_upload_dir,
    file_path_for_upload,
    load_state,
    save_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

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

# ---------------------------------------------------------------------------
# POST /api/uploads
# ---------------------------------------------------------------------------

@router.post("", response_model=UploadResponse, status_code=201)
async def create_upload(
    request: Request,
    file: UploadFile = File(...),
    transient: bool = Form(True),
    redact: bool = Form(True),
    save_consent: bool = Form(False),
):
    """
    Accept a payslip file (JPG/PNG/HEIC/PDF).
    - Validates MIME type and size.
    - Stores file transiently on disk.
    - Returns upload_id with status 'awaiting_questions'.
    - Does NOT start processing yet; waits for POST /answers.
    """
    # -- validate MIME type --
    content_type = file.content_type or ""
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail={
                "error": "סוג קובץ לא נתמך",
                "detail": f"הקובץ חייב להיות JPG, PNG, HEIC, או PDF. התקבל: {content_type}",
            },
        )

    # -- read and size-check (stream into memory then write) --
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

    # -- create upload record --
    upload_id = str(uuid.uuid4())
    extension = EXTENSION_MAP.get(content_type, "bin")
    safe_filename = Path(file.filename or "upload").name   # strip any path components

    # Log only metadata — never file bytes
    logger.info(
        "New upload: upload_id=%s filename=%s size=%d mime=%s transient=%s",
        upload_id, safe_filename, len(file_bytes), content_type, transient,
    )

    # -- write file to disk --
    ensure_upload_dir(upload_id)
    dest = file_path_for_upload(upload_id, extension)
    async with aiofiles.open(dest, "wb") as f:
        await f.write(file_bytes)

    # -- persist initial state (awaiting_questions – no processing yet) --
    state = UploadState(
        upload_id=upload_id,
        original_filename=safe_filename,
        file_size_bytes=len(file_bytes),
        mime_type=content_type,
        status=UploadStatus.AWAITING_QUESTIONS,
        progress_stage="ממתין לתשובות",
        progress_pct=0,
        transient=transient,
    )
    save_state(state)

    return UploadResponse(upload_id=upload_id, status=UploadStatus.AWAITING_QUESTIONS)


# ---------------------------------------------------------------------------
# GET /api/uploads/{upload_id}
# ---------------------------------------------------------------------------

@router.get("/{upload_id}", response_model=StatusResponse)
async def get_upload_status(upload_id: str):
    """
    Poll upload processing status.
    Returns progress and, when status == done, the full parsed payload.
    """
    state = load_state(upload_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "העלאה לא נמצאה", "detail": f"upload_id {upload_id} לא קיים."},
        )

    return StatusResponse(
        upload_id=state.upload_id,
        status=state.status,
        progress={"stage": state.progress_stage, "pct": state.progress_pct},
        result=state.result,
        error=state.error_message,
    )


# ---------------------------------------------------------------------------
# POST /api/uploads/{upload_id}/answers
# ---------------------------------------------------------------------------

@router.post("/{upload_id}/answers", response_model=AnswersResponse)
async def submit_answers(
    upload_id: str,
    answers: QuickAnswers,
    background_tasks: BackgroundTasks,
):
    """
    Store quick-answers and trigger background processing.
    Only valid when status == awaiting_questions (or done, for re-analysis).
    Transitions state: awaiting_questions → processing → done/failed.
    """
    state = load_state(upload_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "העלאה לא נמצאה", "detail": f"upload_id {upload_id} לא קיים."},
        )

    if state.status == UploadStatus.PROCESSING:
        raise HTTPException(
            status_code=409,
            detail={"error": "כבר בעיבוד", "detail": "התלוש כבר בתהליך עיבוד. אנא המתן."},
        )

    # Save answers; reset result so re-submission gives a fresh run
    state.answers = answers
    state.status = UploadStatus.PROCESSING
    state.progress_stage = "מתחיל עיבוד…"
    state.progress_pct = 0
    state.result = None
    state.error_message = None
    save_state(state)

    logger.info("Answers received for upload_id=%s — processing started", upload_id)

    # Kick off background processing job (non-blocking)
    background_tasks.add_task(run_processing_job, upload_id)

    return AnswersResponse(
        upload_id=upload_id,
        status=UploadStatus.PROCESSING,
        message="התשובות נשמרו, העיבוד החל",
    )
