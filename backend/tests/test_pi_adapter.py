"""The Pi adapter, against the synthetic fixture and hand-made entries."""

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.access.guard import assert_untouched
from agdash.adapters import pi
from agdash.adapters.pi import PATTERNS, Writer, parse_record, session_id_of
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session, SourceHealth
from agdash.events import validate_event
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.vocab import EventKind, ThoughtsCoverage

FIXTURES = Path(__file__).parent / "fixtures" / "harness" / "pi"
SESSION_ID = "ae2777e1-f701-460b-a586-0b12feec5e97"


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


def ingest(store, root: Path, writer: Writer | None = None):
    access = ReadOnlyAccess([root], snapshot_dir=root.parent / "snapshots-elsewhere")
    ingestor = Ingestor(access, SqlLedger(store, writer or Writer()), harness=pi.HARNESS)
    return ingestor.scan(discover_files(access, [root], PATTERNS))


def fixture_file(root: Path = FIXTURES) -> Path:
    (path,) = root.glob("agent/sessions/*/*.jsonl")
    return path


def source_counts(path: Path) -> dict[str, int]:
    counts = {"thinking": 0, "call": 0, "result": 0, "usage": 0, "compaction": 0}
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record["type"] == "compaction":
            counts["compaction"] += 1
            counts["usage"] += "usage" in record
            continue
        message = record.get("message") or {}
        if message.get("role") == "toolResult":
            counts["result"] += 1
        if message.get("role") == "assistant":
            counts["usage"] += "usage" in message
            for item in message["content"]:
                counts["thinking"] += item["type"] == "thinking"
                counts["call"] += item["type"] == "toolCall"
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
        "call": kinds.get(EventKind.TOOL_CALL, 0),
        "result": kinds.get(EventKind.TOOL_RESULT, 0),
        "usage": kinds.get(EventKind.USAGE, 0),
        "compaction": kinds.get(EventKind.COMPACTION, 0),
    }


def the_session(db, native_id: str = SESSION_ID) -> Session:
    return db.scalars(
        select(Session).where(Session.harness == pi.HARNESS, Session.native_id == native_id)
    ).one()


def write_session(root: Path, native_id: str, records: list[dict]) -> Path:
    folder = root / "agent" / "sessions" / "--work-example--"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"2024-12-01T00-00-00-000Z_{native_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


# -- the fixture ---------------------------------------------------------------------


def test_the_session_id_comes_from_the_file_name():
    assert session_id_of(str(fixture_file())) == SESSION_ID
    assert session_id_of("/x/odd-name.jsonl") == "odd-name"


def test_the_fixture_session_is_ingested_in_full(store):
    report = ingest(store, FIXTURES)
    assert report.lines > 0 and not report.refused
    with store() as db:
        stored = the_session(db)
        assert stored.cwd and stored.cwd.startswith("/work/")
        assert stored.initial_task and stored.models and stored.effort == "off"
        assert stored.started_at and stored.last_activity_at > stored.started_at
        assert stored_counts(db, stored.id) == source_counts(fixture_file())


def test_reasoning_is_stored_in_full(store):
    ingest(store, FIXTURES)
    with store() as db:
        coverages = set(
            db.scalars(select(Event.thoughts_coverage).where(Event.kind == EventKind.THINKING))
        )
    assert coverages == {ThoughtsCoverage.FULL}


def test_usage_keeps_cost_model_and_provider(store):
    ingest(store, FIXTURES)
    with store() as db:
        usages = db.scalars(select(Event.payload).where(Event.kind == EventKind.USAGE)).all()
    messages = [u for u in usages if u.get("extra", {}).get("purpose") != "compaction"]
    assert all(u["scope"] == "message" and u["cost_currency"] == "USD" for u in messages)
    assert all(u["model"] and u["extra"]["provider"] for u in messages)
    assert any(u.get("extra", {}).get("purpose") == "compaction" for u in usages)


def test_the_first_model_choice_and_the_thinking_level_are_events(store):
    ingest(store, FIXTURES)
    with store() as db:
        stored = the_session(db)
        (change,) = db.scalars(
            select(Event.payload).where(
                Event.session_id == stored.id, Event.kind == EventKind.MODEL_CHANGE
            )
        ).all()
        assert change["model"] == stored.models[0] and "previous_model" not in change
        assert change["extra"]["provider"]
        levels = db.scalars(
            select(Event.payload).where(
                Event.session_id == stored.id, Event.kind == EventKind.STATUS
            )
        ).all()
        assert {"status": "thinking_level_change", "detail": "off"} in levels


def test_compaction_keeps_its_summary_and_size(store):
    ingest(store, FIXTURES)
    with store() as db:
        (compaction,) = db.scalars(
            select(Event.payload).where(Event.kind == EventKind.COMPACTION)
        ).all()
    assert compaction["trigger"] == "auto" and compaction["summary"]
    assert compaction["tokens_before"] > 0


def test_every_stored_event_follows_the_contract_and_nothing_is_unknown(store):
    ingest(store, FIXTURES)
    with store() as db:
        for stored in db.scalars(select(Event)):
            validate_event(stored.kind, stored.payload, stored.thoughts_coverage)
        rows = db.scalars(select(SourceHealth)).all()
        assert [row.format_version for row in rows] == ["3"]
        assert rows[0].unknown_record_count == 0 and rows[0].parse_error_count == 0


def test_rerunning_adds_nothing(store):
    ingest(store, FIXTURES)
    with store() as db:
        before = db.scalar(select(func.count()).select_from(Event))
    assert ingest(store, FIXTURES).lines == 0
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == before


def test_reading_leaves_the_fixture_untouched(store):
    with assert_untouched([FIXTURES]):
        ingest(store, FIXTURES)


# -- model and provider changes -------------------------------------------------------

NATIVE = "1c6a7d2f-8b3e-4d4c-af20-3e4d5c6b7a81"


def model_change(provider: str, model: str) -> dict:
    return {"type": "model_change", "provider": provider, "modelId": model}


def test_model_and_provider_switches_are_model_changes_across_reads(store, tmp_path):
    root = tmp_path / "pi"
    entries = [
        {"type": "session", "version": 3, "id": NATIVE, "cwd": "/work/example"},
        model_change("local", "qwen-a"),
        model_change("local", "qwen-a"),
        model_change("cloud", "qwen-a"),
        model_change("cloud", "claude-b"),
    ]
    path = write_session(root, NATIVE, entries[:3])
    ingest(store, root)
    with open(path, "a") as rest:
        rest.write("".join(json.dumps(r) + "\n" for r in entries[3:]))
    ingest(store, root, Writer())
    with store() as db:
        stored = the_session(db, NATIVE)
        changes = db.scalars(
            select(Event.payload)
            .where(Event.session_id == stored.id, Event.kind == EventKind.MODEL_CHANGE)
            .order_by(Event.sequence)
        ).all()
    summary = [
        (
            c.get("previous_model"),
            c["model"],
            c["extra"].get("previous_provider"),
            c["extra"]["provider"],
        )
        for c in changes
    ]
    assert summary == [
        (None, "qwen-a", None, "local"),
        ("qwen-a", "qwen-a", "local", "cloud"),
        ("qwen-a", "claude-b", "cloud", "cloud"),
    ]
    assert stored.models == ["qwen-a", "claude-b"]


# -- unknown, broken and secret entries ------------------------------------------------


def test_unknown_types_and_broken_lines_are_counted_not_fatal(store, tmp_path):
    root = tmp_path / "pi"
    shutil.copytree(FIXTURES, root)
    with open(fixture_file(root), "a") as rest:
        rest.write(json.dumps({"type": "brand_new_entry"}) + "\n")
        rest.write("{not json\n")
        rest.write(
            json.dumps(
                {
                    "type": "message",
                    "message": {"role": "user", "content": [{"type": "text", "text": "later"}]},
                }
            )
            + "\n"
        )
    ingest(store, root)
    with store() as db:
        rows = db.scalars(select(SourceHealth)).all()
        assert sum(r.unknown_record_count for r in rows) == 1
        assert sum(r.parse_error_count for r in rows) == 1
        prompts = db.scalars(select(Event.payload).where(Event.kind == EventKind.USER_PROMPT)).all()
        assert {"text": "later"} in prompts


def test_secrets_are_redacted_before_storage(store, tmp_path):
    root = tmp_path / "pi"
    key = "sk-ant-api03-" + "Z" * 60
    write_session(
        root,
        NATIVE,
        [
            {
                "type": "message",
                "message": {"role": "user", "content": [{"type": "text", "text": f"use {key}"}]},
            },
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "toolCall",
                            "id": "call_1",
                            "name": "bash",
                            "arguments": {"command": f"export API_KEY={key}"},
                        }
                    ],
                },
            },
        ],
    )
    ingest(store, root)
    with store() as db:
        dumped = json.dumps([e.payload for e in db.scalars(select(Event))])
        assert key not in dumped and key not in the_session(db, NATIVE).initial_task
        assert "[REDACTED" in dumped


# -- single entries ------------------------------------------------------------------


def assistant(*content: dict, **message) -> dict:
    return {
        "type": "message",
        "message": {"role": "assistant", "content": list(content), **message},
    }


def test_a_thinking_block_without_text_is_encrypted_coverage():
    (thinking,) = parse_record(
        assistant({"type": "thinking", "thinking": "", "thinkingSignature": "sig"})
    ).events
    assert thinking.thoughts_coverage is ThoughtsCoverage.ENCRYPTED and thinking.payload == {}


def test_tool_results_keep_call_name_and_error_but_not_images():
    (result,) = parse_record(
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "call_7",
                "toolName": "read",
                "isError": True,
                "content": [
                    {"type": "text", "text": "no such file"},
                    {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
                ],
            },
        }
    ).events
    assert result.payload == {
        "output": "no such file",
        "is_error": True,
        "call_id": "call_7",
        "name": "read",
        "extra": {"images": 1},
    }


def test_usage_is_normalised_with_its_cost():
    parsed = parse_record(
        assistant(
            {"type": "text", "text": "done"},
            model="qwen-a",
            provider="local",
            stopReason="stop",
            usage={
                "input": 10,
                "output": 5,
                "cacheRead": 90,
                "cacheWrite": 0,
                "totalTokens": 105,
                "cost": {"total": 0.25},
            },
        )
    )
    text, usage = parsed.events
    assert text.payload == {"text": "done", "model": "qwen-a", "extra": {"stop_reason": "stop"}}
    assert usage.payload == {
        "scope": "message",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 90,
        "cache_write_tokens": 0,
        "reported_cost": 0.25,
        "cost_currency": "USD",
        "model": "qwen-a",
        "extra": {"provider": "local"},
    }


def test_known_metadata_is_not_unknown_and_new_types_are():
    assert parse_record({"type": "session", "version": 3}).unknown_type is None
    assert parse_record({"type": "message", "message": {"role": "system"}}).unknown_type is None
    assert parse_record({"type": "hologram"}).unknown_type == "hologram"
    assert (
        parse_record({"type": "message", "message": {"role": "oracle"}}).unknown_type
        == "message/oracle"
    )
