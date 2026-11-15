"""Codex: rollouts, the archive, the title index, subagent threads and goals.

Layout under the Codex root:

    sessions/YYYY/MM/DD/rollout-<time>-<session-id>.jsonl   a session
    archived_sessions/rollout-<time>-<session-id>.jsonl.zst the same, archived
    session_index.jsonl                                     thread titles
    goals_1.sqlite                                          session objectives

A session's identity is the id in its file name, so archiving a rollout keeps
its identity. The archive holds the rollout compressed; the ingest engine reads
it decompressed, at the same offsets, so nothing is stored twice. Which
running process holds which rollout is the process probe's business (open
files), not this module's.

Codex records a prompt both as a model input and, depending on the version, as
a "user_message" event or with a "retained_source" marker. Only the latter two
count as prompts; other user-role inputs are context Codex injected itself.
Messages between agents become prompts marked with their sender and recipient.

A subagent rollout starts with a copy of its parent's history. Its first
record says where its own history begins; everything before that is skipped.
Reasoning is stored as its summary when there is one, and otherwise only
marked as encrypted.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from agdash.access import ReadOnlyAccess
from agdash.adapters.store import (
    INITIAL_TASK_LIMIT,
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
from agdash.db.models import Event, Session
from agdash.ingest.engine import Cursor, Line
from agdash.normalise import UsageFormat, normalise_usage, parse_timestamp
from agdash.redaction import redact_text
from agdash.vocab import EventKind, Harness, Surface, ThoughtsCoverage

HARNESS = Harness.CODEX.value
TITLE_INDEX = "session_index.jsonl"
GOALS = "goals_1.sqlite"
PATTERNS = [
    "sessions/*/*/*/rollout-*.jsonl",
    "archived_sessions/rollout-*.jsonl",
    "archived_sessions/rollout-*.jsonl.zst",
    "archived_sessions/*/*/*/rollout-*.jsonl",
    "archived_sessions/*/*/*/rollout-*.jsonl.zst",
    TITLE_INDEX,
]
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
ROLLOUT = re.compile(rf"rollout-.+-(?P<id>{_UUID})\.jsonl(?:\.zst)?")

SURFACES_BY_ORIGINATOR = {
    "Codex Desktop": Surface.DESKTOP,
    "codex_vscode": Surface.IDE,
    "codex_exec": Surface.EXEC,
    "codex-tui": Surface.CLI,  # the terminal UI, although it reports source "vscode"
    "codex_cli_rs": Surface.CLI,  # older CLI releases
}
SURFACES_BY_SOURCE = {"exec": Surface.EXEC, "cli": Surface.CLI, "vscode": Surface.IDE}

# The status a subagent's first record produces; it carries where its own
# history starts, so a later read can skip the inherited part again.
SUBAGENT_STARTED = "subagent_started"

# Known record types that carry no event of their own: bookkeeping, settings,
# and records that repeat what another record already says.
METADATA = frozenset(
    {
        "world_state",
        "token_usage_record",
        "inter_agent_communication_metadata",
        "response_item/additional_tools",
        "response_item/configuration_update",
        "event_msg/item_completed",
        "event_msg/thread_settings_applied",
        "event_msg/agent_message",
        "event_msg/agent_reasoning",
        "event_msg/exec_command_begin",
        "event_msg/exec_command_end",
    }
)
TOOL_CALLS = frozenset({"function_call", "custom_tool_call", "local_shell_call", "web_search_call"})
TOOL_OUTPUTS = frozenset({"function_call_output", "custom_tool_call_output"})
STATUS_EVENTS = frozenset({"task_started", "task_complete", "turn_aborted"})


def session_id_of(path: str) -> str | None:
    """The session a rollout belongs to, from its file name."""
    match = ROLLOUT.fullmatch(Path(path).name)
    return match["id"] if match else None


# -- reading one record -------------------------------------------------------------


def _surface(meta: dict) -> Surface | None:
    surface = SURFACES_BY_ORIGINATOR.get(meta.get("originator") or "")
    if surface is None and isinstance(meta.get("source"), str):
        surface = SURFACES_BY_SOURCE.get(meta["source"])
    return surface


def _text(content: Any, kinds: frozenset[str]) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item["text"]
        for item in content
        if isinstance(item, dict)
        and item.get("type") in kinds
        and isinstance(item.get("text"), str)
    )


INPUT = frozenset({"input_text"})
OUTPUT = frozenset({"output_text"})
SUMMARY = frozenset({"summary_text"})
ANY_TEXT = frozenset({"input_text", "output_text", "text"})


def _arguments(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _output(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("content"), str):
        return raw["content"]
    if isinstance(raw, list):
        return _text(raw, ANY_TEXT)
    return json.dumps(raw, ensure_ascii=False) if raw is not None else ""


def _prompt(text: str, facts: SessionFacts, when: datetime | None, extra: dict | None = None):
    body, truncated = bounded(text)
    payload: dict[str, Any] = {"text": body}
    extra = dict(extra or {})
    if truncated:
        extra["truncated"] = True
    if extra:
        payload["extra"] = extra
    facts.initial_task = facts.initial_task or text
    return ParsedEvent(EventKind.USER_PROMPT, payload, when)


def _session_meta(payload: dict, session_id: str | None, parsed: ParsedRecord) -> None:
    if session_id is not None and payload.get("id") != session_id:
        return  # a copy of the parent's header inside a subagent rollout
    facts = parsed.facts
    if isinstance(payload.get("cwd"), str):
        facts.cwd = payload["cwd"]
    facts.surface = _surface(payload)
    start = payload.get("subagent_history_start_ordinal")
    parent = payload.get("parent_thread_id")
    extra: dict[str, Any] = {}
    if isinstance(start, int):
        extra["history_start_ordinal"] = start
    if isinstance(parent, str) and parent != payload.get("id"):
        extra["parent_thread_id"] = parent
    if extra:
        parsed.events.append(
            ParsedEvent(
                EventKind.STATUS, {"status": SUBAGENT_STARTED, "extra": extra}, facts.timestamp
            )
        )


def _message(record: dict, payload: dict, parsed: ParsedRecord) -> None:
    when = parsed.facts.timestamp
    role = payload.get("role")
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    if role == "user" and metadata.get("retained_source"):
        text = _text(payload.get("content"), INPUT)
        if text.strip():
            parsed.events.append(_prompt(text, parsed.facts, when))
    elif role == "assistant":
        text = _text(payload.get("content"), OUTPUT)
        if text.strip():
            body, truncated = bounded(text)
            event: dict[str, Any] = {"text": body}
            extra: dict[str, Any] = {}
            if isinstance(payload.get("phase"), str):
                extra["phase"] = payload["phase"]
            if truncated:
                extra["truncated"] = True
            if extra:
                event["extra"] = extra
            parsed.events.append(ParsedEvent(EventKind.ASSISTANT_TEXT, event, when))


def _agent_message(payload: dict, parsed: ParsedRecord) -> None:
    text = _text(payload.get("content"), INPUT)
    if not text.strip():
        return
    extra = {
        key: payload[key] for key in ("author", "recipient") if isinstance(payload.get(key), str)
    }
    extra["from_agent"] = True
    parsed.events.append(_prompt(text, parsed.facts, parsed.facts.timestamp, extra))


def _reasoning(payload: dict, parsed: ParsedRecord) -> None:
    when = parsed.facts.timestamp
    summary = _text(payload.get("summary"), SUMMARY)
    if summary.strip():
        text, _ = bounded(summary)
        event = ParsedEvent(EventKind.THINKING, {"text": text}, when, ThoughtsCoverage.SUMMARY)
    elif payload.get("encrypted_content"):
        event = ParsedEvent(EventKind.THINKING, {}, when, ThoughtsCoverage.ENCRYPTED)
    else:
        event = ParsedEvent(EventKind.THINKING, {}, when, ThoughtsCoverage.ABSENT)
    parsed.events.append(event)


def _tool_call(payload: dict, parsed: ParsedRecord) -> None:
    inner = payload["type"]
    name = payload.get("name") if isinstance(payload.get("name"), str) else None
    event: dict[str, Any] = {"name": name or inner.removesuffix("_call")}
    if inner == "function_call":
        event["input"] = _arguments(payload.get("arguments"))
    elif "input" in payload:
        event["input"] = payload["input"]
    elif "action" in payload:
        event["input"] = payload["action"]
    if isinstance(payload.get("call_id"), str) and payload["call_id"]:
        event["call_id"] = payload["call_id"]
    parsed.events.append(ParsedEvent(EventKind.TOOL_CALL, event, parsed.facts.timestamp))


def _tool_output(payload: dict, parsed: ParsedRecord) -> None:
    output, truncated = bounded(_output(payload.get("output")))
    event: dict[str, Any] = {"output": output}
    if isinstance(payload.get("call_id"), str) and payload["call_id"]:
        event["call_id"] = payload["call_id"]
    if truncated:
        event["truncated"] = True
    parsed.events.append(ParsedEvent(EventKind.TOOL_RESULT, event, parsed.facts.timestamp))


def _turn_context(payload: dict, facts: SessionFacts) -> None:
    for key, attribute in (
        ("model", "model"),
        ("effort", "effort"),
        ("approval_policy", "permission_mode"),
        ("cwd", "cwd"),
    ):
        if isinstance(payload.get(key), str) and payload[key]:
            setattr(facts, attribute, payload[key])


def parse_record(record: dict, session_id: str | None = None) -> ParsedRecord:
    """What one rollout record means for the session ``session_id``."""
    kind = record.get("type")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    inner = payload.get("type") if isinstance(payload.get("type"), str) else None
    parsed = ParsedRecord(facts=SessionFacts(timestamp=parse_timestamp(record.get("timestamp"))))
    when = parsed.facts.timestamp

    if kind == "session_meta":
        _session_meta(payload, session_id, parsed)
    elif kind == "turn_context":
        _turn_context(payload, parsed.facts)
    elif kind == "compacted" or (kind == "response_item" and inner == "compaction"):
        body: dict[str, Any] = {}
        if isinstance(payload.get("message"), str) and payload["message"].strip():
            body["summary"] = bounded(payload["message"])[0]
        parsed.events.append(ParsedEvent(EventKind.COMPACTION, body, when))
    elif kind == "response_item" and inner == "message":
        _message(record, payload, parsed)
    elif kind == "response_item" and inner == "agent_message":
        _agent_message(payload, parsed)
    elif kind == "response_item" and inner == "reasoning":
        _reasoning(payload, parsed)
    elif kind == "response_item" and inner in TOOL_CALLS:
        _tool_call(payload, parsed)
    elif kind == "response_item" and inner in TOOL_OUTPUTS:
        _tool_output(payload, parsed)
    elif kind == "event_msg" and inner == "user_message":
        text = payload.get("message") if isinstance(payload.get("message"), str) else ""
        if text.strip():
            parsed.events.append(_prompt(text, parsed.facts, when))
    elif kind == "event_msg" and inner == "token_count":
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        usage = normalise_usage(UsageFormat.OPENAI, info.get("last_token_usage"))
        if usage:
            parsed.events.append(ParsedEvent(EventKind.USAGE, usage, when))
    elif kind == "event_msg" and inner in STATUS_EVENTS:
        parsed.events.append(ParsedEvent(EventKind.STATUS, {"status": inner}, when))
    else:
        label = f"{kind}/{inner}" if inner else str(kind)
        if label not in METADATA and kind not in METADATA:
            parsed.unknown_type = label
    return parsed


# -- storing ------------------------------------------------------------------------


class Writer:
    """Turns rollout and title-index lines into stored sessions and events."""

    def __init__(self) -> None:
        self._title_ranks: dict[int, int] = {}
        self._history_start: dict[int, int | None] = {}
        self._model: dict[int, str | None] = {}

    def __call__(self, db: DbSession, cursor: Cursor, lines: Sequence[Line]) -> None:
        if Path(cursor.path).name == TITLE_INDEX:
            self._titles(db, lines)
            return
        native_id = session_id_of(cursor.path)
        if native_id is None:
            return
        session = find_or_create_session(db, HARNESS, native_id)
        unknown = errors = 0
        version = None
        for line in lines:
            try:
                record = json.loads(line.data)
            except ValueError:
                errors += 1
                continue
            if not isinstance(record, dict):
                errors += 1
                continue
            meta = record.get("payload") if record.get("type") == "session_meta" else None
            if isinstance(meta, dict) and meta.get("id") == native_id:
                if isinstance(meta.get("cli_version"), str):
                    version = meta["cli_version"]
            if self._inherited(db, session, record):
                continue
            parsed = parse_record(record, native_id)
            if parsed.unknown_type:
                unknown += 1
            self._follow(db, session, parsed)
            self._note_model_change(db, session, parsed)
            apply_facts(session, parsed.facts, self._title_ranks)
            store_events(db, session, line.offset, parsed.events, cursor.path)
        record_health(db, HARNESS, version, unknown, errors)

    # A subagent's own history and its parent.

    def _follow(self, db: DbSession, session: Session, parsed: ParsedRecord) -> None:
        for event in parsed.events:
            if event.kind is not EventKind.STATUS or event.payload["status"] != SUBAGENT_STARTED:
                continue
            extra = event.payload["extra"]
            if isinstance(extra.get("history_start_ordinal"), int):
                self._history_start[session.id] = extra["history_start_ordinal"]
            parent = extra.get("parent_thread_id")
            if isinstance(parent, str) and session.parent_session_id is None:
                link_parent(db, session, HARNESS, parent)

    def _inherited(self, db: DbSession, session: Session, record: dict) -> bool:
        """Whether a record in a subagent rollout is a copy of its parent's history."""
        ordinal = record.get("ordinal")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool):
            return False
        if session.id not in self._history_start:
            self._history_start[session.id] = self._stored_start(db, session)
        start = self._history_start[session.id]
        if start is None:
            return False
        # The subagent's own header precedes the copy, so it is never skipped.
        if record.get("type") == "session_meta":
            meta = record.get("payload") or {}
            if meta.get("id") == session.native_id:
                return False
        return ordinal < start

    @staticmethod
    def _stored_start(db: DbSession, session: Session) -> int | None:
        for payload in db.scalars(
            select(Event.payload).where(
                Event.session_id == session.id, Event.kind == EventKind.STATUS
            )
        ):
            if payload.get("status") == SUBAGENT_STARTED:
                start = payload.get("extra", {}).get("history_start_ordinal")
                return start if isinstance(start, int) else None
        return None

    # Model changes between turns.

    def _note_model_change(self, db: DbSession, session: Session, parsed: ParsedRecord) -> None:
        model = parsed.facts.model
        if not model:
            return
        if session.id not in self._model:
            self._model[session.id] = self._stored_model(db, session)
        previous = self._model[session.id]
        self._model[session.id] = model
        if previous is None or previous == model:
            return
        change: dict[str, Any] = {"model": model, "previous_model": previous}
        if parsed.facts.effort:
            change["effort"] = parsed.facts.effort
        parsed.events.append(ParsedEvent(EventKind.MODEL_CHANGE, change, parsed.facts.timestamp))

    @staticmethod
    def _stored_model(db: DbSession, session: Session) -> str | None:
        latest = db.scalar(
            select(Event.payload)
            .where(Event.session_id == session.id, Event.kind == EventKind.MODEL_CHANGE)
            .order_by(Event.sequence.desc())
            .limit(1)
        )
        if latest is not None:
            return latest.get("model")
        return session.models[0] if session.models else None

    # Thread titles.

    def _titles(self, db: DbSession, lines: Sequence[Line]) -> None:
        for line in lines:
            try:
                entry = json.loads(line.data)
            except ValueError:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                continue
            name = entry.get("thread_name")
            if isinstance(name, str) and name.strip():
                session = find_or_create_session(db, HARNESS, entry["id"])
                apply_facts(session, SessionFacts(title=name, title_rank=1), self._title_ranks)


# -- goals --------------------------------------------------------------------------

GOAL_STATUSES = frozenset(
    {"active", "paused", "blocked", "usage_limited", "budget_limited", "complete"}
)


@dataclass(frozen=True)
class Goal:
    """The objective Codex keeps for one session, with its status and budget."""

    session_id: str
    objective: str
    status: str
    token_budget: int | None
    tokens_used: int
    time_used_seconds: int
    created_at: datetime | None
    updated_at: datetime | None

    def document(self) -> dict[str, Any]:
        return {
            "objective": redact_text(self.objective)[:INITIAL_TASK_LIMIT],
            "status": self.status,
            "token_budget": self.token_budget,
            "tokens_used": self.tokens_used,
            "time_used_seconds": self.time_used_seconds,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def read_goals(access: ReadOnlyAccess, root: Path) -> list[Goal]:
    """Every session goal, read from a private snapshot of the goals store."""
    database = root / GOALS
    if not access.exists(database):
        return []
    with access.sqlite_snapshot(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "thread_goals" not in tables:
            return []
        rows = db.execute(
            "SELECT thread_id, objective, status, token_budget, tokens_used,"
            " time_used_seconds, created_at_ms, updated_at_ms FROM thread_goals"
        ).fetchall()
    goals = []
    for thread, objective, status, budget, used, seconds, created, updated in rows:
        if not isinstance(thread, str) or not isinstance(objective, str):
            continue
        goals.append(
            Goal(
                session_id=thread,
                objective=objective,
                status=status if status in GOAL_STATUSES else "unknown",
                token_budget=_count(budget),
                tokens_used=_count(used) or 0,
                time_used_seconds=_count(seconds) or 0,
                created_at=parse_timestamp(created),
                updated_at=parse_timestamp(updated),
            )
        )
    return sorted(goals, key=lambda goal: goal.session_id)


def store_goals(db: DbSession, goals: Sequence[Goal]) -> None:
    """Put each goal on its session; a session not seen yet is created for it."""
    for goal in goals:
        session = find_or_create_session(db, HARNESS, goal.session_id)
        document = goal.document()
        if session.goal != document:
            session.goal = document
