"""What agdash keeps, and the two settings of it a person may change.

* ``GET /api/storage``: where the store is and how large, how many sessions it
  holds and since when, whether original harness lines are archived, and
  retention: the limit, where it comes from, what it would remove now, when
  sessions were last removed and when they next will be.
* ``GET /api/storage/archive``: how much the archive of original lines holds. It
  walks every archived chunk, which takes seconds on a large store, so it is
  asked for apart from the rest.
* ``GET /api/storage/parts``: what the store's file is made of: the archive,
  room left free inside the file, and the rest. Asked for apart from the rest
  for the same reason.
* ``GET /api/storage/retention/preview``: what a limit would remove, before it
  is saved.
* ``POST /api/storage/settings``: save the retention limit, whether original
  lines are kept, or whether the encoded data in them is. Saving removes nothing.
* ``POST /api/storage/archive/trim``: take encoded data out of the lines already
  archived. The service's trimmer does it, a few chunks at a time; this only asks.
* ``POST /api/storage/retention/removal``: remove what is past the limit now,
  and nothing last active after the time the person was shown. The service's
  keeper does it, a few sessions at a time; this only asks.

Those that write change agdash's own store and nothing else: a row in
``settings``, and, through the keeper and the trimmer, what agdash itself
collected. The
store's address is never returned, since it can hold a password; only the path
of a store that is a file.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session as DbSession

from agdash import saved
from agdash.api.routes import Db
from agdash.api.workchord import Writer
from agdash.archive import Trimmer, untrimmed
from agdash.db.migrate import sqlite_file
from agdash.db.models import RawChunk, Session
from agdash.retention import Keeper, removable

router = APIRouter()

WRITE_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/storage/settings"),
        ("POST", "/storage/retention/removal"),
        ("POST", "/storage/archive/trim"),
    }
)
"""The routes here that change anything, beside the WorkChord connector's own."""

Source = Literal["page", "environment", "default"]


class StoreFacts(BaseModel):
    file: str | None = Field(description="Null when the store is not a file on this host.")
    bytes: int | None = Field(description="The file and its write-ahead log; null with no file.")
    sessions: int
    first_started_at: datetime | None = Field(description="The earliest session's start.")


class Archive(BaseModel):
    enabled: bool = Field(description="Whether lines read from now on are archived.")
    source: Source = Field(description="Where that comes from: the Data page, or the service.")
    chunks: int
    keep_encoded: bool = Field(
        description="Whether long runs of encoded data stay in the lines archived from now on."
    )
    keep_encoded_source: Source
    untrimmed_chunks: int = Field(
        description="Chunks archived before, not yet looked at for encoded data."
    )
    trimming: bool = Field(description="Whether the service is trimming them now.")
    last_trim: dict[str, Any] | None = Field(
        description="When the archive was last trimmed, of how many chunks, and the bytes saved."
    )


class Removal(BaseModel):
    """The last time sessions past the limit were removed here, or looked for."""

    at: datetime
    sessions: int
    events: int
    automatic: bool = Field(description="False when a person asked for it.")
    error: str | None = Field(description="The kind of failure, when it did not finish.")


class Retention(BaseModel):
    days: int | None = Field(description="Null keeps everything.")
    source: Source
    cutoff: datetime | None = Field(description="Sessions last active before this may go.")
    removable_sessions: int | None = Field(description="Sessions a removal would take now.")
    kept_alive: int | None = Field(
        description="Sessions older than the limit that stay because they, or a session "
        "of their family, may still run."
    )
    automatic: bool = Field(description="Whether this service removes by itself, once a day.")
    running: bool = Field(description="A removal is under way, or about to start.")
    next_removal_at: datetime | None = Field(
        description="When sessions past the limit are next removed unasked; null with no "
        "limit, or where the service does not remove by itself."
    )
    last_removal: Removal | None


class Storage(BaseModel):
    store: StoreFacts
    archive: Archive
    retention: Retention


class ArchiveSize(BaseModel):
    chunks: int
    bytes: int = Field(description="Compressed, as stored.")


class Preview(BaseModel):
    days: int
    cutoff: datetime
    removable_sessions: int
    kept_alive: int


class SettingsIn(BaseModel):
    """Only what is named changes. ``retention_days`` null keeps everything."""

    retention_days: int | None = Field(default=None, ge=1, le=saved.MAX_DAYS)
    keep_raw: bool | None = None
    keep_encoded: bool | None = None

    @model_validator(mode="after")
    def something_named(self) -> "SettingsIn":
        if not self.model_fields_set & {"retention_days", "keep_raw", "keep_encoded"}:
            raise ValueError("name retention_days, keep_raw or keep_encoded")
        for name in ("keep_raw", "keep_encoded"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} is true or false")
        return self


class StoreParts(BaseModel):
    bytes: int | None = Field(description="The file and its write-ahead log; null with no file.")
    archive_bytes: int = Field(description="The archived original lines, compressed.")
    free_bytes: int | None = Field(
        description="Room inside the file that new data fills before the file grows."
    )
    rest_bytes: int | None = Field(
        description="Everything else: the events, their search index and the rest."
    )


class RemovalIn(BaseModel):
    before: AwareDatetime | None = Field(
        default=None,
        description="The time the person was shown in the question. Nothing last active "
        "after it is removed, however long the page was open; it can only narrow the limit.",
    )


def _size(path: Path) -> int | None:
    """The store's file and its write-ahead log, which holds what is not yet folded in."""
    if not path.exists():
        return None
    log = path.with_name(f"{path.name}-wal")
    return path.stat().st_size + (log.stat().st_size if log.exists() else 0)


def _keeper(request: Request) -> Keeper | None:
    return getattr(request.app.state, "retention", None)


def _trimmer(request: Request) -> Trimmer | None:
    return getattr(request.app.state, "trimmer", None)


def _reach(db: DbSession, days: int) -> dict[str, Any]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    chosen, kept = removable(db, cutoff)
    return {"cutoff": cutoff, "removable_sessions": len(chosen), "kept_alive": kept}


def _storage(request: Request, db: DbSession) -> dict[str, dict]:
    path = sqlite_file(request.app.state.store.database_url)
    days, source = saved.retention_days(db)
    keep, keep_source = saved.keep_raw(db)
    encoded, encoded_source = saved.keep_encoded(db)
    trimmer = _trimmer(request)
    keeper = _keeper(request)
    standing = keeper.standing() if keeper is not None else None
    retention: dict[str, Any] = {
        "days": days,
        "source": source,
        "cutoff": None,
        "removable_sessions": None,
        "kept_alive": None,
        "automatic": keeper is not None,
        "running": bool(standing and standing.running),
        "next_removal_at": standing.next_at if standing and days is not None else None,
        "last_removal": saved.last_removal(db),
    }
    if days is not None:
        retention |= _reach(db, days)
    return {
        "store": {
            "file": str(path) if path is not None else None,
            "bytes": _size(path) if path is not None else None,
            "sessions": db.scalar(select(func.count()).select_from(Session)) or 0,
            "first_started_at": db.scalar(select(func.min(Session.started_at))),
        },
        "archive": {
            "enabled": keep,
            "source": keep_source,
            "chunks": db.scalar(select(func.count()).select_from(RawChunk)) or 0,
            "keep_encoded": encoded,
            "keep_encoded_source": encoded_source,
            "untrimmed_chunks": untrimmed(db),
            "trimming": bool(trimmer and trimmer.running()),
            "last_trim": saved.last_trim(db),
        },
        "retention": retention,
    }


@router.get("/storage", response_model=Storage, summary="The store, the archive and retention")
def storage(request: Request, db: Db) -> dict[str, dict]:
    """Where agdash keeps what it collected, and what its retention would remove now."""
    return _storage(request, db)


@router.get("/storage/archive", response_model=ArchiveSize, summary="How much the archive holds")
def archive_size(db: Db) -> dict[str, int]:
    """The archived original lines, compressed as they are stored."""
    chunks, stored = db.execute(
        select(func.count(), func.coalesce(func.sum(func.length(RawChunk.data)), 0))
    ).one()
    return {"chunks": chunks, "bytes": stored}


@router.get("/storage/parts", response_model=StoreParts, summary="What the store is made of")
def store_parts(request: Request, db: Db) -> dict[str, int | None]:
    """The store's file by what fills it: the archive, the room free inside it, and
    what remains, which is the events, their search index and everything else. The
    search index is not told apart: sizing it would read all of it."""
    path = sqlite_file(request.app.state.store.database_url)
    total = _size(path) if path is not None else None
    archive = db.scalar(select(func.coalesce(func.sum(func.length(RawChunk.data)), 0))) or 0
    free = None
    if path is not None:
        free = (db.scalar(text("PRAGMA freelist_count")) or 0) * (
            db.scalar(text("PRAGMA page_size")) or 0
        )
    rest = None if total is None else max(0, total - archive - (free or 0))
    return {"bytes": total, "archive_bytes": archive, "free_bytes": free, "rest_bytes": rest}


@router.get(
    "/storage/retention/preview", response_model=Preview, summary="What a limit would remove"
)
def preview(db: Db, days: Annotated[int, Query(ge=1, le=saved.MAX_DAYS)]) -> dict[str, Any]:
    """What keeping ``days`` of history would remove now. Nothing is saved or removed."""
    return {"days": days, **_reach(db, days)}


@router.post("/storage/settings", response_model=Storage, summary="Save a setting")
def save(request: Request, body: SettingsIn, db: Writer) -> dict[str, dict]:
    """Save the retention limit, or whether original lines are kept. Nothing is removed."""
    if "retention_days" in body.model_fields_set:
        saved.save_retention(db, body.retention_days)
    if body.keep_raw is not None:
        saved.save_keep_raw(db, body.keep_raw)
    if body.keep_encoded is not None:
        saved.save_keep_encoded(db, body.keep_encoded)
    db.commit()
    keeper = _keeper(request)
    if keeper is not None and "retention_days" in body.model_fields_set:
        keeper.limit_changed()
    return _storage(request, db)


@router.post(
    "/storage/retention/removal",
    response_model=Storage,
    status_code=202,
    summary="Remove what is past the limit now",
    responses={409: {"description": "No limit is set."}, 503: {"description": "No keeper."}},
)
def remove_now(request: Request, db: Db, body: RemovalIn | None = None) -> dict[str, dict]:
    """Ask the service to remove the sessions past the limit now. It answers at once;
    ``retention.running`` says while it is under way and ``last_removal`` what it did."""
    keeper = _keeper(request)
    if keeper is None:
        raise HTTPException(503, "this service removes nothing by itself; use agdash retention")
    if saved.retention_days(db)[0] is None:
        raise HTTPException(409, "no limit is set, so there is nothing to remove")
    keeper.ask(body.before if body is not None else None)
    return _storage(request, db)


@router.post(
    "/storage/archive/trim",
    response_model=Storage,
    status_code=202,
    summary="Take encoded data out of what is archived",
    responses={409: {"description": "Encoded data is kept."}, 503: {"description": "No trimmer."}},
)
def trim_now(request: Request, db: Db) -> dict[str, dict]:
    """Ask the service to take long runs of encoded data out of the lines archived
    before. It cannot be undone. It answers at once; ``archive.trimming`` says while it
    is under way and ``archive.untrimmed_chunks`` how many are left."""
    trimmer = _trimmer(request)
    if trimmer is None:
        raise HTTPException(503, "this service trims nothing by itself; use agdash archive")
    if saved.keep_encoded(db)[0]:
        raise HTTPException(409, "encoded data is kept, so there is nothing to take out")
    trimmer.ask()
    return _storage(request, db)
