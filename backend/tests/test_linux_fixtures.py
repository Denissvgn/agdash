"""The adapters on the Linux fixtures: records made by the four harnesses on Ubuntu.

The fixtures come from an Ubuntu 24.04 container through the fixture kit. These tests
show that each adapter reads the Linux layout as it reads the macOS one, and record
how much thinking each harness wrote there.
"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.adapters import antigravity, claude_code, codex, pi
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session, SourceHealth
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.vocab import EventKind, ThoughtsCoverage

LINUX = Path(__file__).parent / "fixtures" / "harness_linux"


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    folder = tmp_path_factory.mktemp("linux")
    (folder / "snapshots").mkdir()
    url = f"sqlite:///{folder / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    for module, root in (
        (claude_code, LINUX / "claude"),
        (codex, LINUX / "codex"),
        (pi, LINUX / "pi" / "agent"),
        (antigravity, LINUX / "antigravity" / "cli"),
    ):
        access = ReadOnlyAccess([root], snapshot_dir=folder / "snapshots")
        Ingestor(access, SqlLedger(factory, module.Writer()), harness=module.HARNESS).scan(
            discover_files(access, [root], module.PATTERNS)
        )
    yield factory
    engine.dispose()


def _sessions(db, harness: str) -> int:
    return db.scalar(select(func.count()).select_from(Session).where(Session.harness == harness))


def _coverage(db, harness: str) -> set:
    return set(
        db.scalars(
            select(Event.thoughts_coverage)
            .join(Session, Session.id == Event.session_id)
            .where(Session.harness == harness, Event.kind == EventKind.THINKING)
        )
    )


@pytest.mark.parametrize("harness", ["claude_code", "codex", "pi", "antigravity"])
def test_every_harness_reads_from_the_linux_layout(store, harness):
    with store() as db:
        assert _sessions(db, harness) >= 1
        tools = db.scalar(
            select(func.count())
            .select_from(Event)
            .join(Session, Session.id == Event.session_id)
            .where(Session.harness == harness, Event.kind == EventKind.TOOL_CALL)
        )
        assert tools >= 1, "each session read a file"


def test_thinking_is_as_much_as_each_harness_wrote(store):
    with store() as db:
        # Claude Code keeps only a signature, as on macOS; Pi and Antigravity keep the text.
        assert _coverage(db, "claude_code") == {ThoughtsCoverage.ENCRYPTED}
        assert _coverage(db, "pi") == {ThoughtsCoverage.FULL}
        assert _coverage(db, "antigravity") == {ThoughtsCoverage.FULL}
        # Codex wrote no reasoning in these sessions.
        assert _coverage(db, "codex") == set()


def test_nothing_fails_to_parse(store):
    with store() as db:
        assert db.scalar(select(func.sum(SourceHealth.parse_error_count))) == 0
