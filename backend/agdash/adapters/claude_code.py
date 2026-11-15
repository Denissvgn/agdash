"""Claude Code: transcripts, subagent transcripts, titles and the session registry.

Layout under the Claude Code root:

    projects/<cwd-slug>/<session-id>.jsonl                      a session
    projects/<cwd-slug>/<session-id>/subagents/<agent>.jsonl    its subagents
    sessions/<pid>.json                                         live registry

A subagent becomes a session of its own, linked to its parent session by the
folder it sits in. The registry is read for liveness only; its messaging socket
is a harness control channel and is never used.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
from agdash.ingest.engine import Cursor, Line
from agdash.normalise import UsageFormat, normalise_usage, parse_timestamp
from agdash.vocab import EventKind, Harness, Surface, ThoughtsCoverage

HARNESS = Harness.CLAUDE_CODE.value
TRANSCRIPTS = ["projects/*/*.jsonl"]
SUBAGENTS = ["projects/*/*/subagents/*.jsonl"]
PATTERNS = TRANSCRIPTS + SUBAGENTS

SURFACES = {
    "claude-desktop": Surface.DESKTOP,
    "cli": Surface.CLI,
    "claude-vscode": Surface.IDE,
    "claude-jetbrains": Surface.IDE,
    "sdk-cli": Surface.EXEC,
    "sdk-ts": Surface.EXEC,
    "sdk-py": Surface.EXEC,
}

# Record types that carry no event: request plumbing, queue bookkeeping, file
# history and accounting. Known, so they are not counted as unknown.
METADATA_TYPES = frozenset(
    {
        "api-request",
        "api-request-blob",
        "api-request-shape",
        "queue-operation",
        "last-prompt",
        "atis-latch",
        "session-owner",
        "tools-used",
        "cost-state",
        "file-history-snapshot",
        "file-history-delta",
        "relocated",
        "agent-name",
        "attachment",
        "summary",
    }
)


# -- where things are ---------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    native_id: str
    parent_native_id: str | None = None


def source_for(path: str) -> Source:
    """The session a transcript file belongs to, from its place in the layout."""
    parts = Path(path).parts
    if len(parts) >= 3 and parts[-2] == "subagents":
        parent = parts[-3]
        return Source(native_id=f"{parent}/{Path(parts[-1]).stem}", parent_native_id=parent)
    return Source(native_id=Path(path).stem)


# -- reading one record -------------------------------------------------------------


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def _facts(record: dict) -> SessionFacts:
    facts = SessionFacts(timestamp=parse_timestamp(record.get("timestamp")))
    for key, attribute in (
        ("cwd", "cwd"),
        ("gitBranch", "branch"),
        ("permissionMode", "permission_mode"),
    ):
        if isinstance(record.get(key), str) and record[key]:
            setattr(facts, attribute, record[key])
    if isinstance(record.get("effort"), str):
        facts.effort = record["effort"]
    if isinstance(record.get("entrypoint"), str):
        facts.surface = SURFACES.get(record["entrypoint"])
    return facts


def _user(record: dict, facts: SessionFacts, when: datetime | None) -> list[ParsedEvent]:
    if record.get("isMeta"):
        return []
    content = (record.get("message") or {}).get("content")
    events: list[ParsedEvent] = []
    if isinstance(content, str):
        items: list[Any] = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        items = content
    else:
        return []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text" and item.get("text"):
            text, truncated = bounded(item["text"])
            payload: dict[str, Any] = {"text": text}
            if truncated:
                payload["extra"] = {"truncated": True}
            events.append(ParsedEvent(EventKind.USER_PROMPT, payload, when))
            facts.initial_task = facts.initial_task or item["text"]
        elif item.get("type") == "tool_result":
            output, truncated = bounded(_text_of(item.get("content")))
            payload = {"output": output, "is_error": bool(item.get("is_error"))}
            if isinstance(item.get("tool_use_id"), str):
                payload["call_id"] = item["tool_use_id"]
            if truncated:
                payload["truncated"] = True
            events.append(ParsedEvent(EventKind.TOOL_RESULT, payload, when))
    return events


def _assistant(record: dict, facts: SessionFacts, when: datetime | None) -> list[ParsedEvent]:
    message = record.get("message") or {}
    model = message.get("model") if isinstance(message.get("model"), str) else None
    if model and model != "<synthetic>":
        facts.model = model
    events: list[ParsedEvent] = []
    for item in message.get("content") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "thinking":
            text = item.get("thinking") or ""
            if text.strip():
                text, _ = bounded(text)
                events.append(
                    ParsedEvent(EventKind.THINKING, {"text": text}, when, ThoughtsCoverage.FULL)
                )
            else:
                # The reasoning happened, but only its signature was written down.
                events.append(ParsedEvent(EventKind.THINKING, {}, when, ThoughtsCoverage.ENCRYPTED))
        elif kind == "redacted_thinking":
            events.append(ParsedEvent(EventKind.THINKING, {}, when, ThoughtsCoverage.ENCRYPTED))
        elif kind == "text" and item.get("text"):
            text, truncated = bounded(item["text"])
            payload: dict[str, Any] = {"text": text}
            extra = (
                {"stop_reason": message.get("stop_reason")} if message.get("stop_reason") else {}
            )
            if truncated:
                extra["truncated"] = True
            if model:
                payload["model"] = model
            if extra:
                payload["extra"] = extra
            events.append(ParsedEvent(EventKind.ASSISTANT_TEXT, payload, when))
        elif kind in ("tool_use", "server_tool_use") and item.get("name"):
            payload = {"name": item["name"], "input": item.get("input")}
            if isinstance(item.get("id"), str):
                payload["call_id"] = item["id"]
            events.append(ParsedEvent(EventKind.TOOL_CALL, payload, when))
    usage = normalise_usage(UsageFormat.ANTHROPIC, message.get("usage"))
    if usage:
        if model:
            usage["model"] = model
        # Claude Code repeats a message's usage on every record of that message;
        # readers keep one usage per message id.
        if isinstance(message.get("id"), str):
            usage["extra"] = {"message_id": message["id"]}
        events.append(ParsedEvent(EventKind.USAGE, usage, when))
    return events


def parse_record(record: dict) -> ParsedRecord:
    """What one transcript record means: events, session facts, or an unknown type."""
    facts = _facts(record)
    when = facts.timestamp
    kind = record.get("type")
    parsed = ParsedRecord(facts=facts)
    if kind == "user":
        parsed.events = _user(record, facts, when)
    elif kind == "assistant":
        parsed.events = _assistant(record, facts, when)
    elif kind == "custom-title" and isinstance(record.get("customTitle"), str):
        facts.title, facts.title_rank = record["customTitle"], 2
    elif kind == "ai-title" and isinstance(record.get("aiTitle"), str):
        facts.title, facts.title_rank = record["aiTitle"], 1
    elif kind == "system":
        subtype = record.get("subtype")
        if subtype == "compact_boundary":
            parsed.events = [ParsedEvent(EventKind.COMPACTION, {"trigger": "auto"}, when)]
        elif isinstance(subtype, str) and subtype:
            parsed.events = [ParsedEvent(EventKind.STATUS, {"status": subtype}, when)]
    elif kind not in METADATA_TYPES:
        parsed.unknown_type = str(kind)
    return parsed


# -- storing ------------------------------------------------------------------------


class Writer:
    """Turns transcript lines into stored sessions and events, for the ingest ledger."""

    def __init__(self) -> None:
        self._title_ranks: dict[int, int] = {}

    def __call__(self, db: DbSession, cursor: Cursor, lines: Sequence[Line]) -> None:
        source = source_for(cursor.path)
        session = find_or_create_session(db, HARNESS, source.native_id)
        if source.parent_native_id and session.parent_session_id is None:
            link_parent(db, session, HARNESS, source.parent_native_id)
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
            if isinstance(record.get("version"), str) and re.fullmatch(
                r"\d+\.\d+\.\d+", record["version"]
            ):
                version = record["version"]
            parsed = parse_record(record)
            if parsed.unknown_type:
                unknown += 1
            apply_facts(session, parsed.facts, self._title_ranks)
            store_events(db, session, line.offset, parsed.events, cursor.path)
        record_health(db, HARNESS, version, unknown, errors)


# -- the live registry --------------------------------------------------------------


@dataclass(frozen=True)
class RegistryEntry:
    """A running Claude Code process as its registry file describes it."""

    pid: int
    session_id: str
    cwd: str | None
    entrypoint: str | None
    status: str | None
    status_at: datetime | None


REGISTRY_FIELDS = ("pid", "sessionId", "cwd", "entrypoint", "status", "statusUpdatedAt")


def read_registry(access: ReadOnlyAccess, root: Path) -> list[RegistryEntry]:
    """Every registry entry, keeping only the fields liveness needs."""
    folder = root / "sessions"
    if not access.exists(folder):
        return []
    entries = []
    for path in access.list_dir(folder):
        if path.suffix != ".json":
            continue
        try:
            with access.open(path, "r") as source:
                raw = json.load(source)
        except (ValueError, OSError):
            continue
        if not isinstance(raw, dict):
            continue
        record = {key: raw.get(key) for key in REGISTRY_FIELDS}
        del raw
        if not isinstance(record["pid"], int) or not isinstance(record["sessionId"], str):
            continue
        entries.append(
            RegistryEntry(
                pid=record["pid"],
                session_id=record["sessionId"],
                cwd=record["cwd"] if isinstance(record["cwd"], str) else None,
                entrypoint=record["entrypoint"] if isinstance(record["entrypoint"], str) else None,
                status=record["status"] if isinstance(record["status"], str) else None,
                status_at=parse_timestamp(record["statusUpdatedAt"]),
            )
        )
    return sorted(entries, key=lambda entry: entry.pid)
