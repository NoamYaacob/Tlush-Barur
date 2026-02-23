"""
Background cleanup job.

Runs on startup (once) and then every CLEANUP_INTERVAL_SECONDS to:
  1. Query for expired transient uploads (expires_at <= now).
  2. Delete the physical file from disk.
  3. Hard-delete the DB row (cascades to all child rows).

No PII is logged. Only upload_id and count.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from app.db.crud import delete_upload, get_expired_uploads
from app.db.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS: float = float(os.getenv("CLEANUP_INTERVAL_SECONDS", "60"))


async def cleanup_expired_uploads() -> int:
    """
    Run a single cleanup pass.
    Returns the number of uploads deleted.
    """
    deleted = 0
    async with AsyncSessionLocal() as db:
        try:
            expired = await get_expired_uploads(db)
            for upload in expired:
                upload_id = upload.id

                # Remove physical file if it exists
                if upload.upload_file and upload.upload_file.path:
                    file_path = Path(upload.upload_file.path)
                    try:
                        if file_path.exists():
                            file_path.unlink()
                            logger.info("Deleted file for upload_id=%s", upload_id)
                        # Also remove the upload directory if empty
                        parent = file_path.parent
                        if parent.exists() and not any(parent.iterdir()):
                            parent.rmdir()
                    except OSError as exc:
                        logger.warning(
                            "Could not remove file for upload_id=%s: %s", upload_id, exc
                        )

                # Delete DB row (cascades to upload_files, quick_answers, results)
                await delete_upload(db, upload_id)
                deleted += 1
                logger.info("Expired upload deleted: upload_id=%s", upload_id)

            await db.commit()
        except Exception as exc:
            logger.exception("Cleanup pass failed: %s", exc)
            await db.rollback()

    if deleted:
        logger.info("Cleanup pass: deleted %d expired upload(s)", deleted)
    return deleted


async def run_cleanup_loop() -> None:
    """
    Infinite loop: run cleanup_expired_uploads(), sleep, repeat.
    Designed to run as an asyncio task from the FastAPI lifespan.
    """
    logger.info(
        "Cleanup loop started (interval=%ss, TTL from TRANSIENT_TTL_HOURS)",
        CLEANUP_INTERVAL_SECONDS,
    )
    while True:
        try:
            await cleanup_expired_uploads()
        except Exception as exc:
            logger.exception("Unhandled error in cleanup loop: %s", exc)
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
