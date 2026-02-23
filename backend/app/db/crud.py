"""
Database CRUD operations for Talush Barur.

All functions accept an AsyncSession and return ORM objects or None.
No PII (file bytes, raw text) is ever passed through here.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.orm import Upload, UploadFile, QuickAnswers, Result

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL for transient uploads
# ---------------------------------------------------------------------------

TRANSIENT_TTL_HOURS: float = 1.0   # set low (e.g. 0.003 ≈ 10s) via env for testing


def _set_transient_ttl() -> None:
    """Reads TRANSIENT_TTL_HOURS from env at import time (for testing)."""
    import os
    global TRANSIENT_TTL_HOURS
    try:
        TRANSIENT_TTL_HOURS = float(os.getenv("TRANSIENT_TTL_HOURS", "1.0"))
    except (ValueError, TypeError):
        pass


_set_transient_ttl()


def transient_expires_at() -> datetime:
    """Return UTC expiry timestamp for a transient upload."""
    return datetime.now(timezone.utc) + timedelta(hours=TRANSIENT_TTL_HOURS)


# ---------------------------------------------------------------------------
# Upload CRUD
# ---------------------------------------------------------------------------

async def create_upload(
    db: AsyncSession,
    *,
    upload_id: str,
    filename: str,
    mime_type: str,
    file_size_bytes: int,
    transient: bool,
    redact: bool,
    save_consent: bool,
) -> Upload:
    """Insert a new Upload row, set expires_at for transient mode."""
    row = Upload(
        id=upload_id,
        filename=filename,
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        status="awaiting_questions",
        progress_stage="ממתין לתשובות",
        progress_pct=0,
        transient=transient,
        redact=redact,
        save_consent=save_consent,
        expires_at=transient_expires_at() if transient else None,
    )
    db.add(row)
    await db.flush()   # get the row into the session (commit handled by get_db())
    return row


async def get_upload(db: AsyncSession, upload_id: str) -> Optional[Upload]:
    """Fetch an Upload with its related result (eager-loaded)."""
    stmt = (
        select(Upload)
        .where(Upload.id == upload_id)
        .options(
            selectinload(Upload.quick_answers),
            selectinload(Upload.result),
            selectinload(Upload.upload_file),
        )
    )
    row = await db.scalar(stmt)
    return row


async def update_upload_status(
    db: AsyncSession,
    upload_id: str,
    *,
    status: str,
    progress_stage: str,
    progress_pct: int,
    error_message: Optional[str] = None,
) -> None:
    """Update status + progress fields on an existing Upload row."""
    row = await db.get(Upload, upload_id)
    if row is None:
        logger.warning("update_upload_status: upload_id %s not found", upload_id)
        return
    row.status = status
    row.progress_stage = progress_stage
    row.progress_pct = progress_pct
    if error_message is not None:
        row.error_message = error_message
    await db.flush()


# ---------------------------------------------------------------------------
# UploadFile CRUD
# ---------------------------------------------------------------------------

async def create_upload_file(
    db: AsyncSession,
    *,
    upload_id: str,
    path: str,
    sha256: Optional[str] = None,
) -> UploadFile:
    """Insert an UploadFile row for a saved physical file."""
    row = UploadFile(upload_id=upload_id, path=path, sha256=sha256)
    db.add(row)
    await db.flush()
    return row


# ---------------------------------------------------------------------------
# QuickAnswers CRUD
# ---------------------------------------------------------------------------

async def upsert_quick_answers(
    db: AsyncSession,
    *,
    upload_id: str,
    answers_dict: dict,
) -> QuickAnswers:
    """Insert or replace quick answers for an upload."""
    stmt = select(QuickAnswers).where(QuickAnswers.upload_id == upload_id)
    existing = await db.scalar(stmt)
    answers_json = json.dumps(answers_dict, ensure_ascii=False)

    if existing:
        existing.answers_json = answers_json
        existing.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return existing

    row = QuickAnswers(
        upload_id=upload_id,
        answers_json=answers_json,
    )
    db.add(row)
    await db.flush()
    return row


async def get_quick_answers(db: AsyncSession, upload_id: str) -> Optional[dict]:
    """Return the answers dict for an upload, or None."""
    stmt = select(QuickAnswers).where(QuickAnswers.upload_id == upload_id)
    row = await db.scalar(stmt)
    if row is None:
        return None
    return json.loads(row.answers_json)


# ---------------------------------------------------------------------------
# Result CRUD
# ---------------------------------------------------------------------------

async def upsert_result(
    db: AsyncSession,
    *,
    upload_id: str,
    result_dict: dict,
) -> Result:
    """Insert or replace the parsed result for an upload."""
    stmt = select(Result).where(Result.upload_id == upload_id)
    existing = await db.scalar(stmt)
    result_json = json.dumps(result_dict, ensure_ascii=False)

    if existing:
        existing.result_json = result_json
        await db.flush()
        return existing

    row = Result(upload_id=upload_id, result_json=result_json)
    db.add(row)
    await db.flush()
    return row


async def get_result(db: AsyncSession, upload_id: str) -> Optional[dict]:
    """Return the parsed result dict for an upload, or None."""
    stmt = select(Result).where(Result.upload_id == upload_id)
    row = await db.scalar(stmt)
    if row is None:
        return None
    return json.loads(row.result_json)


# ---------------------------------------------------------------------------
# Cleanup (expired transient uploads)
# ---------------------------------------------------------------------------

async def get_expired_uploads(db: AsyncSession) -> list[Upload]:
    """Return all transient uploads whose expires_at is in the past."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Upload)
        .where(Upload.transient == True)          # noqa: E712
        .where(Upload.expires_at != None)         # noqa: E711
        .where(Upload.expires_at <= now)
        .options(selectinload(Upload.upload_file))
    )
    rows = (await db.scalars(stmt)).all()
    return list(rows)


async def delete_upload(db: AsyncSession, upload_id: str) -> None:
    """
    Hard-delete an upload and all child rows (cascade).
    Physical file must be removed by the caller before this.
    """
    await db.execute(delete(Upload).where(Upload.id == upload_id))
    await db.flush()
