"""Settings saved on the Settings pages, over what the service was started with.

A setting comes from the first of three places that has it: a value a person
saved on a Settings page (a row in ``settings``), the service's environment
(``AGDASH_...``), and the default. Each answer says which, so a page can state
the evidence behind what it shows.

Only these are saved this way: how long history is kept, whether original
harness lines are archived, and whether the encoded data in them is kept. Where
the service listens, where its store is and which pages may talk to it decide
who can reach the data, so they stay where the service starts.
"""

from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session as DbSession

from agdash.config import get_settings
from agdash.db.models import Setting

Source = Literal["page", "environment", "default"]

RETENTION = "retention"
ARCHIVE = "archive"
LAST_REMOVAL = "retention_removal"
LAST_TRIM = "archive_trim"
MAX_DAYS = 3650


def _started_with(field: str) -> Source:
    return "environment" if field in get_settings().model_fields_set else "default"


def retention_days(db: DbSession) -> tuple[int | None, Source]:
    """Days of history to keep, or None to keep everything, and where that comes from."""
    row = db.get(Setting, RETENTION)
    if row is not None:
        return row.value.get("days"), "page"
    return get_settings().retention_days, _started_with("retention_days")


def keep_raw(db: DbSession) -> tuple[bool, Source]:
    """Whether lines read from now on are archived, and where that comes from."""
    row = db.get(Setting, ARCHIVE)
    if row is not None and "keep" in row.value:
        return bool(row.value["keep"]), "page"
    return get_settings().keep_raw, _started_with("keep_raw")


def keep_encoded(db: DbSession) -> tuple[bool, Source]:
    """Whether encoded data stays in the lines archived from now on, and where that comes from."""
    row = db.get(Setting, ARCHIVE)
    if row is not None and "encoded" in row.value:
        return bool(row.value["encoded"]), "page"
    return get_settings().keep_encoded, _started_with("keep_encoded")


def _save(db: DbSession, name: str, value: dict[str, Any]) -> None:
    row = db.get(Setting, name)
    if row is None:
        db.add(Setting(name=name, value=value))
    else:
        row.value = value


def save_retention(db: DbSession, days: int | None) -> None:
    """Keep ``days`` of history, or everything. Saving removes nothing by itself."""
    if days is not None and not 1 <= days <= MAX_DAYS:
        raise ValueError(f"keep between 1 and {MAX_DAYS} days, or everything")
    _save(db, RETENTION, {"days": days})


def _archive(db: DbSession, **changed: bool) -> None:
    """Change one choice about the archive and leave the other as it was saved."""
    row = db.get(Setting, ARCHIVE)
    _save(db, ARCHIVE, {**(row.value if row is not None else {}), **changed})


def save_keep_raw(db: DbSession, keep: bool) -> None:
    _archive(db, keep=keep)


def save_keep_encoded(db: DbSession, keep: bool) -> None:
    """Holds from the next lines read. What is archived already changes only when trimmed."""
    _archive(db, encoded=keep)


def last_trim(db: DbSession) -> dict[str, Any] | None:
    """How far the archive has been trimmed of encoded data, and what that saved."""
    row = db.get(Setting, LAST_TRIM)
    return dict(row.value) if row is not None else None


def note_trim(db: DbSession, *, at: datetime, through: int, chunks: int, saved_bytes: int) -> None:
    """Chunks up to ``through`` hold no encoded data any more; the counts add up over passes."""
    before = last_trim(db) or {}
    _save(
        db,
        LAST_TRIM,
        {
            "at": at.isoformat(),
            "through": through,
            "chunks": before.get("chunks", 0) + chunks,
            "saved_bytes": before.get("saved_bytes", 0) + saved_bytes,
        },
    )


def last_removal(db: DbSession) -> dict[str, Any] | None:
    """The last time sessions past the limit were removed here, or looked for."""
    row = db.get(Setting, LAST_REMOVAL)
    return dict(row.value) if row is not None else None


def note_removal(
    db: DbSession,
    *,
    at: datetime,
    sessions: int,
    events: int,
    automatic: bool,
    error: str | None = None,
) -> None:
    _save(
        db,
        LAST_REMOVAL,
        {
            "at": at.isoformat(),
            "sessions": sessions,
            "events": events,
            "automatic": automatic,
            "error": error,
        },
    )
