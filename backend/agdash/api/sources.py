"""What agdash reads on this host: each harness, where it was looked for, and how it went.

``/api/sources`` answers, per harness:

* where its records are looked for, and whether each place exists here;
* how many of its files have been read, how far, and when last;
* how many sessions are recorded for it;
* every format version seen, with the records of an unknown kind and the lines
  that could not be read, so a harness that changed its format shows up here.

Whether a place exists is asked of the file system by name only; nothing is
opened. Everything else comes from agdash's own store.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from agdash.adapters.store import RECENT_FORMATS
from agdash.api.routes import Db
from agdash.db.models import IngestCursor, Session, SourceHealth
from agdash.platform import Host, current_host
from agdash.platform.roots import is_settings_file

router = APIRouter()

# What record_health stores for records that named no version.
NO_VERSION = "unknown"


class Location(BaseModel):
    path: str
    role: Literal["records", "settings"] = Field(
        description="records: a folder of session records. settings: a single file, from "
        "which only the sign-in method is read, from allowlisted fields."
    )
    found: bool


class FormatHealth(BaseModel):
    version: str | None = Field(description="Null for records that named no version.")
    unknown_records: int
    unreadable_lines: int
    first_seen_at: datetime
    last_seen_at: datetime
    recent: bool = Field(
        description="Something in this format was not understood and it was read recently: "
        "the harness may have changed its format."
    )


class Source(BaseModel):
    harness: str
    found: bool = Field(description="Whether a folder of its records exists on this host.")
    locations: list[Location]
    files: int = Field(description="Files of it read so far.")
    bytes_read: int
    last_read_at: datetime | None
    sessions: int
    format_version: str | None = Field(description="The version read most recently.")
    formats: list[FormatHealth] = Field(description="Most recently read first.")


class Sources(BaseModel):
    sources: list[Source]


def _host(request: Request) -> Host:
    return getattr(request.app.state, "host", None) or current_host()


@router.get("/sources", response_model=Sources, summary="What agdash reads on this host")
def sources(request: Request, db: Db) -> dict[str, list[dict]]:
    """Each harness: where it is looked for, what has been read, and what was not understood."""
    now = datetime.now(UTC)
    places = {
        harness.value: [
            {
                "path": str(root),
                "role": "settings" if is_settings_file(root) else "records",
                "found": root.exists(),
            }
            for root in roots
        ]
        for harness, roots in _host(request).harness_roots().items()
    }
    read = {
        harness: (files, offset or 0, last)
        for harness, files, offset, last in db.execute(
            select(
                IngestCursor.harness,
                func.count(),
                func.sum(IngestCursor.byte_offset),
                func.max(IngestCursor.updated_at),
            ).group_by(IngestCursor.harness)
        )
    }
    sessions = {
        harness: count
        for harness, count in db.execute(
            select(Session.harness, func.count()).group_by(Session.harness)
        )
    }
    formats: dict[str, list[dict]] = {}
    for row in db.scalars(select(SourceHealth).order_by(SourceHealth.last_seen_at.desc())):
        troubled = bool(row.unknown_record_count or row.parse_error_count)
        formats.setdefault(row.adapter, []).append(
            {
                "version": None if row.format_version == NO_VERSION else row.format_version,
                "unknown_records": row.unknown_record_count,
                "unreadable_lines": row.parse_error_count,
                "first_seen_at": row.first_seen_at,
                "last_seen_at": row.last_seen_at,
                "recent": troubled and now - row.last_seen_at <= RECENT_FORMATS,
            }
        )
    # A harness with nothing looked for here still shows what the store holds of it.
    known = [*places, *sorted((read.keys() | sessions.keys() | formats.keys()) - places.keys())]
    found = []
    for harness in known:
        files, offset, last = read.get(harness, (0, 0, None))
        seen = formats.get(harness, [])
        looked = places.get(harness, [])
        found.append(
            {
                "harness": harness,
                "found": any(place["found"] for place in looked if place["role"] == "records"),
                "locations": looked,
                "files": files,
                "bytes_read": offset,
                "last_read_at": last,
                "sessions": sessions.get(harness, 0),
                "format_version": next(
                    (item["version"] for item in seen if item["version"] is not None), None
                ),
                "formats": seen,
            }
        )
    return {"sources": found}
