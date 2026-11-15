"""Export one session as JSONL, Markdown or its archived raw lines.

Exporting is something a person asks for. From the command line it writes to
the path given, and only there:

    python -m agdash.export SESSION_ID --format markdown --out ~/Desktop/session.md

The file must not exist yet, may not be inside a harness directory, and is
created readable by its owner only. The API offers the same content as a
download, which the person saves where they choose; the service writes no file.

Events are read page by page, so a long session is never held in memory.
"""

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.archive import raw_lines
from agdash.config import database_url
from agdash.db.migrate import sync_database_url
from agdash.db.models import Event, Session
from agdash.platform.roots import is_inside

FORMATS = {"jsonl": ".jsonl", "markdown": ".md", "raw": ".raw.jsonl"}
MEDIA_TYPES = {
    "jsonl": "application/x-ndjson",
    "markdown": "text/markdown; charset=utf-8",
    "raw": "application/x-ndjson",
}
PAGE = 500
MAX_OUTPUT_CHARS = 4000


class ExportRefused(Exception):
    """The export would write somewhere it must not. The message says why."""


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def session_header(session: Session) -> dict[str, Any]:
    return {
        "type": "session",
        "id": session.id,
        "harness": session.harness,
        "native_id": session.native_id,
        "surface": session.surface,
        "title": session.title,
        "cwd": session.cwd,
        "branch": session.branch,
        "models": session.models or [],
        "effort": session.effort,
        "parent_session_id": session.parent_session_id,
        "started_at": _iso(session.started_at),
        "last_activity_at": _iso(session.last_activity_at),
        "ended_at": _iso(session.ended_at),
        "state": session.state,
        "goal": session.goal,
    }


def events_of(db: DbSession, session_id: int) -> Iterator[Event]:
    after = -1
    while True:
        page = db.scalars(
            select(Event)
            .where(Event.session_id == session_id, Event.sequence > after)
            .order_by(Event.sequence)
            .limit(PAGE)
        ).all()
        if not page:
            return
        yield from page
        after = page[-1].sequence
        db.expunge_all()


def jsonl(db: DbSession, session: Session) -> Iterator[str]:
    yield json.dumps(session_header(session), ensure_ascii=False) + "\n"
    for event in events_of(db, session.id):
        record = {
            "type": "event",
            "sequence": event.sequence,
            "occurred_at": _iso(event.occurred_at),
            "kind": event.kind,
            "payload": event.payload,
        }
        if event.thoughts_coverage:
            record["thoughts_coverage"] = event.thoughts_coverage
        yield json.dumps(record, ensure_ascii=False) + "\n"


def _fence(text: str, language: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{language}\n{text}\n{ticks}\n"


def _cut(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… ({len(text) - MAX_OUTPUT_CHARS} more characters)"


def _when(event: Event) -> str:
    return f" · {event.occurred_at:%Y-%m-%d %H:%M:%S} UTC" if event.occurred_at else ""


def _markdown_event(event: Event) -> str | None:
    payload, kind = event.payload, str(event.kind)
    if kind == "user_prompt":
        return f"## Prompt{_when(event)}\n\n{payload.get('text', '')}\n"
    if kind == "assistant_text":
        return f"### Reply{_when(event)}\n\n{payload.get('text', '')}\n"
    if kind == "thinking":
        if payload.get("text"):
            label = "Thinking" if event.thoughts_coverage == "full" else "Thinking (summary)"
            return f"<details><summary>{label}</summary>\n\n{payload['text']}\n\n</details>\n"
        return f"_Thinking ({event.thoughts_coverage or 'not kept'}, not kept)_\n"
    if kind == "tool_call":
        body = payload.get("input")
        shown = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
        return f"**Tool call** `{payload.get('name', '?')}`\n\n" + _fence(_cut(shown or ""), "json")
    if kind == "tool_result":
        output = payload.get("output")
        shown = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        label = "**Result (error)**" if payload.get("is_error") else "**Result**"
        return f"{label}\n\n" + _fence(_cut(shown or ""))
    if kind == "compaction":
        summary = payload.get("summary")
        return "_Context compacted._\n" + (f"\n> {summary}\n" if summary else "")
    if kind == "model_change":
        return f"_Model changed to `{payload.get('model')}`._\n"
    if kind == "subagent":
        what = payload.get("description") or payload.get("native_id") or ""
        return f"_Subagent {payload.get('phase')}: {what}_\n"
    if kind == "status":
        detail = f" — {payload['detail']}" if payload.get("detail") else ""
        return f"_Status: {payload.get('status')}{detail}_\n"
    return None  # usage is summed, not listed


def markdown(db: DbSession, session: Session) -> Iterator[str]:
    yield f"# {session.title or f'Session {session.id}'}\n\n"
    facts = [
        ("Harness", f"{session.harness}" + (f" ({session.surface})" if session.surface else "")),
        ("Folder", session.cwd),
        ("Branch", session.branch),
        ("Models", ", ".join(session.models or []) or None),
        ("Effort", session.effort),
        ("Started", f"{session.started_at:%Y-%m-%d %H:%M} UTC" if session.started_at else None),
        (
            "Last activity",
            f"{session.last_activity_at:%Y-%m-%d %H:%M} UTC" if session.last_activity_at else None,
        ),
    ]
    for label, value in facts:
        if value:
            yield f"- **{label}:** {value}\n"
    yield "\n---\n\n"
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
    seen_messages: set[str] = set()
    for event in events_of(db, session.id):
        if str(event.kind) == "usage":
            message = (event.payload.get("extra") or {}).get("message_id")
            if event.payload.get("scope") == "message" and (
                message is None or message not in seen_messages
            ):
                if message:
                    seen_messages.add(message)
                for key in usage:
                    usage[key] += event.payload.get(key) or 0
            continue
        rendered = _markdown_event(event)
        if rendered:
            yield rendered + "\n"
    if any(usage.values()):
        yield (
            "---\n\n"
            f"_Tokens: {usage['input_tokens']:,} in, {usage['output_tokens']:,} out,"
            f" {usage['cache_read_tokens']:,} read from cache._\n"
        )


def raw(db: DbSession, session: Session) -> Iterator[str]:
    for line in raw_lines(db, session.id):
        yield line + "\n"


RENDERERS = {"jsonl": jsonl, "markdown": markdown, "raw": raw}


def render(db: DbSession, session: Session, format_: str) -> Iterator[str]:
    if format_ not in RENDERERS:
        raise ValueError(f"export as one of: {', '.join(RENDERERS)}")
    return RENDERERS[format_](db, session)


def write_export(chunks: Iterator[str], target: Path, forbidden_roots: Sequence[Path]) -> int:
    """Write to ``target``, a new file the person named. Returns the bytes written."""
    target = target.expanduser()
    parent = target.parent.resolve(strict=False)
    if not parent.is_dir():
        raise ExportRefused(f"{target.parent} is not a folder")
    destination = parent / target.name
    if is_inside(destination, tuple(forbidden_roots)):
        raise ExportRefused("an export may not be written inside a harness directory")
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ExportRefused(f"{destination} exists; choose a new file name") from None
    written = 0
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as out:
        for chunk in chunks:
            out.write(chunk)
            written += len(chunk.encode("utf-8"))
    return written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agdash-export", description=__doc__.split("\n\n")[0])
    parser.add_argument("session", type=int)
    parser.add_argument("--format", choices=sorted(RENDERERS), default="markdown")
    parser.add_argument("--out", type=Path, required=True, help="a new file to write")
    args = parser.parse_args(argv)
    from agdash.platform import current_host

    engine = create_engine(sync_database_url(database_url()))
    try:
        with sessionmaker(engine)() as db:
            session = db.get(Session, args.session)
            if session is None:
                print(f"No session {args.session}.", file=sys.stderr)
                return 1
            written = write_export(
                render(db, session, args.format), args.out, current_host().all_harness_roots()
            )
    except ExportRefused as error:
        print(f"Not exported: {error}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"Exported session {args.session} ({written:,} bytes) to {args.out}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
