"""The adapters on the Linux fixtures: records made by the four harnesses on Ubuntu.

The fixtures come from an Ubuntu 24.04 container through the fixture kit. Each adapter
must read them as it reads the macOS ones: every session in full, every event within
the contract, nothing left unknown, nothing changed on disk, and nothing new on a
second read. The checks that depend on what the macOS sessions happened to do stay in
each adapter's own tests.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from test_claude_code_adapter import source_counts, stored_counts

from agdash.access import ReadOnlyAccess
from agdash.access.guard import assert_untouched
from agdash.adapters import antigravity, claude_code, codex, pi
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session, SourceHealth
from agdash.events import validate_event
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.vocab import EventKind, Surface, ThoughtsCoverage

LINUX = Path(__file__).parent / "fixtures" / "harness_linux"
ADAPTERS = (
    (claude_code, LINUX / "claude"),
    (codex, LINUX / "codex"),
    (pi, LINUX / "pi" / "agent"),
    (antigravity, LINUX / "antigravity" / "cli"),
)
HARNESSES = [str(module.HARNESS) for module, _ in ADAPTERS]


def read_all(factory, snapshots: Path) -> int:
    """Ingest every Linux fixture with its own adapter, as the collector does, with
    Antigravity's conversation summaries. Returns the lines read."""
    lines = 0
    for module, root in ADAPTERS:
        access = ReadOnlyAccess([root], snapshot_dir=snapshots)
        report = Ingestor(access, SqlLedger(factory, module.Writer()), harness=module.HARNESS).scan(
            discover_files(access, [root], module.PATTERNS)
        )
        assert not report.refused, module.HARNESS
        lines += report.lines
        if module is antigravity:
            with factory() as db:
                antigravity.store_summaries(
                    db,
                    antigravity.read_summaries(access, root),
                    antigravity.SURFACES["antigravity-cli"],
                )
                db.commit()
    return lines


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    folder = tmp_path_factory.mktemp("linux")
    (folder / "snapshots").mkdir()
    url = f"sqlite:///{folder / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    with assert_untouched([LINUX]):
        assert read_all(factory, folder / "snapshots") > 0
    factory.snapshots = folder / "snapshots"
    yield factory
    engine.dispose()


def _sessions(db, harness: str) -> list[Session]:
    return db.scalars(select(Session).where(Session.harness == harness)).all()


def _coverage(db, harness: str) -> set:
    return set(
        db.scalars(
            select(Event.thoughts_coverage)
            .join(Session, Session.id == Event.session_id)
            .where(Session.harness == harness, Event.kind == EventKind.THINKING)
        )
    )


@pytest.mark.parametrize("harness", HARNESSES)
def test_every_session_is_read_with_its_task_model_and_times(store, harness):
    with store() as db:
        sessions = _sessions(db, harness)
        assert sessions
        for session in sessions:
            assert session.initial_task, session.native_id
            assert session.models, session.native_id
            assert session.cwd and session.cwd.startswith("/work/"), session.native_id
            assert session.started_at and session.last_activity_at >= session.started_at
            tools = db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.session_id == session.id, Event.kind == EventKind.TOOL_CALL)
            )
            assert tools >= 1, "each session read a file"


def test_claude_code_sessions_are_read_in_full(store):
    with store() as db:
        for path in (LINUX / "claude").glob("projects/*/*.jsonl"):
            session = db.scalars(select(Session).where(Session.native_id == path.stem)).one()
            assert session.surface is Surface.CLI
            assert stored_counts(db, session.id) == source_counts(path), path.name


@pytest.mark.parametrize("harness", HARNESSES)
def test_every_stored_event_follows_the_contract(store, harness):
    with store() as db:
        events = db.scalars(
            select(Event)
            .join(Session, Session.id == Event.session_id)
            .where(Session.harness == harness)
        ).all()
        assert events
        for stored in events:
            validate_event(stored.kind, stored.payload, stored.thoughts_coverage)
        assert {EventKind.USER_PROMPT, EventKind.TOOL_CALL, EventKind.TOOL_RESULT} <= {
            stored.kind for stored in events
        }


def test_thinking_is_as_much_as_each_harness_wrote(store):
    with store() as db:
        # Claude Code keeps only a signature, as on macOS; Pi and Antigravity keep the text.
        assert _coverage(db, "claude_code") == {ThoughtsCoverage.ENCRYPTED}
        assert _coverage(db, "pi") == {ThoughtsCoverage.FULL}
        assert _coverage(db, "antigravity") == {ThoughtsCoverage.FULL}
        # Codex wrote no reasoning in these sessions.
        assert _coverage(db, "codex") == set()


def test_every_record_is_understood(store):
    with store() as db:
        rows = db.execute(
            select(
                SourceHealth.adapter,
                SourceHealth.unknown_record_count,
                SourceHealth.parse_error_count,
            )
        ).all()
        assert rows, "source health is recorded"
        assert [
            (adapter, unknown, broken) for adapter, unknown, broken in rows if unknown or broken
        ] == []


def test_a_second_read_adds_nothing(store):
    with store() as db:
        before = db.scalar(select(func.count()).select_from(Event))
    assert read_all(store, store.snapshots) == 0
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Event)) == before
