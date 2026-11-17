"""The Codex adapter, against the synthetic fixtures and hand-made records."""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.access.guard import assert_untouched
from agdash.adapters import codex
from agdash.adapters.codex import (
    PATTERNS,
    Goal,
    Writer,
    parse_record,
    read_goals,
    session_id_of,
    store_goals,
)
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session, SourceHealth
from agdash.events import validate_event
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.compressed import decompress
from agdash.ingest.ledger import SqlLedger
from agdash.vocab import EventKind, ParentBasis, Surface, ThoughtsCoverage

FIXTURES = Path(__file__).parent / "fixtures" / "harness" / "codex"
EXEC = "76f11fad-ff5f-4d44-bb76-832c79d3037f"
IDE = "34378b13-6c9f-46fa-ba34-edf339035cf3"
DESKTOP = "fa55f7f6-e5e1-4419-967f-112573380eb3"
SUBAGENT = "f6e9cc88-d14c-4409-ac34-fd576552916a"
CLI = "621104ed-f169-4e51-ac72-514437a306af"
ARCHIVED = "cc17346e-324f-46b6-8065-2029ddfbd7f5"


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
    ingestor = Ingestor(access, SqlLedger(store, writer or Writer()), harness=codex.HARNESS)
    return ingestor.scan(discover_files(access, [root], PATTERNS))


def rollout(native_id: str, root: Path = FIXTURES) -> Path:
    return next(root.glob(f"sessions/*/*/*/rollout-*-{native_id}.jsonl"))


def own_records(path: Path) -> list[dict]:
    """A rollout's records without the parent history a subagent inherits."""
    if path.suffix == ".zst":
        with open(path, "rb") as source:
            text = decompress(source, 1 << 30).decode()
    else:
        text = path.read_text()
    records = [json.loads(line) for line in text.splitlines()]
    start = records[0]["payload"].get("subagent_history_start_ordinal")
    if start is None:
        return records
    return [records[0]] + [r for r in records[1:] if r.get("ordinal", start) >= start]


def source_counts(path: Path) -> dict[str, int]:
    counts = {"prompt": 0, "thinking": 0, "call": 0, "result": 0}
    for record in own_records(path):
        payload = record.get("payload") or {}
        inner = payload.get("type")
        if record["type"] == "response_item" and inner == "message":
            if payload.get("role") == "user" and (record.get("metadata") or {}).get(
                "retained_source"
            ):
                counts["prompt"] += 1
        elif record["type"] == "response_item" and inner == "agent_message":
            counts["prompt"] += any(item.get("text") for item in payload["content"])
        elif record["type"] == "event_msg" and inner == "user_message":
            counts["prompt"] += 1
        elif inner == "reasoning":
            counts["thinking"] += 1
        elif inner in ("function_call", "custom_tool_call"):
            counts["call"] += 1
        elif inner in ("function_call_output", "custom_tool_call_output"):
            counts["result"] += 1
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
        "prompt": kinds.get(EventKind.USER_PROMPT, 0),
        "thinking": kinds.get(EventKind.THINKING, 0),
        "call": kinds.get(EventKind.TOOL_CALL, 0),
        "result": kinds.get(EventKind.TOOL_RESULT, 0),
    }


def session(db, native_id: str) -> Session:
    return db.scalars(
        select(Session).where(Session.harness == codex.HARNESS, Session.native_id == native_id)
    ).one()


# -- the fixtures --------------------------------------------------------------------


def test_discovery_finds_rollouts_the_archive_and_the_title_index():
    access = ReadOnlyAccess([FIXTURES], snapshot_dir=FIXTURES.parent.parent)
    found = discover_files(access, [FIXTURES], PATTERNS)
    names = [path.name for path in found]
    assert "session_index.jsonl" in names
    archived = [p for p in found if "archived_sessions" in p.parts]
    assert len(archived) == 2 and all(p.name.endswith(".jsonl.zst") for p in archived)
    ids = {session_id_of(str(path)) for path in found} - {None}
    assert ids == {EXEC, IDE, DESKTOP, SUBAGENT, CLI, ARCHIVED}


def test_every_fixture_session_is_ingested_in_full(store):
    report = ingest(store, FIXTURES)
    assert report.lines > 0 and not report.refused
    with store() as db:
        for native_id in (EXEC, IDE, DESKTOP, SUBAGENT, CLI):
            stored = session(db, native_id)
            assert stored.initial_task, native_id
            assert stored.models, native_id
            assert stored.effort, native_id
            assert stored.cwd and stored.cwd.startswith("/work/"), native_id
            assert stored.started_at and stored.last_activity_at >= stored.started_at
            assert stored_counts(db, stored.id) == source_counts(rollout(native_id)), native_id


def test_originators_map_to_surfaces(store):
    ingest(store, FIXTURES)
    with store() as db:
        assert session(db, EXEC).surface is Surface.EXEC
        assert session(db, IDE).surface is Surface.IDE
        assert session(db, DESKTOP).surface is Surface.DESKTOP
        assert session(db, SUBAGENT).surface is Surface.DESKTOP
        assert session(db, CLI).surface is Surface.CLI, "the terminal UI says vscode"


def test_the_older_cli_originator_is_recognised(store, tmp_path):
    root = tmp_path / "codex"
    target = root / "sessions" / "2024" / "12" / "23" / rollout(EXEC).name
    target.parent.mkdir(parents=True)
    lines = rollout(EXEC).read_text().splitlines()
    header = json.loads(lines[0])
    header["payload"].update(originator="codex_cli_rs", source="cli")
    target.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n")
    ingest(store, root)
    with store() as db:
        assert session(db, EXEC).surface is Surface.CLI


def test_an_archived_rollout_keeps_its_identity(store, tmp_path):
    ingest(store, FIXTURES)
    with store() as db:
        assert (
            db.scalar(select(func.count()).select_from(Session).where(Session.native_id == EXEC))
            == 1
        )
        both = db.scalars(
            select(Event.sequence).where(Event.session_id == session(db, EXEC).id)
        ).all()

    live_only = tmp_path / "live-only"
    target = live_only / rollout(EXEC).relative_to(FIXTURES)
    target.parent.mkdir(parents=True)
    shutil.copyfile(rollout(EXEC), target)
    url = f"sqlite:///{tmp_path / 'live.db'}"
    upgrade(url)
    engine = create_engine(url)
    alone = sessionmaker(engine, expire_on_commit=False)
    ingest(alone, live_only)
    with alone() as db:
        live = db.scalars(select(Event.sequence).where(Event.session_id == session(db, EXEC).id))
        assert sorted(both) == sorted(live), "live and archived copies are one set of events"
    engine.dispose()


def test_a_compressed_archive_is_ingested_in_full(store):
    ingest(store, FIXTURES)
    (path,) = FIXTURES.glob(f"archived_sessions/rollout-*-{ARCHIVED}.jsonl.zst")
    with store() as db:
        stored = session(db, ARCHIVED)
        assert stored.surface is Surface.IDE and stored.models
        assert stored_counts(db, stored.id) == source_counts(path)
        assert db.scalar(
            select(func.count()).select_from(Event).where(Event.session_id == stored.id)
        )


def test_a_subagent_is_linked_to_its_parent_without_its_parents_history(store):
    ingest(store, FIXTURES)
    with store() as db:
        child, parent = session(db, SUBAGENT), session(db, DESKTOP)
        assert child.parent_session_id == parent.id
        assert child.parent_basis is ParentBasis.HARNESS_RECORD
        assert parent.initial_task and parent.initial_task != child.initial_task
        inherited = rollout(SUBAGENT).read_text().splitlines()[1:183]
        earliest = db.scalar(
            select(func.min(Event.source_offset)).where(Event.session_id == child.id)
        )
        header = len(rollout(SUBAGENT).read_text().splitlines()[0]) + 1
        assert earliest == 0, "the subagent's own header is kept"
        later = db.scalars(
            select(Event.source_offset).where(Event.session_id == child.id, Event.source_offset > 0)
        ).all()
        assert min(later) >= header + sum(len(line) + 1 for line in inherited)


def test_subagent_history_is_skipped_on_a_later_read_too(store, tmp_path):
    root = tmp_path / "codex"
    target = root / "sessions" / "2024" / "12" / "18" / rollout(SUBAGENT).name
    target.parent.mkdir(parents=True)
    lines = rollout(SUBAGENT).read_text().splitlines(keepends=True)
    target.write_text("".join(lines[:2]))
    ingest(store, root)
    with open(target, "a") as rest:
        rest.write("".join(lines[2:]))
    ingest(store, root, Writer())
    with store() as db:
        child = session(db, SUBAGENT)
        assert stored_counts(db, child.id) == source_counts(rollout(SUBAGENT))


def test_inter_agent_messages_are_prompts_marked_with_their_sender(store):
    ingest(store, FIXTURES)
    with store() as db:
        prompts = db.scalars(
            select(Event.payload).where(
                Event.session_id == session(db, SUBAGENT).id, Event.kind == EventKind.USER_PROMPT
            )
        ).all()
        relayed = [p for p in prompts if p.get("extra", {}).get("from_agent")]
        assert relayed and all("author" in p["extra"] for p in relayed)


def test_titles_come_from_the_index(store):
    ingest(store, FIXTURES)
    index = [
        json.loads(line) for line in (FIXTURES / "session_index.jsonl").read_text().splitlines()
    ]
    with store() as db:
        for entry in index:
            assert session(db, entry["id"]).title == entry["thread_name"]


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
            EventKind.STATUS,
        } <= kinds


def test_only_known_fixture_records_and_no_parse_errors(store):
    ingest(store, FIXTURES)
    with store() as db:
        rows = db.scalars(select(SourceHealth).where(SourceHealth.adapter == codex.HARNESS)).all()
        assert rows, "the format version is recorded"
        assert sum(row.unknown_record_count for row in rows) == 0
        assert sum(row.parse_error_count for row in rows) == 0


def test_reading_leaves_the_fixtures_untouched(store):
    with assert_untouched([FIXTURES]):
        ingest(store, FIXTURES)


# -- unknown, broken and secret records ----------------------------------------------


def test_unknown_types_and_broken_lines_are_counted_not_fatal(store, tmp_path):
    root = tmp_path / "codex"
    shutil.copytree(FIXTURES, root)
    target = rollout(EXEC, root)
    with open(target, "a") as rest:
        rest.write(json.dumps({"type": "brand_new_record", "payload": {}}) + "\n")
        rest.write("{not json\n")
        rest.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": "2024-12-26T00:00:00Z",
                    "payload": {"type": "user_message", "message": "after the noise"},
                }
            )
            + "\n"
        )
    ingest(store, root)
    with store() as db:
        rows = db.scalars(select(SourceHealth)).all()
        assert sum(row.unknown_record_count for row in rows) == 1
        assert sum(row.parse_error_count for row in rows) == 1
        texts = db.scalars(
            select(Event.payload).where(
                Event.session_id == session(db, EXEC).id, Event.kind == EventKind.USER_PROMPT
            )
        ).all()
        assert {"text": "after the noise"} in texts


def test_secrets_are_redacted_before_storage(store, tmp_path):
    root = tmp_path / "codex"
    folder = root / "sessions" / "2024" / "12" / "01"
    folder.mkdir(parents=True)
    key = "sk-proj-" + "Z" * 60
    native_id = "0b5f6c1e-7a2d-4c3b-9e1f-2d3c4b5a6f70"
    records = [
        {"type": "session_meta", "payload": {"id": native_id, "originator": "codex_cli_rs"}},
        {"type": "event_msg", "payload": {"type": "user_message", "message": f"use {key}"}},
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "call_id": "call_1",
                "arguments": json.dumps({"command": ["bash", "-lc", f"export API_KEY={key}"]}),
            },
        },
    ]
    (folder / f"rollout-2024-12-01T00-00-00-{native_id}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records)
    )
    ingest(store, root)
    with store() as db:
        dumped = json.dumps([e.payload for e in db.scalars(select(Event)).all()])
        stored = session(db, native_id)
        assert key not in dumped and key not in stored.initial_task
        assert "[REDACTED" in dumped


def test_a_model_switch_between_turns_is_a_model_change(store, tmp_path):
    root = tmp_path / "codex"
    folder = root / "sessions" / "2024" / "12" / "01"
    folder.mkdir(parents=True)
    native_id = "1c6a7d2f-8b3e-4d4c-af20-3e4d5c6b7a81"
    turns = [{"type": "session_meta", "payload": {"id": native_id}}] + [
        {"type": "turn_context", "payload": {"model": model, "effort": "high"}}
        for model in ("gpt-a", "gpt-a", "gpt-b", "gpt-b", "gpt-a")
    ]
    path = folder / f"rollout-2024-12-01T00-00-00-{native_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in turns[:4]))
    ingest(store, root)
    with open(path, "a") as rest:
        rest.write("".join(json.dumps(r) + "\n" for r in turns[4:]))
    ingest(store, root, Writer())
    with store() as db:
        stored = session(db, native_id)
        changes = db.scalars(
            select(Event.payload)
            .where(Event.session_id == stored.id, Event.kind == EventKind.MODEL_CHANGE)
            .order_by(Event.sequence)
        ).all()
        assert [(c["previous_model"], c["model"]) for c in changes] == [
            ("gpt-a", "gpt-b"),
            ("gpt-b", "gpt-a"),
        ]
        # Each model once, the one it went back to last: that is the current one.
        assert stored.models == ["gpt-b", "gpt-a"]


# -- single records ------------------------------------------------------------------


def message(role: str, text: str, kind: str = "input_text", **record) -> dict:
    return {
        "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]},
        **record,
    }


def test_only_retained_user_input_is_a_prompt():
    injected = parse_record(message("user", "<environment_context>"))
    assert injected.events == [] and injected.facts.initial_task is None
    typed = parse_record(message("user", "fix it", metadata={"retained_source": "composer"}))
    assert typed.events[0].kind is EventKind.USER_PROMPT
    assert typed.facts.initial_task == "fix it"
    assert parse_record(message("developer", "be careful")).events == []


def test_assistant_text_keeps_its_phase():
    parsed = parse_record(
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "phase": "final_answer",
                "content": [{"type": "output_text", "text": "done"}],
            },
        }
    )
    assert parsed.events[0].payload == {"text": "done", "extra": {"phase": "final_answer"}}


@pytest.mark.parametrize(
    ("payload", "coverage", "has_text"),
    [
        ({"summary": [{"type": "summary_text", "text": "plan"}]}, ThoughtsCoverage.SUMMARY, True),
        ({"summary": [], "encrypted_content": "opaque"}, ThoughtsCoverage.ENCRYPTED, False),
        ({"summary": []}, ThoughtsCoverage.ABSENT, False),
    ],
)
def test_reasoning_coverage(payload, coverage, has_text):
    parsed = parse_record({"type": "response_item", "payload": {"type": "reasoning", **payload}})
    (thinking,) = parsed.events
    assert thinking.thoughts_coverage is coverage
    assert ("text" in thinking.payload) is has_text
    assert "opaque" not in json.dumps(thinking.payload)


def test_function_arguments_are_parsed_and_outputs_keep_their_call():
    call = parse_record(
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "shell",
                "call_id": "call_9",
                "arguments": '{"command": ["ls"]}',
            },
        }
    ).events[0]
    assert call.payload == {"name": "shell", "input": {"command": ["ls"]}, "call_id": "call_9"}
    result = parse_record(
        {
            "type": "response_item",
            "payload": {"type": "custom_tool_call_output", "call_id": "call_9", "output": "ok"},
        }
    ).events[0]
    assert result.kind is EventKind.TOOL_RESULT
    assert result.payload == {"output": "ok", "call_id": "call_9"}


def test_usage_is_the_last_turn_in_openai_terms():
    parsed = parse_record(
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "last_token_usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 60,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 5,
                    },
                    "total_token_usage": {"input_tokens": 999},
                },
            },
        }
    )
    (usage,) = parsed.events
    assert usage.payload == {
        "scope": "message",
        "input_tokens": 40,
        "cache_read_tokens": 60,
        "output_tokens": 20,
        "reasoning_tokens": 5,
    }
    empty = parse_record({"type": "event_msg", "payload": {"type": "token_count", "info": None}})
    assert empty.events == []


def test_a_copied_parent_header_says_nothing_about_the_subagent():
    copy = parse_record(
        {"type": "session_meta", "payload": {"id": "parent", "cwd": "/work/parent"}}, "child"
    )
    assert copy.events == [] and copy.facts.cwd is None


def test_known_metadata_is_not_unknown_and_new_types_are():
    for record in (
        {"type": "world_state", "payload": {}},
        {"type": "token_usage_record", "payload": {}},
        {"type": "event_msg", "payload": {"type": "item_completed"}},
    ):
        assert parse_record(record).unknown_type is None
    assert parse_record(
        {"type": "response_item", "payload": {"type": "hologram"}}
    ).unknown_type == ("response_item/hologram")


# -- goals ---------------------------------------------------------------------------


def snapshot_access(root: Path, tmp_path: Path) -> ReadOnlyAccess:
    (tmp_path / "snapshots").mkdir(exist_ok=True)
    return ReadOnlyAccess([root], snapshot_dir=tmp_path / "snapshots")


def test_goals_are_read_from_a_snapshot_and_put_on_their_sessions(store, tmp_path):
    with assert_untouched([FIXTURES]):
        goals = read_goals(snapshot_access(FIXTURES, tmp_path), FIXTURES)
    assert len(goals) == 2
    assert all(goal.status == "blocked" and goal.tokens_used > 0 for goal in goals)
    with store() as db:
        store_goals(db, goals)
        db.commit()
    with store() as db:
        for goal in goals:
            stored = session(db, goal.session_id).goal
            assert stored["objective"] == goal.objective
            assert stored["status"] == "blocked" and stored["token_budget"] is None
            assert stored["created_at"] <= stored["updated_at"]


def test_a_goal_objective_is_redacted_and_an_unknown_status_kept_apart(store, tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    key = "sk-proj-" + "Z" * 60
    with sqlite3.connect(root / "goals_1.sqlite") as db:
        db.execute(
            "CREATE TABLE thread_goals (thread_id TEXT, goal_id TEXT, objective TEXT,"
            " status TEXT, token_budget INTEGER, tokens_used INTEGER,"
            " time_used_seconds INTEGER, created_at_ms INTEGER, updated_at_ms INTEGER)"
        )
        db.execute(
            "INSERT INTO thread_goals VALUES (?, 'g', ?, 'dreaming', 5000, 10, 3, ?, ?)",
            (EXEC, f"deploy with {key}", 1735000000000, 1735000060000),
        )
    db.close()
    (goal,) = read_goals(snapshot_access(root, tmp_path), root)
    assert goal.status == "unknown" and goal.token_budget == 5000
    with store() as db:
        store_goals(db, [goal])
        db.commit()
    with store() as db:
        stored = session(db, EXEC).goal
    assert key not in stored["objective"] and "[REDACTED" in stored["objective"]


def test_no_goals_store_means_no_goals(tmp_path):
    root = tmp_path / "codex"
    root.mkdir()
    assert read_goals(snapshot_access(root, tmp_path), root) == []


def test_storing_the_same_goal_twice_changes_nothing(store):
    goal = Goal(EXEC, "ship it", "active", None, 1, 2, None, None)
    with store() as db:
        store_goals(db, [goal])
        db.commit()
        first = session(db, EXEC).updated_at
        store_goals(db, [goal])
        db.commit()
        assert session(db, EXEC).updated_at == first
