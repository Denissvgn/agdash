"""Pi: one JSONL file per session, written as a tree of entries.

Layout under the Pi root (~/.pi/agent):

    sessions/--<cwd-slug>--/<time>_<session-id>.jsonl

Each entry points at its parent, so a session can branch when the person goes
back and edits. Entries are stored in the order they were written; the tree
links are not needed to show what happened.

Pi records the model and the thinking level as entries of their own when they
change, keeps the model's reasoning in full, and reports usage with its cost.
"""

import json
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from agdash.adapters.store import (
    ParsedEvent,
    ParsedRecord,
    SessionFacts,
    apply_facts,
    bounded,
    find_or_create_session,
    record_health,
    store_events,
)
from agdash.db.models import Event, Session
from agdash.ingest.engine import Cursor, Line
from agdash.normalise import UsageFormat, normalise_usage, parse_timestamp
from agdash.vocab import EventKind, Harness, ThoughtsCoverage

HARNESS = Harness.PI.value
# Relative to the Pi root, ~/.pi/agent.
PATTERNS = ["sessions/*/*.jsonl"]
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
SESSION_FILE = re.compile(rf"(?P<time>[^_]+)_(?P<id>{_UUID})\.jsonl")
THINKING_LEVEL = "thinking_level_change"

# Entry and message types that carry no event of their own.
METADATA = frozenset({"session", "message/system", "label", "custom"})


def session_id_of(path: str) -> str:
    """The session a file belongs to: the id in its name, or the name itself."""
    name = Path(path).name
    match = SESSION_FILE.fullmatch(name)
    return match["id"] if match else Path(path).stem


# -- reading one entry --------------------------------------------------------------


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item["text"]
        for item in content
        if isinstance(item, dict)
        and item.get("type") == "text"
        and isinstance(item.get("text"), str)
    )


def _with_extra(payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
    extra = {key: value for key, value in extra.items() if value not in (None, False)}
    if extra:
        payload["extra"] = extra
    return payload


def _user(message: dict, parsed: ParsedRecord) -> None:
    text = _text(message.get("content"))
    if text.strip():
        body, truncated = bounded(text)
        parsed.events.append(
            ParsedEvent(
                EventKind.USER_PROMPT,
                _with_extra({"text": body}, truncated=truncated),
                parsed.facts.timestamp,
            )
        )
        parsed.facts.initial_task = text


def _assistant(message: dict, parsed: ParsedRecord) -> None:
    facts, when = parsed.facts, parsed.facts.timestamp
    model = message.get("model") if isinstance(message.get("model"), str) else None
    if model:
        facts.model = model
    if isinstance(message.get("thinkingLevel"), str):
        facts.effort = message["thinkingLevel"]
    for item in message.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "thinking":
            text = item.get("thinking") if isinstance(item.get("thinking"), str) else ""
            if text.strip():
                body, _ = bounded(text)
                parsed.events.append(
                    ParsedEvent(EventKind.THINKING, {"text": body}, when, ThoughtsCoverage.FULL)
                )
            else:
                parsed.events.append(
                    ParsedEvent(EventKind.THINKING, {}, when, ThoughtsCoverage.ENCRYPTED)
                )
        elif kind == "text" and isinstance(item.get("text"), str) and item["text"].strip():
            body, truncated = bounded(item["text"])
            payload: dict[str, Any] = {"text": body}
            if model:
                payload["model"] = model
            _with_extra(payload, stop_reason=message.get("stopReason"), truncated=truncated)
            parsed.events.append(ParsedEvent(EventKind.ASSISTANT_TEXT, payload, when))
        elif kind == "toolCall" and isinstance(item.get("name"), str) and item["name"]:
            payload = {"name": item["name"], "input": item.get("arguments")}
            if isinstance(item.get("id"), str) and item["id"]:
                payload["call_id"] = item["id"]
            parsed.events.append(ParsedEvent(EventKind.TOOL_CALL, payload, when))
    usage = normalise_usage(UsageFormat.PI, message.get("usage"))
    if usage:
        if model:
            usage["model"] = model
        _with_extra(
            usage,
            provider=message.get("provider") if isinstance(message.get("provider"), str) else None,
        )
        parsed.events.append(ParsedEvent(EventKind.USAGE, usage, when))


def _tool_result(message: dict, parsed: ParsedRecord) -> None:
    output, truncated = bounded(_text(message.get("content")))
    payload: dict[str, Any] = {"output": output, "is_error": bool(message.get("isError"))}
    if isinstance(message.get("toolCallId"), str) and message["toolCallId"]:
        payload["call_id"] = message["toolCallId"]
    if isinstance(message.get("toolName"), str) and message["toolName"]:
        payload["name"] = message["toolName"]
    if truncated:
        payload["truncated"] = True
    content = message.get("content") if isinstance(message.get("content"), list) else []
    images = sum(1 for item in content if isinstance(item, dict) and item.get("type") == "image")
    _with_extra(payload, images=images or None)  # the image itself is never copied
    parsed.events.append(ParsedEvent(EventKind.TOOL_RESULT, payload, parsed.facts.timestamp))


def _compaction(record: dict, parsed: ParsedRecord) -> None:
    when = parsed.facts.timestamp
    payload: dict[str, Any] = {"trigger": "manual" if record.get("fromHook") else "auto"}
    if isinstance(record.get("summary"), str) and record["summary"].strip():
        payload["summary"] = bounded(record["summary"])[0]
    before = record.get("tokensBefore")
    if isinstance(before, int) and not isinstance(before, bool) and before >= 0:
        payload["tokens_before"] = before
    parsed.events.append(ParsedEvent(EventKind.COMPACTION, payload, when))
    # Writing the summary costs tokens too.
    usage = normalise_usage(UsageFormat.PI, record.get("usage"))
    if usage:
        parsed.events.append(
            ParsedEvent(EventKind.USAGE, _with_extra(usage, purpose="compaction"), when)
        )


def parse_record(record: dict) -> ParsedRecord:
    """What one session entry means: events, session facts, or an unknown type."""
    kind = record.get("type")
    when: datetime | None = parse_timestamp(record.get("timestamp"))
    parsed = ParsedRecord(facts=SessionFacts(timestamp=when))
    if kind == "session":
        if isinstance(record.get("cwd"), str) and record["cwd"]:
            parsed.facts.cwd = record["cwd"]
    elif kind == "model_change":
        if isinstance(record.get("modelId"), str) and record["modelId"]:
            parsed.facts.model = record["modelId"]
    elif kind == THINKING_LEVEL:
        level = record.get("thinkingLevel")
        if isinstance(level, str) and level:
            parsed.facts.effort = level
            parsed.events.append(
                ParsedEvent(EventKind.STATUS, {"status": THINKING_LEVEL, "detail": level}, when)
            )
    elif kind == "compaction":
        _compaction(record, parsed)
    elif kind == "message" and isinstance(record.get("message"), dict):
        message = record["message"]
        role = message.get("role")
        if role == "user":
            _user(message, parsed)
        elif role == "assistant":
            _assistant(message, parsed)
        elif role == "toolResult":
            _tool_result(message, parsed)
        elif f"message/{role}" not in METADATA:
            parsed.unknown_type = f"message/{role}"
    elif kind not in METADATA:
        parsed.unknown_type = str(kind)
    return parsed


# -- storing ------------------------------------------------------------------------


class Writer:
    """Turns session-file lines into stored sessions and events."""

    def __init__(self) -> None:
        self._title_ranks: dict[int, int] = {}
        self._model: dict[int, tuple[str | None, str | None]] = {}

    def __call__(self, db: DbSession, cursor: Cursor, lines: Sequence[Line]) -> None:
        session = find_or_create_session(db, HARNESS, session_id_of(cursor.path))
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
            if record.get("type") == "session" and isinstance(record.get("version"), int):
                version = str(record["version"])
            parsed = parse_record(record)
            if parsed.unknown_type:
                unknown += 1
            if record.get("type") == "model_change":
                self._model_change(db, session, record, parsed)
            apply_facts(session, parsed.facts, self._title_ranks)
            store_events(db, session, line.offset, parsed.events, cursor.path)
        record_health(db, HARNESS, version, unknown, errors)

    def _model_change(self, db: DbSession, session: Session, record: dict, parsed: ParsedRecord):
        """A model or provider switch, compared with what the session used before."""
        model = parsed.facts.model
        if not model:
            return
        provider = record.get("provider") if isinstance(record.get("provider"), str) else None
        if session.id not in self._model:
            self._model[session.id] = self._stored_model(db, session)
        previous_model, previous_provider = self._model[session.id]
        self._model[session.id] = (model, provider)
        if (previous_model, previous_provider) == (model, provider):
            return
        payload: dict[str, Any] = {"model": model}
        if previous_model:
            payload["previous_model"] = previous_model
        _with_extra(payload, provider=provider, previous_provider=previous_provider)
        parsed.events.append(ParsedEvent(EventKind.MODEL_CHANGE, payload, parsed.facts.timestamp))

    @staticmethod
    def _stored_model(db: DbSession, session: Session) -> tuple[str | None, str | None]:
        latest = db.scalar(
            select(Event.payload)
            .where(Event.session_id == session.id, Event.kind == EventKind.MODEL_CHANGE)
            .order_by(Event.sequence.desc())
            .limit(1)
        )
        if latest is None:
            return None, None
        return latest.get("model"), (latest.get("extra") or {}).get("provider")
