"""One session over time, its states, and the last half hour of every live session.

Three endpoints:

* ``/api/sessions/{id}/overview``: what the session recorded per bucket of its
  own span (prompts, replies, thoughts by how much of them was kept, tool calls
  and tool errors), its model changes and compactions as points, its subagents
  as spans, and the usage each turn reported. A turn is everything from one
  prompt to the next. A number no message of a turn reported is null, never 0.
* ``/api/sessions/{id}/states``: each change of the session's state, with what
  the new state rests on, as the collector recorded it.
* ``/api/live/activity``: events per minute over the last 30 minutes, for every
  live session at once. The live stream sends ``activity`` when this changes.

Every query is bounded: counts are reduced to session-minutes in the database,
and lists stop at a fixed size and say how much they left out.
"""

from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select

from agdash.api.analytics import METRICS
from agdash.api.routes import Db, _one_per_message
from agdash.api.series import Span, _minute, _next, _spans, _zone
from agdash.db.models import Event, EventUsage, Session, SessionStateChange
from agdash.vocab import EventKind, ParentBasis, SessionState, StateBasis, ThoughtsCoverage

router = APIRouter()

OverviewBucket = Literal["auto", "minute", "5-minute", "quarter-hour", "hour", "day"]
# Shortest first; "auto" takes the first that covers the session in this many buckets.
LADDER = (
    ("minute", timedelta(minutes=1)),
    ("5-minute", timedelta(minutes=5)),
    ("quarter-hour", timedelta(minutes=15)),
    ("hour", timedelta(hours=1)),
    ("day", timedelta(days=1)),
)
AUTO_BUCKETS = 120
MAX_POINTS = 1000
MAX_SUBAGENTS = 500
MAX_STATES = 2000
LIVE = (SessionState.ACTIVE, SessionState.WAITING, SessionState.IDLE)
LIVE_MINUTES = 30
LIVE_SESSIONS = 200
COUNTED = (
    EventKind.USER_PROMPT,
    EventKind.ASSISTANT_TEXT,
    EventKind.THINKING,
    EventKind.TOOL_CALL,
    EventKind.TOOL_RESULT,
)


class Thoughts(BaseModel):
    """Thinking records by how much of the reasoning the harness kept."""

    full: int = 0
    summary: int = 0
    encrypted: int = 0
    absent: int = 0
    not_recorded: int = Field(0, description="The harness did not say how much it kept.")


class Activity(BaseModel):
    prompts: int = 0
    replies: int = 0
    thoughts: Thoughts = Field(default_factory=Thoughts)
    tool_calls: int = 0
    tool_errors: int = Field(0, description="Tool results marked as errors.")


class Marker(BaseModel):
    kind: Literal["model_change", "compaction"]
    sequence: int
    at: datetime | None
    model: str | None = None
    previous_model: str | None = None
    effort: str | None = None
    trigger: str | None = None
    tokens_before: int | None = None
    tokens_after: int | None = None


class Subagent(BaseModel):
    session_id: int
    harness: str
    parent_basis: ParentBasis | None
    title: str | None
    state: SessionState
    start: datetime | None
    end: datetime | None = Field(description="When it ended, else its last activity.")
    ended: bool


class Turn(BaseModel):
    index: int = Field(description="1 for the first prompt; 0 for usage before any prompt.")
    prompt_sequence: int | None
    started_at: datetime | None
    messages: int = Field(description="Usage reports in the turn.")
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    reported_cost: float | None = None
    reported_by: dict[str, int] = Field(
        default_factory=dict, description="Per number, how many of the turn's messages reported it."
    )


class Overview(BaseModel):
    session_id: int
    time_zone: str
    bucket: str
    buckets: list[Span]
    activity: list[Activity] = Field(description="One per bucket, in bucket order.")
    undated_events: int = Field(description="Counted records with no time, left out.")
    markers: list[Marker]
    markers_omitted: int
    subagents: list[Subagent]
    subagents_omitted: int
    turns: list[Turn]


class StateChange(BaseModel):
    state: SessionState
    basis: StateBasis
    previous: SessionState | None
    changed_at: datetime


class StateHistory(BaseModel):
    session_id: int
    state: SessionState = Field(description="The state now.")
    changes: list[StateChange] = Field(description="Oldest first.")
    earlier_omitted: int = Field(description="Older changes left out of the list.")


class LiveSpan(BaseModel):
    state: SessionState
    start: datetime
    end: datetime


class LiveSession(BaseModel):
    id: int
    harness: str
    state: SessionState
    events: list[int] = Field(description="Events in each minute, in minute order.")
    total: int
    states: list[LiveSpan] = Field(
        default_factory=list,
        description="Its states over the same window, oldest first, from recorded changes.",
    )


class LiveActivity(BaseModel):
    minutes: list[datetime] = Field(description="The start of each minute, oldest first.")
    until: datetime = Field(description="The end of the last minute.")
    sessions: list[LiveSession]
    omitted: int = Field(description="Live sessions beyond the limit, left out.")


# -- overview -----------------------------------------------------------------------


def _session(db, session_id: int) -> Session:
    session = db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no such session")
    return session


def _choose(first: datetime, last: datetime) -> str:
    for name, width in LADDER:
        if (last - first) / width < AUTO_BUCKETS:
            return name
    return LADDER[-1][0]


def _activity(db, session_id: int) -> tuple[list[tuple[datetime, Any, Any, int, int]], int]:
    """(minute, kind, coverage, errors, count) rows, and how many records had no time."""
    minute = _minute(db)
    error = case(
        (
            (Event.kind == EventKind.TOOL_RESULT)
            & Event.payload["is_error"].as_boolean().is_(True),
            1,
        ),
        else_=0,
    )
    where = (Event.session_id == session_id, Event.kind.in_(COUNTED))
    rows = db.execute(
        select(minute, Event.kind, Event.thoughts_coverage, error, func.count())
        .where(*where, Event.occurred_at.is_not(None))
        .group_by(minute, Event.kind, Event.thoughts_coverage, error)
    ).all()
    undated = db.scalar(
        select(func.count()).select_from(Event).where(*where, Event.occurred_at.is_(None))
    )
    found = [
        (datetime.fromisoformat(text).replace(tzinfo=UTC), kind, coverage, errors, count)
        for text, kind, coverage, errors, count in rows
    ]
    return found, undated or 0


def _tally(activity: Activity, kind, coverage, errors: int, count: int) -> None:
    if kind == EventKind.USER_PROMPT:
        activity.prompts += count
    elif kind == EventKind.ASSISTANT_TEXT:
        activity.replies += count
    elif kind == EventKind.TOOL_CALL:
        activity.tool_calls += count
    elif kind == EventKind.TOOL_RESULT:
        activity.tool_errors += count if errors else 0
    elif kind == EventKind.THINKING:
        name = ThoughtsCoverage(coverage).value if coverage else "not_recorded"
        setattr(activity.thoughts, name, getattr(activity.thoughts, name) + count)


def _markers(db, session_id: int) -> tuple[list[Marker], int]:
    where = (
        Event.session_id == session_id,
        Event.kind.in_((EventKind.MODEL_CHANGE, EventKind.COMPACTION)),
    )
    total = db.scalar(select(func.count()).select_from(Event).where(*where)) or 0
    rows = db.execute(
        select(Event.kind, Event.sequence, Event.occurred_at, Event.payload)
        .where(*where)
        .order_by(Event.sequence)
        .limit(MAX_POINTS)
    ).all()
    markers = []
    for kind, sequence, at, payload in rows:
        payload = payload or {}
        fields = (
            ("model", "previous_model", "effort")
            if kind == EventKind.MODEL_CHANGE
            else ("trigger", "tokens_before", "tokens_after")
        )
        markers.append(
            Marker(
                kind=str(kind),
                sequence=sequence,
                at=at,
                **{name: payload.get(name) for name in fields},
            )
        )
    return markers, total - len(markers)


def _subagents(db, session_id: int) -> tuple[list[Subagent], int]:
    where = Session.parent_session_id == session_id
    total = db.scalar(select(func.count()).select_from(Session).where(where)) or 0
    children = db.scalars(
        select(Session)
        .where(where)
        .order_by(Session.started_at.is_(None), Session.started_at, Session.id)
        .limit(MAX_SUBAGENTS)
    ).all()
    spans = [
        Subagent(
            session_id=child.id,
            harness=child.harness,
            parent_basis=child.parent_basis,
            title=child.title,
            state=child.state,
            start=child.started_at,
            end=child.ended_at or child.last_activity_at,
            ended=child.state is SessionState.ENDED,
        )
        for child in children
    ]
    return spans, total - len(spans)


def _turns(db, session_id: int) -> list[Turn]:
    prompts = db.execute(
        select(Event.sequence, Event.occurred_at)
        .where(Event.session_id == session_id, Event.kind == EventKind.USER_PROMPT)
        .order_by(Event.sequence)
    ).all()
    # The numbers from the narrow usage table; the event itself only for where it sits.
    usage = db.execute(
        select(Event.sequence, *(getattr(EventUsage, metric) for metric in METRICS))
        .join(EventUsage, EventUsage.event_id == Event.id)
        .where(
            EventUsage.event_id.in_(_one_per_message().where(EventUsage.session_id == session_id))
        )
    ).all()
    starts = [sequence for sequence, _ in prompts]
    totals: dict[int, dict[str, Any]] = defaultdict(lambda: {"messages": 0, "by": {}})
    for sequence, *values in usage:
        payload = dict(zip(METRICS, values, strict=True))
        turn = totals[bisect_right(starts, sequence)]
        turn["messages"] += 1
        for metric in METRICS:
            value = payload.get(metric)
            if isinstance(value, bool) or not isinstance(value, int | float):
                continue
            turn[metric] = (turn.get(metric) or 0) + value
            turn["by"][metric] = turn["by"].get(metric, 0) + 1

    def turn(index: int, sequence: int | None, at: datetime | None) -> Turn:
        found = totals.get(index, {"messages": 0, "by": {}})
        return Turn(
            index=index,
            prompt_sequence=sequence,
            started_at=at,
            messages=found["messages"],
            reported_by={metric: found["by"].get(metric, 0) for metric in METRICS},
            **{metric: found.get(metric) for metric in METRICS},
        )

    turns = [turn(0, None, None)] if 0 in totals else []
    turns += [turn(index, seq, at) for index, (seq, at) in enumerate(prompts, start=1)]
    return turns


@router.get(
    "/sessions/{session_id}/overview",
    response_model=Overview,
    summary="One session over time",
)
def overview(
    db: Db,
    session_id: int,
    time_zone: Annotated[
        str, Query(max_length=64, description="An IANA zone, such as Europe/Berlin.")
    ] = "UTC",
    bucket: Annotated[
        OverviewBucket,
        Query(description=f"auto picks the shortest that fits in {AUTO_BUCKETS} buckets."),
    ] = "auto",
) -> dict[str, Any]:
    """Activity per bucket, model changes and compactions, subagents, and usage per turn."""
    zone = _zone(time_zone)
    _session(db, session_id)
    rows, undated = _activity(db, session_id)
    starts: list[datetime] = []
    if rows:
        first, last = min(r[0] for r in rows), max(r[0] for r in rows)
        if bucket == "auto":
            bucket = _choose(first, last)
        starts = _spans(first, last, bucket, zone)
    elif bucket == "auto":
        bucket = LADDER[0][0]
    activity = [Activity() for _ in starts]
    for minute, kind, coverage, errors, count in rows:
        _tally(activity[bisect_right(starts, minute) - 1], kind, coverage, errors, count)
    markers, markers_omitted = _markers(db, session_id)
    subagents, subagents_omitted = _subagents(db, session_id)
    return {
        "session_id": session_id,
        "time_zone": time_zone,
        "bucket": bucket,
        "buckets": [Span(start=s, end=_next(s, bucket, zone)) for s in starts],
        "activity": activity,
        "undated_events": undated,
        "markers": markers,
        "markers_omitted": markers_omitted,
        "subagents": subagents,
        "subagents_omitted": subagents_omitted,
        "turns": _turns(db, session_id),
    }


# -- states -------------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/states", response_model=StateHistory, summary="A session's states"
)
def states(db: Db, session_id: int) -> dict[str, Any]:
    """Each change of state, with its basis, oldest first; at most the latest 2,000."""
    session = _session(db, session_id)
    where = SessionStateChange.session_id == session_id
    total = db.scalar(select(func.count()).select_from(SessionStateChange).where(where)) or 0
    latest = db.scalars(
        select(SessionStateChange)
        .where(where)
        .order_by(SessionStateChange.changed_at.desc(), SessionStateChange.id.desc())
        .limit(MAX_STATES)
    ).all()
    return {
        "session_id": session_id,
        "state": session.state,
        "changes": [
            StateChange.model_validate(row, from_attributes=True) for row in reversed(latest)
        ],
        "earlier_omitted": total - len(latest),
    }


# -- live ---------------------------------------------------------------------------


def live_minute(now: datetime) -> datetime:
    """The start of the minute ``now`` falls in, in UTC."""
    return now.astimezone(UTC).replace(second=0, microsecond=0)


def _live_spans(db, live, start: datetime, end: datetime) -> dict[int, list[LiveSpan]]:
    """Each live session's states from ``start`` to ``end``, from its recorded changes.

    The state at the window's start is what the first change in it left behind; a
    session with no change in the window held its current state throughout; one
    whose first change has no earlier state began then, so nothing is drawn before.
    """
    if not live:
        return {}
    changes: dict[int, list[SessionStateChange]] = defaultdict(list)
    for change in db.scalars(
        select(SessionStateChange)
        .where(
            SessionStateChange.session_id.in_([row.id for row in live]),
            SessionStateChange.changed_at >= start,
            SessionStateChange.changed_at < end,
        )
        .order_by(
            SessionStateChange.session_id, SessionStateChange.changed_at, SessionStateChange.id
        )
    ):
        changes[change.session_id].append(change)
    spans: dict[int, list[LiveSpan]] = {}
    for row in live:
        found = changes.get(row.id, [])
        points: list[tuple[SessionState, datetime]] = []
        if not found:
            points.append((row.state, start))
        else:
            if found[0].previous is not None:
                points.append((found[0].previous, start))
            points.extend((change.state, change.changed_at) for change in found)
        ends = [since for _, since in points[1:]] + [end]
        spans[row.id] = [
            LiveSpan(state=state, start=since, end=until)
            for (state, since), until in zip(points, ends, strict=True)
            if until > since
        ]
    return spans


@router.get("/live/activity", response_model=LiveActivity, summary="Live sessions, per minute")
def live_activity(
    db: Db,
    until: Annotated[
        datetime | None,
        Query(description="Count the 30 minutes up to the one holding this instant; default now."),
    ] = None,
) -> dict[str, Any]:
    """Events per minute over the last 30 minutes for every active, waiting or idle session."""
    end = live_minute(until or datetime.now(UTC)) + timedelta(minutes=1)
    minutes = [end - timedelta(minutes=LIVE_MINUTES - n) for n in range(LIVE_MINUTES)]
    where = Session.state.in_(LIVE)
    total = db.scalar(select(func.count()).select_from(Session).where(where)) or 0
    live = db.execute(
        select(Session.id, Session.harness, Session.state)
        .where(where)
        .order_by(Session.last_activity_at.desc(), Session.id.desc())
        .limit(LIVE_SESSIONS)
    ).all()
    counts: dict[int, list[int]] = {row.id: [0] * LIVE_MINUTES for row in live}
    if counts:
        minute = _minute(db)
        rows = db.execute(
            select(Event.session_id, minute, func.count())
            .where(
                Event.session_id.in_(list(counts)),
                Event.occurred_at >= minutes[0],
                Event.occurred_at < end,
            )
            .group_by(Event.session_id, minute)
        )
        for session_id, text, count in rows:
            at = datetime.fromisoformat(text).replace(tzinfo=UTC)
            counts[session_id][int((at - minutes[0]) / timedelta(minutes=1))] += count
    spans = _live_spans(db, live, minutes[0], end)
    return {
        "minutes": minutes,
        "until": end,
        "sessions": [
            LiveSession(
                id=row.id,
                harness=row.harness,
                state=row.state,
                events=counts[row.id],
                total=sum(counts[row.id]),
                states=spans.get(row.id, []),
            )
            for row in live
        ],
        "omitted": total - len(live),
    }
