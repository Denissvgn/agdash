"""What agdash keeps: its store, the archive of original lines, and retention.

* ``/api/storage``: where the store is and how large, how many sessions it holds
  and since when, whether original harness lines are archived, and the
  retention this service runs with: the limit, and what it would remove now.
* ``/api/storage/archive``: how much the archive of original lines holds. It
  walks every archived chunk, which takes seconds on a large store, so it is
  asked for apart from the rest.

Both only read. Retention removes nothing here: ``agdash retention --apply``
does, when a person runs it. The store's address is never returned, since it
can hold a password; only the path of a store that is a file.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from agdash.api.routes import Db
from agdash.config import get_settings
from agdash.db.migrate import sqlite_file
from agdash.db.models import RawChunk, Session
from agdash.retention import removable

router = APIRouter()


class StoreFacts(BaseModel):
    file: str | None = Field(description="Null when the store is not a file on this host.")
    bytes: int | None = Field(description="The file and its write-ahead log; null with no file.")
    sessions: int
    first_started_at: datetime | None = Field(description="The earliest session's start.")


class Archive(BaseModel):
    enabled: bool = Field(description="Whether lines read from now on are archived.")
    chunks: int


class Retention(BaseModel):
    days: int | None = Field(description="Null keeps everything.")
    cutoff: datetime | None = Field(description="Sessions last active before this may go.")
    removable_sessions: int | None = Field(
        description="Sessions `agdash retention --apply` would remove now."
    )
    kept_alive: int | None = Field(
        description="Sessions older than the limit that stay because they, or a session "
        "of their family, may still run."
    )


class Storage(BaseModel):
    store: StoreFacts
    archive: Archive
    retention: Retention


class ArchiveSize(BaseModel):
    chunks: int
    bytes: int = Field(description="Compressed, as stored.")


def _size(path: Path) -> int | None:
    """The store's file and its write-ahead log, which holds what is not yet folded in."""
    if not path.exists():
        return None
    log = path.with_name(f"{path.name}-wal")
    return path.stat().st_size + (log.stat().st_size if log.exists() else 0)


@router.get("/storage", response_model=Storage, summary="The store, the archive and retention")
def storage(request: Request, db: Db) -> dict[str, dict]:
    """Where agdash keeps what it collected, and what its retention would remove now."""
    settings = get_settings()
    path = sqlite_file(request.app.state.store.database_url)
    retention: dict[str, object] = {
        "days": settings.retention_days,
        "cutoff": None,
        "removable_sessions": None,
        "kept_alive": None,
    }
    if settings.retention_days is not None:
        cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
        chosen, kept = removable(db, cutoff)
        retention |= {"cutoff": cutoff, "removable_sessions": len(chosen), "kept_alive": kept}
    return {
        "store": {
            "file": str(path) if path is not None else None,
            "bytes": _size(path) if path is not None else None,
            "sessions": db.scalar(select(func.count()).select_from(Session)) or 0,
            "first_started_at": db.scalar(select(func.min(Session.started_at))),
        },
        "archive": {
            "enabled": settings.keep_raw,
            "chunks": db.scalar(select(func.count()).select_from(RawChunk)) or 0,
        },
        "retention": retention,
    }


@router.get("/storage/archive", response_model=ArchiveSize, summary="How much the archive holds")
def archive_size(db: Db) -> dict[str, int]:
    """The archived original lines, compressed as they are stored."""
    chunks, stored = db.execute(
        select(func.count(), func.coalesce(func.sum(func.length(RawChunk.data)), 0))
    ).one()
    return {"chunks": chunks, "bytes": stored}
