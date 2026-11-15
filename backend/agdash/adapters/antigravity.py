"""Antigravity, desktop app and CLI: conversation summaries and transcripts.

Layout under each Antigravity base (``antigravity`` for the app,
``antigravity-cli`` for the CLI):

    conversation_summaries.db                       one row per conversation, and
                                                    full-text bodies of its turns
    brain/<conversation-id>/.system_generated/logs/transcript_full.jsonl
                                                    every step, one line each
    conversations/<conversation-id>.db              step blobs (not read here)
    presence/<id>.lock                              left behind; proves nothing

The transcript is the main source: prompts, reasoning, tool calls, tool output,
model and token counts, each with its step number. A planner step lists the
tool calls it makes; the steps after it hold their output, in the same order,
so calls and results are paired by position. The CLI's reply to the person is
the summary it passes to its ``finish`` tool. A conversation without one
gets its user and agent text from the full-text bodies instead, and loses that
text once a transcript appears.

The summary database gives the title, workspace, status and parent of every
conversation, and the harness's own view of whether it is working. It is read
from a private snapshot copy. Presence lock files stay behind after a
conversation ends, so they are never read.
"""

import json
import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from agdash.access import ReadOnlyAccess
from agdash.adapters.store import (
    ParsedEvent,
    ParsedRecord,
    SessionFacts,
    apply_facts,
    bounded,
    find_or_create_session,
    link_parent,
    record_health,
    store_events,
)
from agdash.collector.state import HarnessStatus
from agdash.db.models import Event, Session
from agdash.ingest.engine import Cursor, Line
from agdash.normalise import UsageFormat, normalise_usage, parse_timestamp
from agdash.vocab import EventKind, Harness, Surface, ThoughtsCoverage

HARNESS = Harness.ANTIGRAVITY.value
SUMMARIES = "conversation_summaries.db"
TRANSCRIPT = "transcript_full.jsonl"
PATTERNS = [f"brain/*/.system_generated/logs/{TRANSCRIPT}"]
SURFACES = {"antigravity": Surface.DESKTOP, "antigravity-cli": Surface.CLI}

# Event sequences. Transcript lines use byte offsets (well below 2**52); the
# summary's status and the full-text fallback sit in ranges of their own.
STATUS_BASE = 1 << 52
BODY_BASE = 1 << 53
EVENTS_PER_STEP = 32

# The effort level is the last part of the model name: gemini-4-argon-high.
EFFORTS = ("minimal", "low", "medium", "high", "max")

# Steps that report what a tool did; their content is the tool's output.
TOOL_STEPS = frozenset(
    {
        "RUN_COMMAND",
        "VIEW_FILE",
        "CODE_ACTION",
        "GREP_SEARCH",
        "LIST_DIRECTORY",
        "FIND",
        "READ_URL_CONTENT",
        "INVOKE_SUBAGENT",
    }
)
METADATA = frozenset({"SYSTEM_MESSAGE", "CONVERSATION_HISTORY"})


def conversation_of(path: str) -> str:
    """The conversation a transcript belongs to, from its place in the layout."""
    return Path(path).parts[-4]


def surface_of(path: str) -> Surface | None:
    """App or CLI, from the base folder a file sits in."""
    for part in reversed(Path(path).parts):
        if part in SURFACES:
            return SURFACES[part]
    return None


# -- reading one transcript step ----------------------------------------------------


def _effort(model: str) -> str | None:
    last = model.rsplit("-", 1)[-1]
    return last if last in EFFORTS else None


def _extra(payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
    extra = {key: value for key, value in extra.items() if value not in (None, False, 0)}
    if extra:
        payload["extra"] = extra
    return payload


def _text_event(kind: EventKind, text: str, when, **extra) -> ParsedEvent:
    body, truncated = bounded(text)
    return ParsedEvent(kind, _extra({"text": body}, truncated=truncated, **extra), when)


def _media(record: dict) -> int | None:
    media = record.get("media")
    return len(media) if isinstance(media, list) and media else None


def _planner(record: dict, parsed: ParsedRecord) -> None:
    facts, when = parsed.facts, parsed.facts.timestamp
    model = record.get("model") if isinstance(record.get("model"), str) else None
    if model:
        facts.model = model
        facts.effort = _effort(model)
    thinking = record.get("thinking")
    if isinstance(thinking, str) and thinking.strip():
        body, _ = bounded(thinking)
        parsed.events.append(
            ParsedEvent(EventKind.THINKING, {"text": body}, when, ThoughtsCoverage.FULL)
        )
    content = record.get("content")
    if isinstance(content, str) and content.strip():
        parsed.events.append(_text_event(EventKind.ASSISTANT_TEXT, content, when))
    step = record.get("step_index")
    for number, call in enumerate(record.get("tool_calls") or []):
        if not (isinstance(call, dict) and isinstance(call.get("name"), str) and call["name"]):
            continue
        payload: dict[str, Any] = {"name": call["name"], "input": call.get("args")}
        if isinstance(step, int):
            payload["call_id"] = f"{step}.{number}"
        parsed.events.append(ParsedEvent(EventKind.TOOL_CALL, payload, when))
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        if call["name"] == "finish" and isinstance(args.get("summary"), str):
            parsed.events.append(
                _text_event(EventKind.ASSISTANT_TEXT, args["summary"], when, via="finish")
            )
    counts = {
        "input_tokens": record.get("input_tokens"),
        "cache_read_input_tokens": record.get("cache_read_tokens"),
        "output_tokens": record.get("output_tokens"),
    }
    # Input here leaves out what was read from the cache, as Anthropic counts it.
    usage = normalise_usage(
        UsageFormat.ANTHROPIC, {k: v for k, v in counts.items() if v is not None}
    )
    if usage:
        if model:
            usage["model"] = model
        parsed.events.append(ParsedEvent(EventKind.USAGE, usage, when))


def _tool_step(kind: str, record: dict, parsed: ParsedRecord, name: str | None = "") -> None:
    content = record.get("content") if isinstance(record.get("content"), str) else ""
    output, truncated = bounded(content)
    payload: dict[str, Any] = {"output": output}
    if name is not None:
        payload["name"] = name or kind.lower()
    exit_code = record.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        payload["is_error"] = exit_code != 0
    if record.get("status") == "ERROR":
        payload["is_error"] = True
    if truncated:
        payload["truncated"] = True
    _extra(
        payload,
        exit_code=exit_code,
        running=record.get("status") == "RUNNING",
        images=_media(record),
    )
    parsed.events.append(ParsedEvent(EventKind.TOOL_RESULT, payload, parsed.facts.timestamp))


def parse_step(record: dict) -> ParsedRecord:
    """What one transcript step means: events, session facts, or an unknown type."""
    kind = record.get("type")
    when: datetime | None = parse_timestamp(record.get("created_at"))
    parsed = ParsedRecord(facts=SessionFacts(timestamp=when))
    content = record.get("content") if isinstance(record.get("content"), str) else ""
    if kind == "USER_INPUT":
        if content.strip():
            parsed.events.append(
                _text_event(EventKind.USER_PROMPT, content, when, images=_media(record))
            )
            parsed.facts.initial_task = content
    elif kind == "PLANNER_RESPONSE":
        _planner(record, parsed)
    elif kind == "GENERIC":
        # The output of the tool a planner step called, with a header of times.
        _tool_step(kind, record, parsed, name=None)
        if isinstance(record.get("error"), str) and record["error"].strip():
            parsed.events.append(
                ParsedEvent(
                    EventKind.STATUS,
                    {"status": "error", "detail": bounded(record["error"])[0]},
                    when,
                )
            )
    elif kind == "ERROR_MESSAGE":
        detail = record.get("error") if isinstance(record.get("error"), str) else content
        parsed.events.append(
            ParsedEvent(EventKind.STATUS, {"status": "error", "detail": bounded(detail)[0]}, when)
        )
    elif kind == "CHECKPOINT":
        payload: dict[str, Any] = {}
        if content.strip():
            payload["summary"] = bounded(content)[0]
        parsed.events.append(ParsedEvent(EventKind.COMPACTION, payload, when))
    elif kind in TOOL_STEPS:
        _tool_step(kind, record, parsed)
    elif kind not in METADATA:
        parsed.unknown_type = str(kind)
        if record.get("source") == "MODEL" and content:
            _tool_step(str(kind), record, parsed)  # most likely a new kind of tool
    return parsed


class Writer:
    """Turns transcript lines into stored sessions and events."""

    def __init__(self) -> None:
        self._title_ranks: dict[int, int] = {}
        self._has_transcript: set[int] = set()
        self._pending: dict[int, deque[tuple[str | None, str]]] = {}

    def __call__(self, db: DbSession, cursor: Cursor, lines: Sequence[Line]) -> None:
        session = find_or_create_session(db, HARNESS, conversation_of(cursor.path))
        surface = surface_of(cursor.path)
        if surface and not session.surface:
            session.surface = surface
        if session.id not in self._has_transcript:
            # The transcript replaces any text taken from the full-text bodies.
            db.execute(
                delete(Event).where(Event.session_id == session.id, Event.sequence >= BODY_BASE)
            )
            self._has_transcript.add(session.id)
        unknown = errors = 0
        for line in lines:
            try:
                record = json.loads(line.data)
            except ValueError:
                errors += 1
                continue
            if not isinstance(record, dict):
                errors += 1
                continue
            parsed = parse_step(record)
            if parsed.unknown_type:
                unknown += 1
            self._pair(session, record, parsed)
            apply_facts(session, parsed.facts, self._title_ranks)
            store_events(db, session, line.offset, parsed.events, cursor.path)
        record_health(db, HARNESS, None, unknown, errors)

    def _pair(self, session: Session, record: dict, parsed: ParsedRecord) -> None:
        """Give tool output the id and name of the call it answers, in call order."""
        pending = self._pending.setdefault(session.id, deque())
        if record.get("type") == "PLANNER_RESPONSE":
            pending.clear()
        elif record.get("type") == "USER_INPUT":
            pending.clear()
        for index, event in enumerate(parsed.events):
            if event.kind is EventKind.TOOL_CALL:
                pending.append((event.payload.get("call_id"), event.payload["name"]))
            elif event.kind is EventKind.TOOL_RESULT and pending:
                call_id, name = pending.popleft()
                payload = dict(event.payload)
                if call_id:
                    payload["call_id"] = call_id
                step_name = payload.get("name")
                payload["name"] = name
                if step_name and step_name != name:  # e.g. code_action for write_to_file
                    payload["extra"] = {**payload.get("extra", {}), "step": step_name}
                parsed.events[index] = ParsedEvent(event.kind, payload, event.occurred_at)


# -- the summary database -----------------------------------------------------------


@dataclass(frozen=True)
class Turn:
    step: int
    author: str  # "user" or "agent"
    text: str


@dataclass(frozen=True)
class Summary:
    """One conversation as the summary database describes it."""

    conversation_id: str
    title: str
    preview: str
    workspace: str | None
    status: str
    step_count: int
    last_modified: datetime | None
    parent_id: str | None
    working: bool  # the harness says it is not fully idle
    killed: bool
    archived: bool
    agent_name: str | None
    source: str = ""
    turns: tuple[Turn, ...] = field(default=())


def _workspace(raw: Any) -> str | None:
    """The first workspace folder, as a path."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        uris = json.loads(raw)
    except ValueError:
        uris = [raw]
    for uri in uris if isinstance(uris, list) else []:
        if isinstance(uri, str) and uri.startswith("file://"):
            return unquote(urlparse(uri).path) or None
    return None


def step_offsets(raw: Any) -> list[tuple[int, int]]:
    """``[[step, byte offset], ...]`` as the full-text table stores it."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        pairs = json.loads(raw)
    except ValueError:
        return []
    found = []
    for pair in pairs if isinstance(pairs, list) else []:
        if (
            isinstance(pair, list)
            and len(pair) == 2
            and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in pair)
        ):
            found.append((pair[0], pair[1]))
    return sorted(found, key=lambda pair: pair[1])


def split_body(body: Any, offsets: Any, author: str) -> list[Turn]:
    """The turns of one full-text body, cut at the byte offsets of their steps."""
    if not isinstance(body, str) or not body:
        return []
    data = body.encode("utf-8")
    pairs = [(step, offset) for step, offset in step_offsets(offsets) if offset <= len(data)]
    turns = []
    for index, (step, offset) in enumerate(pairs):
        end = pairs[index + 1][1] if index + 1 < len(pairs) else len(data)
        text = data[offset:end].decode("utf-8", errors="replace").strip()
        if text:
            turns.append(Turn(step, author, text))
    return turns


def _table_names(db) -> set[str]:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def read_summaries(access: ReadOnlyAccess, base: Path) -> list[Summary]:
    """Every conversation in one base's summary database, read from a private snapshot."""
    database = base / SUMMARIES
    if not access.exists(database):
        return []
    with access.sqlite_snapshot(database) as db:
        tables = _table_names(db)
        if "conversation_summaries" not in tables:
            return []
        columns = [row[1] for row in db.execute('PRAGMA table_info("conversation_summaries")')]
        rows = [
            dict(zip(columns, row, strict=True))
            for row in db.execute('SELECT * FROM "conversation_summaries"')
        ]
        parents = {}
        if "conversation_parents" in tables:
            parents = dict(
                db.execute("SELECT conversation_id, parent_id FROM conversation_parents")
            )
        archived: set[str] = set()
        turns: dict[str, list[Turn]] = {}
        if "conversations_fts" in tables:
            for cid, chunk, is_archived, user, user_offsets, agent, agent_offsets in db.execute(
                "SELECT conversation_id, chunk_index, archived, user_body,"
                " user_step_byte_offsets, agent_body, agent_step_byte_offsets"
                " FROM conversations_fts ORDER BY conversation_id, chunk_index"
            ):
                if chunk == -1:
                    if is_archived:
                        archived.add(cid)
                    continue
                found = turns.setdefault(cid, [])
                found += split_body(user, user_offsets, "user")
                found += split_body(agent, agent_offsets, "agent")
    summaries = []
    for row in rows:
        cid = row.get("conversation_id")
        if not isinstance(cid, str) or not cid:
            continue
        parent = row.get("parent_conversation_id") or parents.get(cid)
        summaries.append(
            Summary(
                conversation_id=cid,
                title=row.get("title") or "",
                preview=row.get("preview") or "",
                workspace=_workspace(row.get("workspace_uris")),
                status=row.get("status") or "",
                step_count=row.get("step_count") if isinstance(row.get("step_count"), int) else 0,
                last_modified=parse_timestamp(row.get("last_modified_time")),
                parent_id=parent if isinstance(parent, str) and parent else None,
                working=bool(row.get("not_fully_idle")),
                killed=bool(row.get("killed")),
                archived=cid in archived,
                agent_name=row.get("agent_name") or None,
                source=row.get("source") or "",
                turns=tuple(sorted(turns.get(cid, []), key=lambda t: (t.step, t.author))),
            )
        )
    return sorted(summaries, key=lambda summary: summary.conversation_id)


def _short_status(raw: str) -> str:
    """``CASCADE_RUN_STATUS_IDLE`` as ``idle``."""
    return re.sub(r"^(CASCADE_)?(RUN_)?STATUS_", "", raw).lower() or "unknown"


def harness_status(summary: Summary) -> HarnessStatus | None:
    """What Antigravity itself says about a conversation, for the state machine."""
    if summary.working:
        return HarnessStatus.BUSY
    short = _short_status(summary.status)
    if "idle" in short:
        return HarnessStatus.IDLE
    if "wait" in short:
        return HarnessStatus.WAITING
    if "run" in short or "busy" in short:
        return HarnessStatus.BUSY
    return None


def store_summaries(
    db: DbSession, summaries: Sequence[Summary], surface: Surface | None = None
) -> None:
    """Put each conversation's summary on its session, with its text if it has no transcript."""
    title_ranks: dict[int, int] = {}
    for summary in summaries:
        session = find_or_create_session(db, HARNESS, summary.conversation_id)
        facts = SessionFacts(
            cwd=summary.workspace,
            # The app's database lists CLI conversations too; its source tells them apart.
            surface=Surface.CLI if "CLI" in summary.source else surface,
            title=summary.title or summary.preview or None,
            title_rank=1 if summary.title else 0,
            timestamp=summary.last_modified,
        )
        apply_facts(session, facts, title_ranks)
        if summary.parent_id and session.parent_session_id is None:
            link_parent(db, session, HARNESS, summary.parent_id)
        status = {
            "status": _short_status(summary.status),
            "detail": summary.status,
        }
        _extra(
            status,
            not_fully_idle=summary.working,
            killed=summary.killed,
            archived=summary.archived,
            agent_name=summary.agent_name,
        )
        # One status per step count: a new one when the conversation moves on.
        store_events(
            db,
            session,
            STATUS_BASE // EVENTS_PER_STEP + summary.step_count,
            [ParsedEvent(EventKind.STATUS, status, summary.last_modified)],
            SUMMARIES,
        )
        if not _has_transcript(db, session):
            _store_turns(db, session, summary)


def _has_transcript(db: DbSession, session: Session) -> bool:
    return (
        db.scalar(
            select(Event.id)
            .where(Event.session_id == session.id, Event.sequence < STATUS_BASE)
            .limit(1)
        )
        is not None
    )


def _store_turns(db: DbSession, session: Session, summary: Summary) -> None:
    """User and agent text from the full-text bodies, one event per step and author."""
    for turn in summary.turns:
        user = turn.author == "user"
        event = _text_event(
            EventKind.USER_PROMPT if user else EventKind.ASSISTANT_TEXT,
            turn.text,
            None,
            source="full_text",
        )
        store_events(
            db,
            session,
            BODY_BASE // EVENTS_PER_STEP + turn.step,
            [event],
            SUMMARIES,
            first_index=0 if user else 1,
        )
        if user and not session.initial_task:
            apply_facts(session, SessionFacts(initial_task=turn.text), {})
