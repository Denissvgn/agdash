"""The Claude Code adapter, against the synthetic fixtures and hand-made records."""

import dataclasses
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.access.guard import assert_untouched
from agdash.adapters import claude_code
from agdash.adapters.claude_code import (
    PATTERNS,
    RegistryEntry,
    Writer,
    parse_record,
    read_registry,
    source_for,
)
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session, SourceHealth
from agdash.events import validate_event
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.vocab import EventKind, ParentBasis, Surface, ThoughtsCoverage

FIXTURES = Path(__file__).parent / "fixtures" / "harness" / "claude"


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def ingest(store, root: Path):
    access = ReadOnlyAccess([root], snapshot_dir=root.parent / "snapshots-elsewhere")
    ingestor = Ingestor(access, SqlLedger(store, Writer()), harness=claude_code.HARNESS)
    return ingestor.scan(discover_files(access, [root], PATTERNS))


def source_counts(path: Path) -> dict[str, int]:
    counts = {"thinking": 0, "tool_use": 0, "tool_result": 0}
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") not in ("assistant", "user") or record.get("isMeta"):
            continue
        content = (record.get("message") or {}).get("content")
        for item in content if isinstance(content, list) else []:
            if item.get("type") in ("thinking", "redacted_thinking"):
                counts["thinking"] += 1
            elif item.get("type") in counts:
                counts[item["type"]] += 1
    return counts


def stored_counts(db, session_id: int) -> dict[str, int]:
    kinds = dict(
        db.execute(
            select(Event.kind, func.count())
            .where(Event.session_id == session_id)
            .group_by(Event.kind)
        ).all()
    )
    return {
        "thinking": kinds.get(EventKind.THINKING, 0),
        "tool_use": kinds.get(EventKind.TOOL_CALL, 0),
        "tool_result": kinds.get(EventKind.TOOL_RESULT, 0),
    }


# -- the fixtures ---------------------------------------------------------------------


def test_discovery_finds_sessions_and_subagents():
    access = ReadOnlyAccess([FIXTURES], snapshot_dir=FIXTURES.parent.parent)
    found = discover_files(access, [FIXTURES], PATTERNS)
    sources = [source_for(str(path)) for path in found]
    assert len([s for s in sources if s.parent_native_id is None]) == 2
    assert len([s for s in sources if s.parent_native_id]) == 2


def test_every_fixture_session_is_ingested_in_full(store):
    report = ingest(store, FIXTURES)
    assert report.lines > 0 and not report.refused
    with store() as db:
        for path in FIXTURES.glob("projects/*/*.jsonl"):
            session = db.scalars(select(Session).where(Session.native_id == path.stem)).one()
            assert session.initial_task, path.name
            assert session.models, path.name
            assert session.effort in {"low", "medium", "high"}, path.name
            assert session.surface in {Surface.DESKTOP, Surface.EXEC}
            assert session.cwd and session.cwd.startswith("/work/")
            assert session.started_at and session.last_activity_at >= session.started_at
            assert stored_counts(db, session.id) == source_counts(path), path.name
        for path in FIXTURES.glob("projects/*/*/subagents/*.jsonl"):
            source = source_for(str(path))
            child = db.scalars(select(Session).where(Session.native_id == source.native_id)).one()
            assert child.initial_task and child.models
            assert stored_counts(db, child.id) == source_counts(path), path.name


def test_subagents_are_attached_to_their_parent(store):
    ingest(store, FIXTURES)
    with store() as db:
        children = db.scalars(select(Session).where(Session.parent_session_id.is_not(None))).all()
        assert len(children) == 2
        for child in children:
            parent = db.get(Session, child.parent_session_id)
            assert child.native_id.startswith(f"{parent.native_id}/")
            assert child.parent_basis is ParentBasis.HARNESS_RECORD
            assert parent.initial_task, "the parent is the real session, not a placeholder"


def test_rerunning_adds_nothing(store):
    ingest(store, FIXTURES)
    with store() as db:
        before = db.scalar(select(func.count()).select_from(Event))
    second = ingest(store, FIXTURES)
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == before
    assert second.lines == 0


def test_every_stored_event_follows_the_contract(store):
    ingest(store, FIXTURES)
    with store() as db:
        events = db.scalars(select(Event)).all()
        assert len(events) > 100
        for stored in events:
            validate_event(stored.kind, stored.payload, stored.thoughts_coverage)
        kinds = {stored.kind for stored in events}
        assert {
            EventKind.USER_PROMPT,
            EventKind.ASSISTANT_TEXT,
            EventKind.TOOL_CALL,
            EventKind.TOOL_RESULT,
            EventKind.THINKING,
            EventKind.USAGE,
        } <= kinds


def test_titles_prefer_the_one_the_person_chose(store):
    ingest(store, FIXTURES)
    with store() as db:
        titled = db.scalars(select(Session).where(Session.title.is_not(None))).all()
        assert titled, "the fixture with custom-title records has a title"


def test_reading_leaves_the_fixtures_untouched(store):
    with assert_untouched([FIXTURES]):
        ingest(store, FIXTURES)


# -- unknown and broken records ------------------------------------------------------


def test_unknown_types_and_broken_lines_are_counted_not_fatal(store, tmp_path):
    root = tmp_path / "claude"
    shutil.copytree(FIXTURES, root)
    transcript = next(p for p in root.glob("projects/*/*.jsonl"))
    with open(transcript, "a") as target:
        target.write(json.dumps({"type": "brand-new-record", "version": "9.9.9"}) + "\n")
        target.write("{not json\n")
        target.write(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "after the noise"},
                    "timestamp": "2024-12-26T00:00:00Z",
                }
            )
            + "\n"
        )
    ingest(store, root)
    with store() as db:
        health = {row.format_version: row for row in db.scalars(select(SourceHealth)).all()}
        assert sum(row.unknown_record_count for row in health.values()) == 1
        assert sum(row.parse_error_count for row in health.values()) == 1
        assert health["9.9.9"].unknown_record_count == 1
        session = db.scalars(select(Session).where(Session.native_id == transcript.stem)).one()
        texts = db.scalars(
            select(Event.payload).where(
                Event.session_id == session.id, Event.kind == EventKind.USER_PROMPT
            )
        ).all()
        assert {"text": "after the noise"} in texts, "lines after the noise are still read"


def test_secrets_are_redacted_before_storage(store, tmp_path):
    root = tmp_path / "claude"
    folder = root / "projects" / "-work-example"
    folder.mkdir(parents=True)
    key = "sk-ant-api03-" + "Z" * 60
    (folder / "0b5f6c1e-7a2d-4c3b-9e1f-2d3c4b5a6f70.jsonl").write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": f"use {key} please"}})
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-opus-5-5",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Bash",
                            "input": {"command": f"export API_KEY={key}"},
                        }
                    ],
                },
            }
        )
        + "\n"
    )
    ingest(store, root)
    with store() as db:
        dumped = json.dumps([e.payload for e in db.scalars(select(Event)).all()])
        session = db.scalars(select(Session)).one()
        assert key not in dumped and key not in session.initial_task
        assert "[REDACTED" in dumped


# -- single records ------------------------------------------------------------------


def test_a_thinking_block_with_text_is_full_coverage():
    parsed = parse_record(
        {
            "type": "assistant",
            "message": {
                "model": "m",
                "content": [
                    {"type": "thinking", "thinking": "weigh the options", "signature": "abc"}
                ],
            },
        }
    )
    (thought,) = parsed.events
    assert (thought.kind, thought.thoughts_coverage, thought.payload) == (
        EventKind.THINKING,
        ThoughtsCoverage.FULL,
        {"text": "weigh the options"},
    )


@pytest.mark.parametrize(
    "item",
    [
        {"type": "thinking", "thinking": "", "signature": "abc"},
        {"type": "redacted_thinking", "data": "opaque"},
    ],
)
def test_unreadable_thinking_is_encrypted_coverage_without_text(item):
    parsed = parse_record({"type": "assistant", "message": {"content": [item]}})
    (thought,) = parsed.events
    assert thought.thoughts_coverage is ThoughtsCoverage.ENCRYPTED and thought.payload == {}


def test_meta_prompts_are_not_the_initial_task():
    parsed = parse_record(
        {"type": "user", "isMeta": True, "message": {"content": "<command-name>/x</command-name>"}}
    )
    assert parsed.events == [] and parsed.facts.initial_task is None


def test_tool_results_keep_their_call_and_error_state():
    parsed = parse_record(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "is_error": True,
                        "content": [
                            {"type": "text", "text": "line one"},
                            {"type": "text", "text": "line two"},
                        ],
                    }
                ]
            },
        }
    )
    (result,) = parsed.events
    assert result.payload == {
        "output": "line one\nline two",
        "is_error": True,
        "call_id": "toolu_1",
    }


def test_usage_is_normalised_and_tagged_with_its_message():
    parsed = parse_record(
        {
            "type": "assistant",
            "message": {
                "id": "msg_1",
                "model": "claude-opus-5-5",
                "content": [],
                "usage": {
                    "input_tokens": 2,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 5,
                    "output_tokens": 7,
                },
            },
        }
    )
    (usage,) = parsed.events
    assert usage.payload == {
        "scope": "message",
        "input_tokens": 2,
        "output_tokens": 7,
        "cache_read_tokens": 10,
        "cache_write_tokens": 5,
        "model": "claude-opus-5-5",
        "extra": {"message_id": "msg_1"},
    }


def test_long_text_is_cut_and_marked():
    parsed = parse_record(
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x" * 70000}]
            },
        }
    )
    (result,) = parsed.events
    assert len(result.payload["output"]) == 64 * 1024 and result.payload["truncated"] is True


@pytest.mark.parametrize(
    ("entrypoint", "surface"),
    [
        ("claude-desktop", Surface.DESKTOP),
        ("cli", Surface.CLI),
        ("claude-vscode", Surface.IDE),
        ("sdk-cli", Surface.EXEC),
        ("something-new", None),
    ],
)
def test_entrypoints_map_to_surfaces(entrypoint, surface):
    assert parse_record({"type": "user", "entrypoint": entrypoint}).facts.surface is surface


def test_known_metadata_is_not_unknown_and_new_types_are():
    assert parse_record({"type": "api-request-blob"}).unknown_type is None
    assert parse_record({"type": "attachment", "attachment": {"type": "date"}}).unknown_type is None
    assert parse_record({"type": "hologram"}).unknown_type == "hologram"


# -- the registry ---------------------------------------------------------------------


def test_the_registry_is_read_for_liveness_only():
    access = ReadOnlyAccess([FIXTURES], snapshot_dir=FIXTURES.parent.parent)
    (entry,) = read_registry(access, FIXTURES)
    assert entry.status == "busy" and entry.entrypoint == "claude-desktop"
    assert entry.pid > 0 and len(entry.session_id) == 36
    assert entry.status_at is not None
    fields = {f.name for f in dataclasses.fields(RegistryEntry)}
    assert not any("socket" in name.lower() for name in fields), (
        "the control socket is never surfaced"
    )


def test_a_missing_or_broken_registry_is_empty(tmp_path):
    root = tmp_path / "claude"
    access = ReadOnlyAccess([root], snapshot_dir=tmp_path)
    assert read_registry(access, root) == []
    (root / "sessions").mkdir(parents=True)
    (root / "sessions" / "1.json").write_text("{broken")
    (root / "sessions" / "2.json").write_text(json.dumps({"pid": "not-a-number", "sessionId": "x"}))
    assert read_registry(access, root) == []
