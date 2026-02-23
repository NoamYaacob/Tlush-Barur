"""
SQLAlchemy ORM models for Talush Barur.

Tables:
  uploads       – one row per file upload
  upload_files  – physical file info (path, sha256)
  quick_answers – user answers JSON per upload
  results       – parsed slip result JSON per upload
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def _utcnow() -> datetime:
    """Return current UTC time (timezone-aware)."""
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------

class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)          # UUID
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="awaiting_questions")
    progress_stage: Mapped[str] = mapped_column(String(128), nullable=False, default="ממתין לתשובות")
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transient: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    redact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    save_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    # relationships
    upload_file: Mapped[UploadFile | None] = relationship(
        "UploadFile", back_populates="upload", uselist=False, cascade="all, delete-orphan"
    )
    quick_answers: Mapped[QuickAnswers | None] = relationship(
        "QuickAnswers", back_populates="upload", uselist=False, cascade="all, delete-orphan"
    )
    result: Mapped[Result | None] = relationship(
        "Result", back_populates="upload", uselist=False, cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# upload_files
# ---------------------------------------------------------------------------

class UploadFile(Base):
    __tablename__ = "upload_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    upload: Mapped[Upload] = relationship("Upload", back_populates="upload_file")


# ---------------------------------------------------------------------------
# quick_answers
# ---------------------------------------------------------------------------

class QuickAnswers(Base):
    __tablename__ = "quick_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    answers_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    upload: Mapped[Upload] = relationship("Upload", back_populates="quick_answers")


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------

class Result(Base):
    __tablename__ = "results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    result_json: Mapped[str] = mapped_column(Text, nullable=False)   # JSON string
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    upload: Mapped[Upload] = relationship("Upload", back_populates="result")
