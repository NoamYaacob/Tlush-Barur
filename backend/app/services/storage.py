"""
Transient file-based storage for upload state.
Each upload is stored as:
  .data/uploads/{upload_id}/state.json   – UploadState JSON
  .data/uploads/{upload_id}/original.<ext> – raw uploaded file

No PII (file bytes, content) is ever written to logs.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from app.models.schemas import UploadState

logger = logging.getLogger(__name__)

# Root storage directory relative to the backend package root
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = _BACKEND_ROOT / ".data" / "uploads"


def _state_path(upload_id: str) -> Path:
    return DATA_DIR / upload_id / "state.json"


def _upload_dir(upload_id: str) -> Path:
    return DATA_DIR / upload_id


def ensure_upload_dir(upload_id: str) -> Path:
    """Create the per-upload directory, return its path."""
    d = _upload_dir(upload_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_state(state: UploadState) -> None:
    """Persist UploadState to disk as JSON."""
    path = _state_path(state.upload_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def load_state(upload_id: str) -> Optional[UploadState]:
    """Load UploadState from disk. Returns None if not found."""
    path = _state_path(upload_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return UploadState.model_validate(data)
    except Exception as exc:
        logger.error("Failed to load state for %s: %s", upload_id, exc)
        return None


def file_path_for_upload(upload_id: str, extension: str) -> Path:
    """Return the canonical path for the original uploaded file."""
    return _upload_dir(upload_id) / f"original.{extension}"


def upload_exists(upload_id: str) -> bool:
    return _state_path(upload_id).exists()
