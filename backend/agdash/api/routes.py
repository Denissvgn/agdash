"""Read-only endpoints: sessions, their events, full-text search and stats.

Every list is paged with a cursor and every query is bounded by the page size,
so no request reads a whole session, however long it is.
"""

import re
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import String, and_, cast, func, or_, select, text
from sqlalchemy.orm import Session as DbSession

from agdash.api import cursors
from agdash.api.schemas import (
    EventOut,
    HealthRow,
    Page,
    SearchHit,
    SessionDetail,
    SessionProcessOut,
    SessionSummary,
    SnippetPart,
    Stats,
    UsageGroup,
    UsageTotals,
)
from agdash.api.store import Store
from agdash.db.base import UTCDateTime
from agdash.db.models import Event, Session, SessionProcess, SourceHealth
from agdash.db.search import SEARCH_TABLE
from agdash.vocab import EventKind, SessionState, Surface

router = APIRouter()

INITIAL_TASK_PREVIEW = 300
MATCH_START, MATCH_END = "\x02", "\x03"


def get_db(request: Request) -> Iterator[DbSession]:
    store: Store = request.app.state.store
    yield from store.session()


Db = Annotated[DbSession, Depends(get_db)]


# -- sessions -----------------------------------------------------------------------

_SORT = func.coalesce(Session.last_activity_at, Session.created_at, type_=UTCDateTime())


def _counts(db: DbSession, column, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    rows = db.execute(select(column, func.count()).where(column.in_(ids)).group_by(column))
    return {key: count for key, count in rows}


def _summary(session: Session, events: int, children: int) -> dict[str, Any]:
    task = session.initial_task
    return {
        "id": session.id,
        "harness": session.harness,
        "native_id": session.native_id,
        "surface": session.surface,
        "title": session.title,
        "cwd": session.cwd,
        "branch": session.branch,
        "state": session.state,
        "started_at": session.started_at,
        "last_activity_at": session.last_activity_at,
        "ended_at": session.ended_at,
        "models": session.models or [],
        "effort": session.effort,
        "parent_session_id": session.parent_session_id,
        "initial_task": task[:INITIAL_TASK_PREVIEW] if task else None,
        "goal": session.goal,
        "event_count": events,
        "child_count": children,
    }


@router.get("/sessions", response_model=Page[SessionSummary], summary="List sessions")
def list_sessions(
    db: Db,
    harness: Annotated[list[str] | None, Query()] = None,
    state: Annotated[list[SessionState] | None, Query()] = None,
    surface: Annotated[list[Surface] | None, Query()] = None,
    parent: Annotated[int | None, Query(description="Only the children of this session.")] = None,
    roots_only: Annotated[bool, Query(description="Leave out subagent sessions.")] = False,
    q: Annotated[
        str | None, Query(max_length=200, description="Text in the title, first prompt or folder.")
    ] = None,
    active_since: Annotated[datetime | None, Query()] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Most recently active first."""
    query = select(Session)
    if harness:
        query = query.where(Session.harness.in_(harness))
    if state:
        query = query.where(Session.state.in_(state))
    if surface:
        query = query.where(Session.surface.in_(surface))
    if parent is not None:
        query = query.where(Session.parent_session_id == parent)
    if roots_only:
        query = query.where(Session.parent_session_id.is_(None))
    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                Session.title.ilike(pattern),
                Session.initial_task.ilike(pattern),
                Session.cwd.ilike(pattern),
            )
        )
    if active_since is not None:
        query = query.where(_SORT >= active_since)
    position = cursors.decode(cursor, "at", "id")
    if position:
        try:
            at = datetime.fromisoformat(position["at"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="invalid cursor") from None
        query = query.where(or_(_SORT < at, and_(_SORT == at, Session.id < position["id"])))
    rows = db.scalars(query.order_by(_SORT.desc(), Session.id.desc()).limit(limit + 1)).all()
    page, more = rows[:limit], len(rows) > limit
    ids = [s.id for s in page]
    events = _counts(db, Event.session_id, ids)
    children = _counts(db, Session.parent_session_id, ids)
    next_cursor = None
    if more:
        last = page[-1]
        at = last.last_activity_at or last.created_at
        next_cursor = cursors.encode({"at": at.isoformat(), "id": last.id})
    return {
        "items": [_summary(s, events.get(s.id, 0), children.get(s.id, 0)) for s in page],
        "next_cursor": next_cursor,
    }


def _one_per_message():
    """Usage events, one per model message: some harnesses repeat a message's usage."""
    message = func.coalesce(
        Event.payload[("extra", "message_id")].as_string(), cast(Event.id, String)
    )
    return (
        select(func.min(Event.id).label("id"))
        .where(Event.kind == EventKind.USAGE, Event.payload["scope"].as_string() == "message")
        .group_by(Event.session_id, message)
    )


def _usage_columns():
    def total(field: str, as_float: bool = False):
        value = Event.payload[field].as_float() if as_float else Event.payload[field].as_integer()
        return func.sum(value)

    return (
        func.count(Event.id),
        total("input_tokens"),
        total("output_tokens"),
        total("cache_read_tokens"),
        total("cache_write_tokens"),
        total("reasoning_tokens"),
        total("reported_cost", as_float=True),
    )


def _usage(row) -> dict[str, Any]:
    names = (
        "messages",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "reported_cost",
    )
    return dict(zip(names, row, strict=True))


@router.get("/sessions/{session_id}", response_model=SessionDetail, summary="One session")
def get_session(db: Db, session_id: int) -> dict[str, Any]:
    session = db.get(Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no such session")
    children = db.scalars(
        select(Session.id).where(Session.parent_session_id == session_id).order_by(Session.id)
    ).all()
    by_kind = {
        str(kind): count
        for kind, count in db.execute(
            select(Event.kind, func.count())
            .where(Event.session_id == session_id)
            .group_by(Event.kind)
        )
    }
    usage = db.execute(
        select(*_usage_columns()).where(
            Event.id.in_(_one_per_message().where(Event.session_id == session_id))
        )
    ).one()
    processes = db.scalars(
        select(SessionProcess)
        .where(SessionProcess.session_id == session_id)
        .order_by(SessionProcess.first_seen_at)
    ).all()
    detail = _summary(session, sum(by_kind.values()), len(children))
    detail.update(
        initial_task=session.initial_task,
        permission_mode=session.permission_mode,
        auth_method=session.auth_method,
        account_label=session.account_label,
        parent_basis=session.parent_basis,
        state_evidence=session.state_evidence or {},
        state_observed_at=session.state_observed_at,
        children=list(children),
        processes=[SessionProcessOut.model_validate(p, from_attributes=True) for p in processes],
        events_by_kind=by_kind,
        usage=UsageTotals(**_usage(usage)),
    )
    return detail


# -- events -------------------------------------------------------------------------


def _event(event: Event) -> dict[str, Any]:
    return {
        "id": event.id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "occurred_at": event.occurred_at,
        "kind": event.kind,
        "payload": event.payload,
        "thoughts_coverage": event.thoughts_coverage,
    }


@router.get(
    "/sessions/{session_id}/events", response_model=Page[EventOut], summary="A session's events"
)
def list_events(
    db: Db,
    session_id: int,
    kind: Annotated[list[EventKind] | None, Query()] = None,
    order: Literal["asc", "desc"] = "asc",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict[str, Any]:
    """In the order they happened (``asc``), or newest first (``desc``)."""
    if db.get(Session, session_id) is None:
        raise HTTPException(status_code=404, detail="no such session")
    query = select(Event).where(Event.session_id == session_id)
    if kind:
        query = query.where(Event.kind.in_(kind))
    position = cursors.decode(cursor, "seq")
    if position:
        if not isinstance(position["seq"], int):
            raise HTTPException(status_code=400, detail="invalid cursor")
        newer = order == "asc"
        query = query.where(
            Event.sequence > position["seq"] if newer else Event.sequence < position["seq"]
        )
    sort = Event.sequence.asc() if order == "asc" else Event.sequence.desc()
    rows = db.scalars(query.order_by(sort).limit(limit + 1)).all()
    page = rows[:limit]
    more = len(rows) > limit
    return {
        "items": [_event(e) for e in page],
        "next_cursor": cursors.encode({"seq": page[-1].sequence}) if more else None,
    }


# -- search -------------------------------------------------------------------------


def match_expression(query: str) -> str:
    """The person's words as an FTS5 query: every word must appear; ``word*`` is a prefix."""
    terms = []
    for word in query.split():
        prefix = word.endswith("*")
        word = word.rstrip("*")
        if not word:
            continue
        quoted = '"' + word.replace('"', '""') + '"'
        terms.append(quoted + ("*" if prefix else ""))
    return " ".join(terms)


def _snippet(marked: str) -> list[dict[str, Any]]:
    parts = []
    for piece in re.split(f"({MATCH_START}[^{MATCH_END}]*{MATCH_END})", marked):
        if not piece:
            continue
        if piece.startswith(MATCH_START):
            parts.append({"text": piece[1:-1], "match": True})
        else:
            parts.append({"text": piece, "match": False})
    return parts


def _plain_snippet(payload: dict[str, Any], query: str) -> list[dict[str, Any]]:
    """Without an index: the text around the first match, found by hand."""
    text_value = next(
        (str(payload[k]) for k in ("text", "output", "summary", "detail") if payload.get(k)), ""
    )
    at = text_value.lower().find(query.lower())
    if at < 0:
        return [{"text": text_value[:160], "match": False}]
    start = max(0, at - 60)
    return [
        part
        for part in (
            {"text": ("…" if start else "") + text_value[start:at], "match": False},
            {"text": text_value[at : at + len(query)], "match": True},
            {"text": text_value[at + len(query) : at + len(query) + 100], "match": False},
        )
        if part["text"]
    ]


def _hit(
    event: Event, harness: str, title: str | None, snippet: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "session_id": event.session_id,
        "sequence": event.sequence,
        "kind": event.kind,
        "occurred_at": event.occurred_at,
        "harness": harness,
        "session_title": title,
        "snippet": [SnippetPart(**part) for part in snippet],
    }


@router.get("/search", response_model=Page[SearchHit], summary="Search event text")
def search(
    request: Request,
    db: Db,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    harness: str | None = None,
    session_id: int | None = None,
    kind: EventKind | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Best matches first. Every word must appear; end a word with ``*`` to match a prefix."""
    position = cursors.decode(cursor, "offset")
    offset = position["offset"] if position else 0
    if not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=400, detail="invalid cursor")
    store: Store = request.app.state.store
    filters = []
    params: dict[str, Any] = {"limit": limit + 1, "offset": offset}
    if harness:
        filters.append("s.harness = :harness")
        params["harness"] = harness
    if session_id is not None:
        filters.append("e.session_id = :session_id")
        params["session_id"] = session_id
    if kind is not None:
        filters.append("e.kind = :kind")
        params["kind"] = kind.value

    if store.has_search_index(db):
        expression = match_expression(q)
        if not expression:
            raise HTTPException(status_code=422, detail="the query has no words")
        params["match"] = expression
        where = " AND ".join([f"{SEARCH_TABLE} MATCH :match", *filters])
        matched = db.execute(
            text(
                f"SELECT e.id, snippet({SEARCH_TABLE}, 0, char(2), char(3), '…', 16) AS marked"
                f" FROM {SEARCH_TABLE} JOIN events e ON e.id = {SEARCH_TABLE}.rowid"
                f" JOIN sessions s ON s.id = e.session_id WHERE {where}"
                f" ORDER BY bm25({SEARCH_TABLE}), e.id LIMIT :limit OFFSET :offset"
            ),
            params,
        ).all()
        loaded = {
            event.id: (event, session_harness, title)
            for event, session_harness, title in db.execute(
                select(Event, Session.harness, Session.title)
                .join(Session, Session.id == Event.session_id)
                .where(Event.id.in_([row.id for row in matched]))
            )
        }
        hits = [
            _hit(*loaded[row.id], _snippet(row.marked or "")) for row in matched if row.id in loaded
        ]
    else:
        query = (
            select(Event, Session.harness, Session.title)
            .join(Session, Session.id == Event.session_id)
            .where(cast(Event.payload, String).ilike(f"%{q}%"))
        )
        if harness:
            query = query.where(Session.harness == harness)
        if session_id is not None:
            query = query.where(Event.session_id == session_id)
        if kind is not None:
            query = query.where(Event.kind == kind)
        found = db.execute(query.order_by(Event.id.desc()).limit(limit + 1).offset(offset)).all()
        hits = [
            _hit(event, session_harness, title, _plain_snippet(event.payload, q))
            for event, session_harness, title in found
        ]
    more = len(hits) > limit
    return {
        "items": hits[:limit],
        "next_cursor": cursors.encode({"offset": offset + limit}) if more else None,
    }


# -- stats --------------------------------------------------------------------------


@router.get("/stats", response_model=Stats, summary="Counts and usage")
def stats(
    db: Db,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Sessions active in the window, their events, and token usage by harness and model."""
    sessions = select(Session.id)
    if since is not None:
        sessions = sessions.where(_SORT >= since)
    if until is not None:
        sessions = sessions.where(_SORT < until)
    in_window = Session.id.in_(sessions)

    def by(column) -> dict[str, int]:
        return {
            str(key) if key is not None else "unknown": count
            for key, count in db.execute(
                select(column, func.count()).where(in_window).group_by(column)
            )
        }

    by_harness = by(Session.harness)
    events_in_window = Event.session_id.in_(sessions)
    if since is not None:
        events_in_window = and_(events_in_window, Event.occurred_at >= since)
    if until is not None:
        events_in_window = and_(events_in_window, Event.occurred_at < until)
    events_by_kind = {
        str(kind): count
        for kind, count in db.execute(
            select(Event.kind, func.count()).where(events_in_window).group_by(Event.kind)
        )
    }
    model = Event.payload["model"].as_string()
    usage_rows = db.execute(
        select(Session.harness, model, *_usage_columns())
        .join(Session, Session.id == Event.session_id)
        .where(Event.id.in_(_one_per_message().where(events_in_window)))
        .group_by(Session.harness, model)
        .order_by(Session.harness, model)
    ).all()
    health = db.scalars(select(SourceHealth).order_by(SourceHealth.adapter)).all()
    return {
        "since": since,
        "until": until,
        "sessions": sum(by_harness.values()),
        "sessions_by_harness": by_harness,
        "sessions_by_state": by(Session.state),
        "sessions_by_surface": by(Session.surface),
        "events": sum(events_by_kind.values()),
        "events_by_kind": events_by_kind,
        "usage": [
            UsageGroup(harness=row[0], model=row[1], **_usage(row[2:])) for row in usage_rows
        ],
        "source_health": [HealthRow.model_validate(h, from_attributes=True) for h in health],
    }
