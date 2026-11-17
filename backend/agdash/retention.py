"""Retention: forget old sessions, but never one that may still be running.

Off unless a number of days is set, on the Data page or as
``AGDASH_RETENTION_DAYS``. A session is removed only when all of these hold:

* its last activity is older than the limit;
* its state is not active, waiting or idle (``unknown`` and ``ended`` qualify);
* no process of it was seen within the limit;
* the same holds for every session in its family (its parent chain and all
  subagents), so a family is kept or removed as a whole.

Removing a session removes its events, processes, state changes, usage, links
and archived raw lines that no remaining session uses. Ingest cursors stay, so a
removed session is not read back in from files that still exist.

Two things remove. The service does, through its :class:`Keeper`: once a day,
and when a person asks from the Data page. And a person does, from the command
line:

    python -m agdash.retention [--days N]            what would be removed
    python -m agdash.retention [--days N] --apply    remove it

Only agdash's own store is touched either way; a harness's files never are.
"""

import argparse
import logging
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash import saved
from agdash.config import database_url
from agdash.db.base import UTCDateTime
from agdash.db.migrate import sync_database_url
from agdash.db.models import (
    Event,
    IdentityLink,
    MappingRule,
    ProjectLink,
    PublishedLink,
    RawChunk,
    RunLink,
    Session,
    SessionProcess,
    SessionStateChange,
    UsageSnapshot,
)
from agdash.vocab import SessionState

log = logging.getLogger("agdash.retention")

ALIVE = (SessionState.ACTIVE, SessionState.WAITING, SessionState.IDLE)
BATCH = 500
# Inside the service, sessions go a few at a time, each step its own transaction with
# a pause after it, so the collector's writes fit in between. A step ends at this many
# sessions or this many events, whichever comes first: about a second's work, well
# inside the time a writer waits for the store.
STEP = 25
STEP_EVENTS = 20_000
PAUSE_SECONDS = 0.2
DAILY = timedelta(days=1)
# After the service starts, the read of what the harnesses wrote meanwhile goes first.
SETTLE = timedelta(minutes=5)
# After a limit is saved, an hour to think again before anything is removed unasked.
GRACE = timedelta(hours=1)
# How often the keeper looks up from its wait, to notice the service stopping.
LOOK_SECONDS = 2.0


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


def _remove(db: DbSession, batch: Sequence[int], *, dry_run: bool = False) -> tuple[int, int]:
    """Remove these sessions and all that hangs on them; the events and raw chunks gone."""
    events = (
        db.scalar(select(func.count()).select_from(Event).where(Event.session_id.in_(batch))) or 0
    )
    if dry_run:
        return events, 0
    files = set(
        db.scalars(select(Event.source_file).where(Event.session_id.in_(batch)).distinct())
    ) - {None}
    for table in (
        SessionProcess,
        SessionStateChange,
        UsageSnapshot,
        RunLink,
        IdentityLink,
        ProjectLink,
        PublishedLink,
        MappingRule,
        Event,
    ):
        db.execute(delete(table).where(table.session_id.in_(batch)))
    db.execute(delete(Session).where(Session.id.in_(batch)))
    still_used = set(
        db.scalars(select(Event.source_file).where(Event.source_file.in_(files)).distinct())
    )
    orphaned = files - still_used
    raw = (
        db.execute(delete(RawChunk).where(RawChunk.source_file.in_(orphaned))).rowcount
        if orphaned
        else 0
    )
    return events, raw


def apply_retention(
    db: DbSession, days: int, *, now: datetime | None = None, dry_run: bool = False
) -> Outcome:
    """Everything past the limit, in the caller's one transaction."""
    if days < 1:
        raise ValueError("keep at least one day")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    chosen, kept = removable(db, cutoff)
    events = raw = 0
    for start in range(0, len(chosen), BATCH):
        gone, chunks = _remove(db, chosen[start : start + BATCH], dry_run=dry_run)
        events += gone
        raw += chunks
    return Outcome(cutoff, len(chosen), events, raw, kept)


def _steps(db: DbSession, chosen: Sequence[int], step: int, step_events: int) -> list[list[int]]:
    """``chosen`` cut into steps of at most ``step`` sessions and about ``step_events`` events.

    One session with more events than that is a step of its own.
    """
    sizes: dict[int, int] = {}
    for start in range(0, len(chosen), BATCH):
        sizes.update(
            db.execute(
                select(Event.session_id, func.count())
                .where(Event.session_id.in_(chosen[start : start + BATCH]))
                .group_by(Event.session_id)
            ).all()
        )
    steps: list[list[int]] = []
    weight = 0
    for session_id in chosen:
        size = sizes.get(session_id, 0)
        if not steps or len(steps[-1]) >= step or weight + size > step_events:
            steps.append([])
            weight = 0
        steps[-1].append(session_id)
        weight += size
    return steps


def remove_in_steps(
    sessions: sessionmaker[DbSession],
    days: int,
    *,
    now: datetime | None = None,
    not_after: datetime | None = None,
    stop: threading.Event | None = None,
    step: int = STEP,
    step_events: int = STEP_EVENTS,
    pause_seconds: float = PAUSE_SECONDS,
) -> Outcome:
    """Everything past the limit, a few sessions per transaction, beside a running collector.

    ``not_after`` is the time a person was shown when they asked: nothing last active
    after it goes, however long ago they were shown it. It can only narrow the limit.
    Stopped part-way, what is left is still past the limit and goes the next time.
    """
    if days < 1:
        raise ValueError("keep at least one day")
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    if not_after is not None and not_after < cutoff:
        cutoff = not_after
    with sessions() as db:
        chosen, kept = removable(db, cutoff)
        steps = _steps(db, chosen, step, step_events)
    removed = events = raw = 0
    for batch in steps:
        if stop is not None and stop.is_set():
            break
        with sessions.begin() as db:
            gone, chunks = _remove(db, batch)
        removed += len(batch)
        events += gone
        raw += chunks
        if stop is not None:
            stop.wait(pause_seconds)
    return Outcome(cutoff, removed, events, raw, kept)


@dataclass(frozen=True)
class Standing:
    """What the keeper is doing: removing now, and when it next will unasked."""

    running: bool
    next_at: datetime | None


class Keeper:
    """Applies retention inside the service: once a day, and when a person asks.

    It reads the limit each time, so one saved on the Data page holds from the next
    pass on. With no limit a pass does nothing. Each pass that had a limit is noted in
    the store, so the page can say when sessions were last removed and how many.
    """

    def __init__(
        self,
        sessions: sessionmaker[DbSession],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._lock = threading.Lock()
        self._asked = threading.Event()
        self._running = False
        self._next_at: datetime | None = None
        self._asked_before: datetime | None = None

    def standing(self) -> Standing:
        with self._lock:
            return Standing(self._running or self._asked.is_set(), self._next_at)

    def ask(self, before: datetime | None = None) -> None:
        """Remove what is past the limit now, without waiting for the day's pass.

        ``before`` is the time the person was shown: nothing last active after it goes.
        """
        with self._lock:
            self._asked_before = before
        self._asked.set()

    def limit_changed(self) -> None:
        """A limit was saved: nothing goes unasked for an hour, to leave time to think again."""
        with self._lock:
            soonest = self._clock() + GRACE
            if self._next_at is None or self._next_at < soonest:
                self._next_at = soonest

    def _first(self) -> datetime:
        """The first unasked pass: a day after the last, and never right at start-up."""
        now = self._clock()
        with self._sessions() as db:
            last = saved.last_removal(db)
        soonest = now + SETTLE
        if last is None or not last.get("automatic"):
            return soonest
        return max(soonest, datetime.fromisoformat(last["at"]) + DAILY)

    def keep(
        self,
        *,
        automatic: bool,
        stop: threading.Event | None = None,
        before: datetime | None = None,
    ) -> Outcome | None:
        """One pass. None when no limit is set, so nothing was looked for."""
        with self._sessions() as db:
            days, _source = saved.retention_days(db)
        if days is None:
            return None
        with self._lock:
            self._running = True
        try:
            outcome = remove_in_steps(
                self._sessions, days, now=self._clock(), not_after=before, stop=stop
            )
            self._note(outcome.sessions, outcome.events, automatic, None)
            log.info(
                "retention: removed %d sessions and %d events last active before %s",
                outcome.sessions,
                outcome.events,
                outcome.cutoff.isoformat(timespec="minutes"),
            )
            return outcome
        except Exception as error:
            # The name of what went wrong, never its text: that can quote a statement.
            self._note(0, 0, automatic, type(error).__name__)
            raise
        finally:
            with self._lock:
                self._running = False

    def _note(self, sessions: int, events: int, automatic: bool, error: str | None) -> None:
        with self._sessions.begin() as db:
            saved.note_removal(
                db,
                at=self._clock(),
                sessions=sessions,
                events=events,
                automatic=automatic,
                error=error,
            )

    def run(self, stop: threading.Event) -> list[threading.Thread]:
        def loop() -> None:
            try:
                first = self._first()
            except Exception:
                log.exception("could not read when sessions were last removed")
                first = self._clock() + SETTLE
            with self._lock:
                self._next_at = first
            while not stop.is_set():
                asked = self._asked.wait(LOOK_SECONDS)
                if stop.is_set():
                    break
                with self._lock:
                    due = self._next_at is not None and self._clock() >= self._next_at
                if not asked and not due:
                    continue
                with self._lock:
                    before, self._asked_before = self._asked_before, None
                try:
                    self.keep(automatic=not asked, stop=stop, before=before if asked else None)
                except Exception:
                    log.exception("removing sessions past the limit failed; it is tried again")
                finally:
                    self._asked.clear()
                    if not asked:
                        with self._lock:
                            self._next_at = self._clock() + DAILY

        thread = threading.Thread(target=loop, name="agdash-retention", daemon=True)
        thread.start()
        return [thread]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agdash-retention", description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--apply", action="store_true", help="remove; without it, only report")
    args = parser.parse_args(argv)
    engine = create_engine(sync_database_url(database_url()))
    try:
        with sessionmaker(engine)() as db:
            days = args.days if args.days is not None else saved.retention_days(db)[0]
            if days is None:
                print(
                    "Retention is off: set a limit on the Data page, "
                    "set AGDASH_RETENTION_DAYS or pass --days."
                )
                return 0
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
