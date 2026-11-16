"""Storing what an adapter parsed: sessions, events and source health.

Shared by every adapter. Each event's sequence is derived from the byte offset
of the line it came from and its position within that line, so reading a line
again finds the same sequence and adds nothing.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from agdash.db.base import utcnow
from agdash.db.models import Event, Session, SourceHealth
from agdash.events import EventContractError, validate_event
from agdash.redaction import redact_text, redact_value
from agdash.vocab import AuthBasis, AuthMethod, EventKind, ParentBasis, Surface, ThoughtsCoverage

EVENTS_PER_LINE = 32
MAX_TEXT = 64 * 1024
INITIAL_TASK_LIMIT = 2000


@dataclass(frozen=True)
class ParsedEvent:
    kind: EventKind
    payload: dict[str, Any]
    occurred_at: datetime | None = None
    thoughts_coverage: ThoughtsCoverage | None = None


@dataclass
class SessionFacts:
    """What one record says about its session. ``None`` means "not stated here"."""

    cwd: str | None = None
    branch: str | None = None
    surface: Surface | None = None
    permission_mode: str | None = None
    effort: str | None = None
    model: str | None = None
    title: str | None = None
    title_rank: int = 0  # a user-chosen title outranks a generated one
    initial_task: str | None = None
    timestamp: datetime | None = None
    # Only what the session's own records prove; settings are read elsewhere.
    auth_method: AuthMethod | None = None


@dataclass
class ParsedRecord:
    events: list[ParsedEvent] = field(default_factory=list)
    facts: SessionFacts = field(default_factory=SessionFacts)
    unknown_type: str | None = None


def bounded(text: str, limit: int = MAX_TEXT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def find_or_create_session(db: DbSession, harness: str, native_id: str) -> Session:
    found = db.scalars(
        select(Session).where(Session.harness == harness, Session.native_id == native_id)
    ).one_or_none()
    if found is None:
        found = Session(harness=harness, native_id=native_id)
        db.add(found)
        db.flush()
    return found


def link_parent(db: DbSession, child: Session, harness: str, parent_native_id: str) -> None:
    parent = find_or_create_session(db, harness, parent_native_id)
    child.parent_session_id = parent.id
    child.parent_basis = ParentBasis.HARNESS_RECORD


def apply_facts(session: Session, facts: SessionFacts, title_ranks: dict[int, int]) -> None:
    if facts.cwd:
        session.cwd = facts.cwd
    if facts.branch:
        session.branch = facts.branch
    if facts.surface:
        session.surface = facts.surface
    if facts.permission_mode:
        session.permission_mode = facts.permission_mode
    if facts.effort:
        session.effort = facts.effort
    if facts.model and facts.model not in (session.models or []):
        session.models = [*(session.models or []), facts.model]
    if facts.title and facts.title_rank >= title_ranks.get(session.id, 0):
        session.title = redact_text(facts.title)[:500]
        title_ranks[session.id] = facts.title_rank
    if facts.initial_task and not session.initial_task:
        session.initial_task = redact_text(facts.initial_task)[:INITIAL_TASK_LIMIT]
    if facts.auth_method:  # the session's own records outrank the settings
        session.auth_method = facts.auth_method.value
        session.account_label = None
        session.auth_basis = AuthBasis.TRANSCRIPT
    if facts.timestamp:
        if session.started_at is None or facts.timestamp < session.started_at:
            session.started_at = facts.timestamp
        if session.last_activity_at is None or facts.timestamp > session.last_activity_at:
            session.last_activity_at = facts.timestamp


# Stored sequences are looked up a window of file offsets at a time, not one query
# per event: a batch of lines then costs a query or two. The window lives on the
# database session and belongs to one transaction, so it never outlasts the read.
_WINDOW_SEQUENCES = 16 * 1024 * 1024 * EVENTS_PER_LINE
_KNOWN = "agdash.known_sequences"


def _known_sequences(db: DbSession, session_id: int, sequence: int) -> set[int]:
    transaction = db.get_transaction()
    cached = db.info.get(_KNOWN)
    if cached is None or cached[0] is not transaction:
        cached = db.info[_KNOWN] = (transaction, {})
    windows = cached[1]
    window = windows.get(session_id)
    if window is None or not window[0] <= sequence < window[1]:
        low, high = sequence, sequence + _WINDOW_SEQUENCES
        found = db.scalars(
            select(Event.sequence).where(
                Event.session_id == session_id, Event.sequence >= low, Event.sequence < high
            )
        )
        window = (low, high, set(found))
        windows[session_id] = window
    return window[2]


def store_events(
    db: DbSession,
    session: Session,
    offset: int,
    events: Iterable[ParsedEvent],
    source_file: str,
    *,
    first_index: int = 0,
) -> int:
    """Store the events of one line. Returns how many were new; invalid events raise.

    ``first_index`` places the first event further along the line's sequence range,
    for sources that are not lines and keep several kinds of event apart.
    """
    stored = 0
    for index, event in enumerate(events, start=first_index):
        if index >= EVENTS_PER_LINE:
            break
        payload = redact_value(event.payload)
        coverage = event.thoughts_coverage
        validate_event(event.kind, payload, coverage)
        sequence = offset * EVENTS_PER_LINE + index
        known = _known_sequences(db, session.id, sequence)
        if sequence in known:
            continue
        known.add(sequence)
        db.add(
            Event(
                session_id=session.id,
                sequence=sequence,
                occurred_at=event.occurred_at,
                kind=event.kind,
                payload=payload,
                thoughts_coverage=coverage,
                source_file=source_file,
                source_offset=offset,
            )
        )
        stored += 1
    return stored


def record_health(
    db: DbSession, adapter: str, version: str | None, unknown: int, errors: int
) -> None:
    if not unknown and not errors and version is None:
        return
    row = db.scalars(
        select(SourceHealth).where(
            SourceHealth.adapter == adapter, SourceHealth.format_version == (version or "unknown")
        )
    ).one_or_none()
    if row is None:
        row = SourceHealth(
            adapter=adapter,
            format_version=version or "unknown",
            unknown_record_count=0,
            parse_error_count=0,
        )
        db.add(row)
    row.unknown_record_count += unknown
    row.parse_error_count += errors
    row.last_seen_at = utcnow()


__all__ = [
    "EventContractError",
    "ParsedEvent",
    "ParsedRecord",
    "SessionFacts",
    "apply_facts",
    "bounded",
    "find_or_create_session",
    "link_parent",
    "record_health",
    "store_events",
]
