"""Retention: forget old sessions, but never one that may still be running.

Off unless a number of days is configured (``AGDASH_RETENTION_DAYS``). A session
is removed only when all of these hold:

* its last activity is older than the limit;
* its state is not active, waiting or idle (``unknown`` and ``ended`` qualify);
* no process of it was seen within the limit;
* the same holds for every session in its family (its parent chain and all
  subagents), so a family is kept or removed as a whole.

Removing a session removes its events, processes, state changes, usage, links
and archived raw lines that no remaining session uses. Ingest cursors stay, so a
removed session is not read back in from files that still exist.

    python -m agdash.retention [--days N]            what would be removed
    python -m agdash.retention [--days N] --apply    remove it
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.config import database_url, get_settings
from agdash.db.base import UTCDateTime
from agdash.db.migrate import sync_database_url
from agdash.db.models import (
    Event,
    IdentityLink,
    MappingRule,
    ProjectLink,
    RawChunk,
    RunLink,
    Session,
    SessionProcess,
    SessionStateChange,
    UsageSnapshot,
)
from agdash.vocab import SessionState

ALIVE = (SessionState.ACTIVE, SessionState.WAITING, SessionState.IDLE)
BATCH = 500


@dataclass(frozen=True)
class Outcome:
    cutoff: datetime
    sessions: int
    events: int
    raw_chunks: int
    kept_because_alive: int


def removable(db: DbSession, cutoff: datetime) -> tuple[list[int], int]:
    """Sessions that may go, family by family, and how many old ones were kept as alive."""
    activity = func.coalesce(Session.last_activity_at, Session.created_at, type_=UTCDateTime())
    recent_process = select(SessionProcess.session_id).where(SessionProcess.last_seen_at >= cutoff)
    old = set(
        db.scalars(
            select(Session.id).where(
                activity < cutoff,
                Session.state.not_in(ALIVE),
                Session.id.not_in(recent_process),
            )
        )
    )
    old_any = db.scalar(select(func.count()).select_from(Session).where(activity < cutoff)) or 0
    parents = {
        sid: parent for sid, parent in db.execute(select(Session.id, Session.parent_session_id))
    }

    def root(session_id: int) -> int:
        seen = set()
        while parents.get(session_id) is not None and session_id not in seen:
            seen.add(session_id)
            session_id = parents[session_id]
        return session_id

    families: dict[int, list[int]] = {}
    for session_id in parents:
        families.setdefault(root(session_id), []).append(session_id)
    chosen = sorted(
        member
        for members in families.values()
        if all(member in old for member in members)
        for member in members
    )
    return chosen, old_any - len(chosen)


def apply_retention(
    db: DbSession, days: int, *, now: datetime | None = None, dry_run: bool = False
) -> Outcome:
    if days < 1:
        raise ValueError("keep at least one day")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    chosen, kept = removable(db, cutoff)
    events = raw = 0
    for start in range(0, len(chosen), BATCH):
        batch = chosen[start : start + BATCH]
        events += (
            db.scalar(select(func.count()).select_from(Event).where(Event.session_id.in_(batch)))
            or 0
        )
        files = set(
            db.scalars(select(Event.source_file).where(Event.session_id.in_(batch)).distinct())
        ) - {None}
        if dry_run:
            continue
        for table in (
            SessionProcess,
            SessionStateChange,
            UsageSnapshot,
            RunLink,
            IdentityLink,
            ProjectLink,
            MappingRule,
            Event,
        ):
            db.execute(delete(table).where(table.session_id.in_(batch)))
        db.execute(delete(Session).where(Session.id.in_(batch)))
        still_used = set(
            db.scalars(select(Event.source_file).where(Event.source_file.in_(files)).distinct())
        )
        orphaned = files - still_used
        if orphaned:
            raw += db.execute(delete(RawChunk).where(RawChunk.source_file.in_(orphaned))).rowcount
    return Outcome(cutoff, len(chosen), events, raw, kept)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agdash-retention", description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="remove; without it, only report")
    args = parser.parse_args(argv)
    settings = get_settings()
    days = args.days if args.days is not None else settings.retention_days
    if days is None:
        print("Retention is off: set AGDASH_RETENTION_DAYS or pass --days.")
        return 0
    engine = create_engine(sync_database_url(database_url()))
    try:
        with sessionmaker(engine)() as db:
            outcome = apply_retention(db, days, dry_run=not args.apply)
            if args.apply:
                db.commit()
    finally:
        engine.dispose()
    verb = "Removed" if args.apply else "Would remove"
    print(
        f"{verb} {outcome.sessions} sessions with {outcome.events} events"
        f" last active before {outcome.cutoff:%Y-%m-%d %H:%M} UTC"
        + (f" and {outcome.raw_chunks} archived chunks" if args.apply else "")
        + f"; kept {outcome.kept_because_alive} older sessions that may still run."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
