"""The Antigravity adapter, against the synthetic fixtures and hand-made steps."""

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.access.guard import assert_untouched
from agdash.adapters import antigravity
from agdash.adapters.antigravity import (
    BODY_BASE,
    PATTERNS,
    SURFACES,
    Summary,
    Writer,
    harness_status,
    parse_step,
    read_summaries,
    split_body,
    store_summaries,
)
from agdash.collector.state import HarnessStatus
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session, SourceHealth
from agdash.events import validate_event
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.vocab import EventKind, ParentBasis, Surface, ThoughtsCoverage

FIXTURES = Path(__file__).parent / "fixtures" / "harness" / "antigravity"
APP, CLI = FIXTURES / "app", FIXTURES / "cli"
PARENT = "28bc75a6-6c9d-4428-944e-778292c5b3e3"
CHILD = "4106e2c9-953f-4984-93cf-81fa331fe35c"


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


def as_bases(root: Path) -> dict[Path, Surface]:
    """The fixture folders under the names Antigravity gives its bases."""
    return {root / "app": SURFACES["antigravity"], root / "cli": SURFACES["antigravity-cli"]}


def access_for(roots, tmp_path: Path) -> ReadOnlyAccess:
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir(exist_ok=True)
    return ReadOnlyAccess(list(roots), snapshot_dir=snapshots)


def ingest(store, tmp_path: Path, root: Path = FIXTURES, writer: Writer | None = None):
    bases = as_bases(root)
    access = access_for(bases, tmp_path)
    ingestor = Ingestor(access, SqlLedger(store, writer or Writer()), harness=antigravity.HARNESS)
    report = ingestor.scan(discover_files(access, list(bases), PATTERNS))
    with store() as db:
        for base, surface in bases.items():
            store_summaries(db, read_summaries(access, base), surface)
        db.commit()
    return report


def transcript(base: Path, cid: str) -> Path:
    return base / "brain" / cid / ".system_generated" / "logs" / "transcript_full.jsonl"


def steps(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def summary_ids(base: Path) -> list[str]:
    with sqlite3.connect(f"file:{base / 'conversation_summaries.db'}?mode=ro", uri=True) as db:
        ids = [row[0] for row in db.execute("SELECT conversation_id FROM conversation_summaries")]
    db.close()
    return ids


def the_session(db, native_id: str) -> Session:
    return db.scalars(
        select(Session).where(
            Session.harness == antigravity.HARNESS, Session.native_id == native_id
        )
    ).one()


def kinds(db, session: Session) -> dict[EventKind, int]:
    return dict(
        db.execute(
            select(Event.kind, func.count())
            .where(Event.session_id == session.id)
            .group_by(Event.kind)
        ).all()
    )


# -- the fixtures --------------------------------------------------------------------


def test_discovery_finds_the_transcripts_of_both_bases():
    access = ReadOnlyAccess([APP, CLI], snapshot_dir=FIXTURES.parent.parent)
    found = discover_files(access, [APP, CLI], PATTERNS)
    assert len([p for p in found if APP in p.parents]) == 4
    assert len([p for p in found if CLI in p.parents]) == 4
    assert not [p for p in found if "presence" in p.parts]


def test_conversations_have_title_workspace_status_parent_and_text(store, tmp_path):
    ingest(store, tmp_path)
    with store() as db:
        for base in (APP, CLI):
            for cid in summary_ids(base):
                stored = the_session(db, cid)
                assert stored.title, cid
                found = kinds(db, stored)
                assert found.get(EventKind.STATUS), cid
                assert found.get(EventKind.USER_PROMPT), cid
                assert found.get(EventKind.TOOL_CALL) or found.get(EventKind.ASSISTANT_TEXT), cid
                statuses = db.scalars(
                    select(Event.payload).where(
                        Event.session_id == stored.id,
                        Event.kind == EventKind.STATUS,
                        Event.sequence >= antigravity.STATUS_BASE,
                    )
                ).all()
                assert {s["status"] for s in statuses} == {"idle"}, cid
        assert the_session(db, PARENT).cwd.startswith("/work/")
        child, parent = the_session(db, CHILD), the_session(db, PARENT)
        assert child.parent_session_id == parent.id
        assert child.parent_basis is ParentBasis.HARNESS_RECORD


def test_surfaces_follow_the_base_and_the_source(store, tmp_path):
    ingest(store, tmp_path)
    with store() as db:
        assert {the_session(db, cid).surface for cid in summary_ids(APP)} == {Surface.DESKTOP}
        assert {the_session(db, cid).surface for cid in summary_ids(CLI)} == {Surface.CLI}


def test_transcripts_are_ingested_in_full(store, tmp_path):
    ingest(store, tmp_path)
    with store() as db:
        for base in (APP, CLI):
            for cid in summary_ids(base):
                source = steps(transcript(base, cid))
                found = kinds(db, the_session(db, cid))
                calls = sum(len(s.get("tool_calls") or []) for s in source)
                thoughts = sum(1 for s in source if s.get("thinking"))
                prompts = sum(1 for s in source if s["type"] == "USER_INPUT")
                usages = sum(1 for s in source if "input_tokens" in s)
                assert found.get(EventKind.TOOL_CALL, 0) == calls, cid
                assert found.get(EventKind.THINKING, 0) == thoughts, cid
                assert found.get(EventKind.USER_PROMPT, 0) == prompts, cid
                assert found.get(EventKind.USAGE, 0) == usages, cid


def test_tool_output_is_paired_with_its_call(store, tmp_path):
    ingest(store, tmp_path)
    with store() as db:
        results = db.execute(
            select(Event.session_id, Event.payload).where(Event.kind == EventKind.TOOL_RESULT)
        ).all()
        calls = {
            (session_id, p["call_id"]): p["name"]
            for session_id, p in db.execute(
                select(Event.session_id, Event.payload).where(Event.kind == EventKind.TOOL_CALL)
            )
        }
    paired = [(sid, r) for sid, r in results if "call_id" in r]
    assert len(paired) >= len(results) * 0.9
    assert all(calls[(sid, r["call_id"])] == r["name"] for sid, r in paired)


def test_the_cli_reply_is_assistant_text(store, tmp_path):
    ingest(store, tmp_path)
    with store() as db:
        replies = [
            p
            for p in db.scalars(select(Event.payload).where(Event.kind == EventKind.ASSISTANT_TEXT))
            if p.get("extra", {}).get("via") == "finish"
        ]
    assert len(replies) == 4


def test_models_and_effort_come_from_the_model_name(store, tmp_path):
    ingest(store, tmp_path)
    with store() as db:
        # Only the CLI writes the model into its transcript.
        for cid in summary_ids(CLI):
            stored = the_session(db, cid)
            assert stored.models and all(m.startswith("gemini-") for m in stored.models)
            assert stored.effort == stored.models[-1].rsplit("-", 1)[1]
        assert not the_session(db, PARENT).models


def test_every_stored_event_follows_the_contract_and_nothing_is_unknown(store, tmp_path):
    ingest(store, tmp_path)
    with store() as db:
        events = db.scalars(select(Event)).all()
        assert len(events) > 100
        for stored in events:
            validate_event(stored.kind, stored.payload, stored.thoughts_coverage)
        assert {e.thoughts_coverage for e in events if e.kind is EventKind.THINKING} == {
            ThoughtsCoverage.FULL
        }
        rows = db.scalars(select(SourceHealth)).all()
        assert sum(r.unknown_record_count + r.parse_error_count for r in rows) == 0


def test_rerunning_adds_nothing(store, tmp_path):
    ingest(store, tmp_path)
    with store() as db:
        before = db.scalar(select(func.count()).select_from(Event))
    assert ingest(store, tmp_path).lines == 0
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == before


def test_reading_leaves_the_fixtures_untouched(store, tmp_path):
    with assert_untouched([FIXTURES]):
        ingest(store, tmp_path)


# -- full-text bodies ----------------------------------------------------------------


def test_without_a_transcript_the_full_text_gives_the_turns_until_one_appears(store, tmp_path):
    root = tmp_path / "antigravity"
    shutil.copytree(FIXTURES, root)
    kept = tmp_path / "kept-transcript.jsonl"
    shutil.move(transcript(root / "app", PARENT), kept)
    ingest(store, tmp_path, root)
    with store() as db:
        stored = the_session(db, PARENT)
        texts = db.scalars(
            select(Event.payload).where(Event.session_id == stored.id, Event.sequence >= BODY_BASE)
        ).all()
        assert texts and all(t["extra"]["source"] == "full_text" for t in texts)
        assert stored.initial_task

    shutil.move(kept, transcript(root / "app", PARENT))
    ingest(store, tmp_path, root, Writer())
    with store() as db:
        stored = the_session(db, PARENT)
        assert not db.scalars(
            select(Event.id).where(Event.session_id == stored.id, Event.sequence >= BODY_BASE)
        ).all(), "the transcript replaced the full-text turns"
        assert kinds(db, stored).get(EventKind.TOOL_CALL)


def test_bodies_are_cut_at_their_step_offsets():
    body = "first ask\nsecond ask é\n"
    offsets = json.dumps([[0, 0], [7, len(b"first ask\n")]]).encode()
    turns = split_body(body, offsets, "user")
    assert [(t.step, t.text) for t in turns] == [(0, "first ask"), (7, "second ask é")]
    assert split_body(body, b"not json", "user") == []
    assert split_body("", offsets, "user") == []
    assert [t.step for t in split_body(body, "[[3, 0], [4, 999]]", "agent")] == [3]


# -- liveness ------------------------------------------------------------------------


def summary(**changes) -> Summary:
    values = dict(
        conversation_id="c",
        title="",
        preview="",
        workspace=None,
        status="CASCADE_RUN_STATUS_IDLE",
        step_count=1,
        last_modified=None,
        parent_id=None,
        working=False,
        killed=False,
        archived=False,
        agent_name=None,
    )
    return Summary(**{**values, **changes})


def test_the_harness_status_comes_from_the_summary():
    assert harness_status(summary()) is HarnessStatus.IDLE
    assert harness_status(summary(working=True)) is HarnessStatus.BUSY
    assert harness_status(summary(status="CASCADE_RUN_STATUS_RUNNING")) is HarnessStatus.BUSY
    assert harness_status(summary(status="CASCADE_RUN_STATUS_WAITING_FOR_USER")) is (
        HarnessStatus.WAITING
    )
    assert harness_status(summary(status="")) is None


class Recording(ReadOnlyAccess):
    """Read-only access that remembers every file it was asked to open or list."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.touched: list[str] = []

    def open(self, path, *args, **kwargs):
        self.touched.append(str(path))
        return super().open(path, *args, **kwargs)

    def list_dir(self, path):
        self.touched.append(str(path))
        return super().list_dir(path)


def test_stale_presence_locks_are_not_liveness(store, tmp_path):
    root = tmp_path / "antigravity"
    shutil.copytree(FIXTURES, root)
    presence = root / "app" / "presence"
    presence.mkdir()
    for cid in summary_ids(root / "app"):
        (presence / f"{cid}.lock").write_text("")  # fresh, as a running conversation leaves it
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    access = Recording([root / "app"], snapshot_dir=snapshots)
    summaries = read_summaries(access, root / "app")
    files = discover_files(access, [root / "app"], PATTERNS)
    Ingestor(access, SqlLedger(store, Writer()), harness=antigravity.HARNESS).scan(files)
    assert {harness_status(s) for s in summaries} == {HarnessStatus.IDLE}
    assert not [path for path in access.touched if "presence" in Path(path).parts]


def test_every_database_read_goes_through_the_snapshot_reader(store, tmp_path, monkeypatch):
    opened: list[str] = []
    connect = sqlite3.connect

    def spy(database, *args, **kwargs):
        opened.append(os.fspath(database))
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", spy)
    ingest(store, tmp_path)
    harness_files = [path for path in opened if str(FIXTURES) in path]
    assert not harness_files, "the summary database is only opened as a private copy"
    assert any(str(tmp_path / "snapshots") in path for path in opened)


# -- single steps --------------------------------------------------------------------


def test_a_planner_step_gives_thinking_calls_usage_and_effort():
    parsed = parse_step(
        {
            "step_index": 4,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2024-12-01T00:00:00Z",
            "model": "gemini-4-argon-high",
            "thinking": "look first",
            "tool_calls": [
                {"name": "view_file", "args": {"AbsolutePath": "/work/a"}},
                {"name": "finish", "args": {"summary": "all done"}},
            ],
            "input_tokens": 10,
            "cache_read_tokens": 90,
            "output_tokens": 5,
        }
    )
    assert parsed.facts.model == "gemini-4-argon-high" and parsed.facts.effort == "high"
    kinds_seen = [e.kind for e in parsed.events]
    assert kinds_seen == [
        EventKind.THINKING,
        EventKind.TOOL_CALL,
        EventKind.TOOL_CALL,
        EventKind.ASSISTANT_TEXT,
        EventKind.USAGE,
    ]
    assert [e.payload["call_id"] for e in parsed.events[1:3]] == ["4.0", "4.1"]
    assert parsed.events[3].payload == {"text": "all done", "extra": {"via": "finish"}}
    assert parsed.events[4].payload == {
        "scope": "message",
        "input_tokens": 10,
        "cache_read_tokens": 90,
        "output_tokens": 5,
        "model": "gemini-4-argon-high",
    }


def test_tool_output_marks_failures_and_running_steps():
    (failed,) = parse_step(
        {"type": "RUN_COMMAND", "source": "MODEL", "status": "DONE", "exit_code": 2, "content": "x"}
    ).events
    assert failed.payload["is_error"] is True and failed.payload["extra"]["exit_code"] == 2
    generic = parse_step(
        {"type": "GENERIC", "source": "MODEL", "status": "RUNNING", "content": "partial"}
    ).events
    assert generic[0].kind is EventKind.TOOL_RESULT and generic[0].payload["extra"]["running"]
    errored = parse_step(
        {"type": "GENERIC", "source": "MODEL", "status": "ERROR", "content": "", "error": "boom"}
    ).events
    assert [e.kind for e in errored] == [EventKind.TOOL_RESULT, EventKind.STATUS]


def test_known_metadata_is_not_unknown_and_new_step_types_are_kept_as_tool_output():
    assert parse_step({"type": "SYSTEM_MESSAGE", "content": "rules"}).unknown_type is None
    assert parse_step({"type": "CONVERSATION_HISTORY"}).events == []
    new = parse_step({"type": "TAKE_SCREENSHOT", "source": "MODEL", "content": "ok"})
    assert new.unknown_type == "TAKE_SCREENSHOT"
    assert new.events[0].payload["name"] == "take_screenshot"


def test_secrets_in_steps_are_redacted_before_storage(store, tmp_path):
    root = tmp_path / "antigravity"
    key = "sk-ant-api03-" + "Z" * 60
    path = transcript(root / "cli", "0b5f6c1e-7a2d-4c3b-9e1f-2d3c4b5a6f70")
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"type": "USER_INPUT", "step_index": 0, "content": f"use {key}"})
        + "\n"
        + json.dumps(
            {
                "type": "PLANNER_RESPONSE",
                "step_index": 1,
                "tool_calls": [{"name": "run_command", "args": {"CommandLine": f"export K={key}"}}],
            }
        )
        + "\n"
    )
    (root / "app").mkdir()
    ingest(store, tmp_path, root)
    with store() as db:
        dumped = json.dumps([e.payload for e in db.scalars(select(Event))])
        assert key not in dumped and "[REDACTED" in dumped
