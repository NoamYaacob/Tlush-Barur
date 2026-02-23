"""
Async database engine + session factory.

Supports:
  - PostgreSQL via DATABASE_URL=postgresql+asyncpg://...
  - SQLite (dev fallback) via DATABASE_URL=sqlite+aiosqlite:///./tlush_barur.db
    or when DATABASE_URL is unset / still set to the placeholder.

The module exposes:
  engine          – AsyncEngine
  AsyncSessionLocal – async_sessionmaker
  get_db()        – FastAPI dependency that yields an AsyncSession
  init_db()       – create tables if using SQLite without Alembic (dev only)
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# ---------------------------------------------------------------------------
# Resolve DATABASE_URL
# ---------------------------------------------------------------------------

_RAW_URL = os.getenv("DATABASE_URL", "")

# Detect the placeholder set in .env.example and fall back to SQLite
_PLACEHOLDER = "postgresql://user:password@127.0.0.1:5432/tlush_barur"

if not _RAW_URL or _RAW_URL.strip() == _PLACEHOLDER:
    # SQLite dev fallback – stored in backend/.data/
    _DATA_DIR = Path(__file__).resolve().parent.parent.parent / ".data"
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_URL: str = f"sqlite+aiosqlite:///{_DATA_DIR / 'tlush_barur.db'}"
    _DIALECT = "sqlite"
else:
    # Support shorthand postgresql:// → asyncpg driver
    _url = _RAW_URL
    if _url.startswith("postgresql://") or _url.startswith("postgres://"):
        _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)
        _url = _url.replace("postgres://", "postgresql+asyncpg://", 1)
    DATABASE_URL = _url
    _DIALECT = "postgresql"

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

_CONNECT_ARGS: dict = {}
if _DIALECT == "sqlite":
    # SQLite needs check_same_thread=False for async use
    _CONNECT_ARGS = {"check_same_thread": False}

engine = create_async_engine(
    DATABASE_URL,
    echo=False,           # set True to log SQL during debugging
    future=True,
    connect_args=_CONNECT_ARGS,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Base for ORM models
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_db() -> AsyncSession:  # type: ignore[return]
    """Yield an async DB session; rolls back on exception, always closes."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Dev-only: create all tables via SQLAlchemy metadata (SQLite only)
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create all tables from ORM metadata. Used for SQLite dev; Postgres uses Alembic."""
    from app.db import orm  # import here to ensure models are registered  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
