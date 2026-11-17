"""Read-only endpoints: sessions, their events, full-text search and stats.

Every list is paged with a cursor and every query is bounded by the page size,
so no request reads a whole session, however long it is.
"""

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import String, and_, bindparam, cast, func, or_, select, text
from sqlalchemy.orm import Session as DbSession

from agdash.api import cursors
from agdash.api.schemas import (
    EventOut,
    Excerpt,
    Facets,
    HealthRow,
    Page,
    Reading,
    SearchHit,
    SessionDetail,
    SessionHit,
    SessionProcessOut,
    SessionSummary,
    SnippetPart,
    Stats,
    UsageGroup,
    UsageTotals,
)
from agdash.api.store import Store
from agdash.db.base import UTCDateTime
from agdash.db.models import Event, EventUsage, Session, SessionProcess, SourceHealth
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

# Most recent activity first; a session with no recorded activity or start goes last.
NO_ACTIVITY = datetime(1970, 1, 1, tzinfo=UTC)
_SORT = func.coalesce(
    Session.last_activity_at, Session.started_at, NO_ACTIVITY, type_=UTCDateTime()
)


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
        "state_basis": (session.state_evidence or {}).get("basis"),
        "started_at": session.started_at,
        "last_activity_at": session.last_activity_at,
        "ended_at": session.ended_at,
        "models": session.models or [],
        "effort": session.effort,
        "parent_session_id": session.parent_session_id,
        "parent_basis": session.parent_basis,
        "initial_task": task[:INITIAL_TASK_PREVIEW] if task else None,
        "goal": session.goal,
        "event_count": events,
        "child_count": children,
    }


@dataclass
class SessionFilters:
    """Which sessions a list or a search covers. Every filter narrows; they combine."""

    harness: list[str] | None = None
    state: list[SessionState] | None = None
    surface: list[Surface] | None = None
    parent: int | None = None
    roots_only: bool = False
    q: str | None = None
    active_since: datetime | None = None
    model: str | None = None
    folder: str | None = None
    auth_method: str | None = None
    started_from: datetime | None = None
    started_before: datetime | None = None
    recorded: bool = False

    def apply(self, query):
        if self.harness:
            query = query.where(Session.harness.in_(self.harness))
        if self.state:
            query = query.where(Session.state.in_(self.state))
        if self.surface:
            query = query.where(Session.surface.in_(self.surface))
        if self.parent is not None:
            query = query.where(Session.parent_session_id == self.parent)
        if self.roots_only:
            query = query.where(Session.parent_session_id.is_(None))
        if self.q:
            pattern = f"%{self.q}%"
            query = query.where(
                or_(
                    Session.title.ilike(pattern),
                    Session.initial_task.ilike(pattern),
                    Session.cwd.ilike(pattern),
                )
            )
        if self.active_since is not None:
            query = query.where(_SORT >= self.active_since)
        if self.model:
            # The models list is stored as JSON text; a quoted name matches one entry exactly.
            query = query.where(
                cast(Session.models, String).contains(json.dumps(self.model), autoescape=True)
            )
        if self.folder:
            folder = self.folder.rstrip("/") or "/"
            query = query.where(
                or_(
                    Session.cwd == folder,
                    Session.cwd.startswith(folder.rstrip("/") + "/", autoescape=True),
                )
            )
        if self.auth_method:
            query = query.where(Session.auth_method == self.auth_method)
        if self.started_from is not None:
            query = query.where(Session.started_at >= self.started_from)
        if self.started_before is not None:
            query = query.where(Session.started_at < self.started_before)
        if self.recorded:
            query = query.where(
                or_(Session.started_at.is_not(None), Session.last_activity_at.is_not(None))
            )
        return query


def session_filters(
    harness: Annotated[list[str] | None, Query()] = None,
    state: Annotated[list[SessionState] | None, Query()] = None,
    surface: Annotated[list[Surface] | None, Query()] = None,
    parent: Annotated[int | None, Query(description="Only the children of this session.")] = None,
    roots_only: Annotated[bool, Query(description="Leave out subagent sessions.")] = False,
    q: Annotated[
        str | None, Query(max_length=200, description="Text in the title, first prompt or folder.")
    ] = None,
    active_since: Annotated[datetime | None, Query()] = None,
    model: Annotated[
        str | None, Query(max_length=255, description="A model the session used.")
    ] = None,
    folder: Annotated[
        str | None,
        Query(max_length=1024, description="The session's folder, or a folder above it."),
    ] = None,
    auth_method: Annotated[str | None, Query(max_length=64)] = None,
    started_from: Annotated[datetime | None, Query(description="Started at or after.")] = None,
    started_before: Annotated[datetime | None, Query(description="Started before.")] = None,
    recorded: Annotated[
        bool,
        Query(description="Leave out sessions known only by name: no start and no activity."),
    ] = False,
) -> SessionFilters:
    return SessionFilters(
        harness,
        state,
        surface,
        parent,
        roots_only,
        q,
        active_since,
        model,
        folder,
        auth_method,
        started_from,
        started_before,
        recorded,
    )


Filters = Annotated[SessionFilters, Depends(session_filters)]


@router.get("/sessions", response_model=Page[SessionSummary], summary="List sessions")
def list_sessions(
    db: Db,
    filters: Filters,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Most recently active first."""
    query = filters.apply(select(Session))
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
        at = last.last_activity_at or last.started_at or NO_ACTIVITY
        next_cursor = cursors.encode({"at": at.isoformat(), "id": last.id})
    return {
        "items": [_summary(s, events.get(s.id, 0), children.get(s.id, 0)) for s in page],
        "next_cursor": next_cursor,
    }


MAX_FOLDERS = 100


def _facet(rows) -> list[dict[str, Any]]:
    counted = [{"value": str(value), "count": count} for value, count in rows if value]
    return sorted(counted, key=lambda item: (-item["count"], item["value"]))


@router.get("/sessions/facets", response_model=Facets, summary="Values to filter sessions by")
def session_facets(db: Db) -> dict[str, Any]:
    """Harnesses, models, folders and the rest, each with its number of sessions."""

    def grouped(column):
        return _facet(db.execute(select(column, func.count()).group_by(column)))

    models: dict[str, int] = {}
    for (names,) in db.execute(select(Session.models)):
        for name in set(names or []):
            models[name] = models.get(name, 0) + 1
    folders = db.execute(
        select(Session.cwd, func.count())
        .where(Session.cwd.is_not(None))
        .group_by(Session.cwd)
        .order_by(func.count().desc(), Session.cwd)
        .limit(MAX_FOLDERS)
    )
    first, last = db.execute(
        select(func.min(Session.started_at), func.max(Session.started_at))
    ).one()
    return {
        "harnesses": grouped(Session.harness),
        "surfaces": grouped(Session.surface),
        "states": grouped(Session.state),
        "models": _facet(models.items()),
        "folders": _facet(folders),
        "auth_methods": grouped(Session.auth_method),
        "first_started_at": first,
        "last_started_at": last,
    }


def _one_per_message():
    """Usage events, one per model message: some harnesses repeat a message's usage.

    Read from ``EventUsage``, the usage numbers kept beside the events: adding them up
    from event payloads reads through the whole store, which takes minutes on a large one.
    Filter it by ``EventUsage``'s own columns.
    """
    return (
        select(func.min(EventUsage.event_id).label("id"))
        .where(EventUsage.scope == "message")
        .group_by(EventUsage.session_id, EventUsage.message_key)
    )


def _usage_columns():
    return (
        func.count(EventUsage.event_id),
        func.sum(EventUsage.input_tokens),
        func.sum(EventUsage.output_tokens),
        func.sum(EventUsage.cache_read_tokens),
        func.sum(EventUsage.cache_write_tokens),
        func.sum(EventUsage.reasoning_tokens),
        func.sum(EventUsage.reported_cost),
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
            EventUsage.event_id.in_(_one_per_message().where(EventUsage.session_id == session_id))
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
        auth_basis=session.auth_basis,
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


def _marked(db: DbSession, expression: str, event_ids: Sequence[int]) -> dict[int, str]:
    """Each event's text around its match, for the events a page shows and no others.

    The index keeps no text: a snippet reads the event itself. Asked for with every
    match, before the page is chosen, it would read every event that matches.
    """
    if not event_ids:
        return {}
    rows = db.execute(
        text(
            f"SELECT rowid AS id, snippet({SEARCH_TABLE}, 0, char(2), char(3), '…', 16) AS marked"
            f" FROM {SEARCH_TABLE} WHERE {SEARCH_TABLE} MATCH :match AND rowid IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"match": expression, "ids": list(event_ids)},
    )
    return {row.id: row.marked or "" for row in rows}


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
                f"SELECT e.id FROM {SEARCH_TABLE} JOIN events e ON e.id = {SEARCH_TABLE}.rowid"
                f" JOIN sessions s ON s.id = e.session_id WHERE {where}"
                f" ORDER BY bm25({SEARCH_TABLE}), e.id LIMIT :limit OFFSET :offset"
            ),
            params,
        ).all()
        marked = _marked(db, expression, [row.id for row in matched])
        loaded = {
            event.id: (event, session_harness, title)
            for event, session_harness, title in db.execute(
                select(Event, Session.harness, Session.title)
                .join(Session, Session.id == Event.session_id)
                .where(Event.id.in_([row.id for row in matched]))
            )
        }
        hits = [
            _hit(*loaded[row.id], _snippet(marked.get(row.id, "")))
            for row in matched
            if row.id in loaded
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


EXCERPTS_PER_SESSION = 2
FALLBACK_SCAN = 2000


@router.get(
    "/search/sessions", response_model=Page[SessionHit], summary="Find sessions by their text"
)
def search_sessions(
    request: Request,
    db: Db,
    filters: Filters,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """Sessions whose events match, best match first, each with its best excerpts."""
    position = cursors.decode(cursor, "offset")
    offset = position["offset"] if position else 0
    if not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=400, detail="invalid cursor")
    store: Store = request.app.state.store
    allowed = filters.apply(select(Session.id))
    if store.has_search_index(db):
        expression = match_expression(q)
        if not expression:
            raise HTTPException(status_code=422, detail="the query has no words")
        # The filters stay bound parameters: compiled with named placeholders, merged below.
        named = type(db.bind.dialect)(paramstyle="named")
        compiled = allowed.compile(dialect=named, compile_kwargs={"render_postcompile": True})
        rows = db.execute(
            text(
                f"WITH hits AS (SELECT e.session_id AS sid, e.id AS eid,"
                f" bm25({SEARCH_TABLE}) AS score"
                f" FROM {SEARCH_TABLE} JOIN events e ON e.id = {SEARCH_TABLE}.rowid"
                f" WHERE {SEARCH_TABLE} MATCH :match AND e.session_id IN ({compiled})),"
                " ranked AS (SELECT *,"
                " row_number() OVER (PARTITION BY sid ORDER BY score, eid) AS rn,"
                " count(*) OVER (PARTITION BY sid) AS hits,"
                " min(score) OVER (PARTITION BY sid) AS best FROM hits),"
                " paged AS (SELECT *, dense_rank() OVER (ORDER BY best, sid) AS place"
                " FROM ranked WHERE rn <= :excerpts)"
                " SELECT sid, eid, hits FROM paged"
                " WHERE place > :offset AND place <= :end ORDER BY place, rn"
            ),
            {
                **compiled.params,
                "match": expression,
                "excerpts": EXCERPTS_PER_SESSION,
                "offset": offset,
                "end": offset + limit + 1,
            },
        ).all()
        marked = _marked(db, expression, [eid for _, eid, _ in rows])
        order: list[int] = []
        found: dict[int, dict[str, Any]] = {}
        for sid, eid, hits in rows:
            if sid not in found:
                order.append(sid)
                found[sid] = {"hits": hits, "excerpts": []}
            found[sid]["excerpts"].append((eid, _snippet(marked.get(eid, ""))))
    else:
        rows = db.execute(
            select(Event.session_id, Event.id, Event.payload)
            .where(cast(Event.payload, String).ilike(f"%{q}%"), Event.session_id.in_(allowed))
            .order_by(Event.id.desc())
            .limit(FALLBACK_SCAN)
        ).all()
        order, found = [], {}
        for sid, eid, payload in rows:
            if sid not in found:
                order.append(sid)
                found[sid] = {"hits": 0, "excerpts": []}
            found[sid]["hits"] += 1
            if len(found[sid]["excerpts"]) < EXCERPTS_PER_SESSION:
                found[sid]["excerpts"].append((eid, _plain_snippet(payload, q)))
        order = order[offset : offset + limit + 1]
    more = len(order) > limit
    order = order[:limit]
    sessions = {s.id: s for s in db.scalars(select(Session).where(Session.id.in_(order)))}
    event_ids = [eid for sid in order for eid, _ in found[sid]["excerpts"]]
    events = {e.id: e for e in db.scalars(select(Event).where(Event.id.in_(event_ids)))}
    counts = _counts(db, Event.session_id, order)
    children = _counts(db, Session.parent_session_id, order)
    items = []
    for sid in order:
        session = sessions.get(sid)
        if session is None:
            continue
        excerpts = [
            Excerpt(
                event_id=eid,
                sequence=events[eid].sequence,
                kind=events[eid].kind,
                occurred_at=events[eid].occurred_at,
                snippet=[SnippetPart(**part) for part in parts],
            )
            for eid, parts in found[sid]["excerpts"]
            if eid in events
        ]
        items.append(
            {
                **_summary(session, counts.get(sid, 0), children.get(sid, 0)),
                "hits": found[sid]["hits"],
                "excerpts": excerpts,
            }
        )
    return {
        "items": items,
        "next_cursor": cursors.encode({"offset": offset + limit}) if more else None,
    }


# -- stats --------------------------------------------------------------------------


@router.get("/reading", response_model=Reading, summary="Progress of the start-up read")
def reading(request: Request) -> dict[str, Any]:
    """Whether the service has read everything the harnesses had written when it started.

    Files of sessions running now are read first, then the newest; lists are short
    of sessions until ``finished``.
    """
    source = getattr(request.app.state, "reading", None)
    found = source() if source is not None else None
    if found is None:
        return {"known": False}
    return {"known": True, "done": found.done, "total": found.total, "finished": found.finished}


@router.get("/stats", response_model=Stats, summary="Counts and usage")
def stats(
    db: Db,
    filters: Filters,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """Sessions the filters select and active in the window, their events, and their usage."""
    sessions = filters.apply(select(Session.id))
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
    usage_in_window = EventUsage.session_id.in_(sessions)
    if since is not None:
        events_in_window = and_(events_in_window, Event.occurred_at >= since)
        usage_in_window = and_(usage_in_window, EventUsage.occurred_at >= since)
    if until is not None:
        events_in_window = and_(events_in_window, Event.occurred_at < until)
        usage_in_window = and_(usage_in_window, EventUsage.occurred_at < until)
    events_by_kind = {
        str(kind): count
        for kind, count in db.execute(
            select(Event.kind, func.count()).where(events_in_window).group_by(Event.kind)
        )
    }
    model = EventUsage.model
    usage_rows = db.execute(
        select(Session.harness, model, *_usage_columns())
        .join(Session, Session.id == EventUsage.session_id)
        .where(EventUsage.event_id.in_(_one_per_message().where(usage_in_window)))
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
