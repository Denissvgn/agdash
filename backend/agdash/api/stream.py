"""The live stream: server-sent events whenever sessions change or gain events.

The stream looks at the store twice a second, so it sees writes from any
process, the collector included. Each look is two small indexed queries,
bounded by a page size, read-only like the rest of the API.

Events:

* ``hello`` once, on connecting: where the stream starts.
* ``sessions``: sessions whose record changed (state, activity, title…),
  oldest change first.
* ``events``: sessions that gained events, with their newest sequence.
* ``activity``: the per-minute counts of ``/api/live/activity`` may have
  changed, because sessions changed or gained events or a new minute began.
* a comment line every fifteen seconds, so idle connections stay open.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session as DbSession
from starlette.concurrency import run_in_threadpool

from agdash.api.store import Store
from agdash.db.models import Event, Session

router = APIRouter()

TICK_SECONDS = 0.5
KEEPALIVE_SECONDS = 15.0
PAGE = 200
# New events read in one look; with more than these, the next look carries on from there.
EVENT_PAGE = 5000


class Watermark:
    """How far the stream has told its client about."""

    def __init__(
        self,
        changed_at: datetime | None,
        session_id: int,
        event_id: int,
        minute: datetime | None = None,
    ) -> None:
        self.changed_at = changed_at
        self.session_id = session_id
        self.event_id = event_id
        self.minute = minute

    def as_dict(self) -> dict[str, Any]:
        return {
            "changed_at": self.changed_at.isoformat() if self.changed_at else None,
            "session_id": self.session_id,
            "event_id": self.event_id,
        }


def _minute(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)


def start(db: DbSession, now: datetime | None = None) -> Watermark:
    latest = db.execute(
        select(Session.updated_at, Session.id)
        .order_by(Session.updated_at.desc(), Session.id.desc())
        .limit(1)
    ).first()
    event_id = db.scalar(select(func.max(Event.id))) or 0
    return Watermark(
        latest[0] if latest else None, latest[1] if latest else 0, event_id, _minute(now)
    )


def _session(session: Session) -> dict[str, Any]:
    return {
        "id": session.id,
        "harness": session.harness,
        "state": session.state,
        "title": session.title,
        "last_activity_at": session.last_activity_at.isoformat()
        if session.last_activity_at
        else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "parent_session_id": session.parent_session_id,
        "changed_at": session.updated_at.isoformat(),
    }


def changes(
    db: DbSession, mark: Watermark, now: datetime | None = None
) -> list[tuple[str, dict[str, Any]]]:
    """What changed since ``mark``, as (event name, data) pairs; ``mark`` moves past it."""
    found: list[tuple[str, dict[str, Any]]] = []
    query = select(Session)
    if mark.changed_at is not None:
        query = query.where(
            or_(
                Session.updated_at > mark.changed_at,
                and_(Session.updated_at == mark.changed_at, Session.id > mark.session_id),
            )
        )
    changed = db.scalars(query.order_by(Session.updated_at, Session.id).limit(PAGE)).all()
    if changed:
        found.append(("sessions", {"sessions": [_session(s) for s in changed]}))
        mark.changed_at, mark.session_id = changed[-1].updated_at, changed[-1].id
    # The new events themselves, by id, and counted here: grouped in SQL, the database
    # walks an index of every event to group by session, on every look, however few are new.
    newest: dict[int, list[int]] = {}
    for event_id, session_id, sequence in db.execute(
        select(Event.id, Event.session_id, Event.sequence)
        .where(Event.id > mark.event_id)
        .order_by(Event.id)
        .limit(EVENT_PAGE)
    ):
        seen = newest.setdefault(session_id, [event_id, sequence, 0])
        seen[0], seen[1], seen[2] = event_id, max(seen[1], sequence), seen[2] + 1
    grown = sorted(
        ((sid, last, sequence, count) for sid, (last, sequence, count) in newest.items()),
        key=lambda row: row[1],
    )
    if grown:
        found.append(
            (
                "events",
                {
                    "sessions": [
                        {"id": sid, "newest_sequence": seq, "new_events": count}
                        for sid, _, seq, count in grown
                    ]
                },
            )
        )
        mark.event_id = max(last for _, last, _, _ in grown)
    minute = _minute(now)
    if found or minute != mark.minute:
        found.append(
            (
                "activity",
                {
                    "minute": minute.isoformat(),
                    "sessions": sorted({s.id for s in changed} | {sid for sid, _, _, _ in grown}),
                },
            )
        )
        mark.minute = minute
    return found


def frame(name: str, data: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


@router.get(
    "/stream",
    summary="Live changes",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream(request: Request) -> StreamingResponse:
    """Server-sent events: ``hello``, then ``sessions``, ``events`` and ``activity``."""
    store: Store = request.app.state.store

    def look(mark: Watermark | None):
        with store.reading() as db:
            if mark is None:
                return start(db), []
            return mark, changes(db, mark)

    async def events() -> AsyncIterator[bytes]:
        mark, _ = await run_in_threadpool(look, None)
        yield frame("hello", {"from": mark.as_dict()})
        quiet = 0.0
        while not await request.is_disconnected():
            await asyncio.sleep(TICK_SECONDS)
            _, found = await run_in_threadpool(look, mark)
            for name, data in found:
                yield frame(name, data)
            quiet = 0.0 if found else quiet + TICK_SECONDS
            if quiet >= KEEPALIVE_SECONDS:
                yield b": still here\n\n"
                quiet = 0.0

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
