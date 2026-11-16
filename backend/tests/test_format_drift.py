"""Format drift: unknown records and unreadable lines, counted per harness and version,
and reported by ``agdash doctor``. All transcripts here are the synthetic fixtures."""

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.adapters import claude_code, codex
from agdash.cli import format_checks
from agdash.db.migrate import upgrade
from agdash.db.models import Session, SourceHealth
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    yield url, sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def ingest(sessions, root: Path, module) -> None:
    access = ReadOnlyAccess([root], snapshot_dir=root.parent / "snapshots-elsewhere")
    Ingestor(access, SqlLedger(sessions, module.Writer()), harness=module.HARNESS).scan(
        discover_files(access, [root], module.PATTERNS)
    )


def claude_copy(tmp_path: Path) -> Path:
    root = tmp_path / "claude"
    source = sorted((FIXTURES / "claude" / "projects").glob("*/*.jsonl"))[0]
    target = root / "projects" / source.parent.name / source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    return target


def health(sessions) -> dict[tuple[str, str], tuple[int, int]]:
    with sessions() as db:
        return {
            (row.adapter, row.format_version): (row.unknown_record_count, row.parse_error_count)
            for row in db.scalars(select(SourceHealth))
        }


def test_an_unknown_record_type_raises_the_counter_and_a_doctor_warning(store, tmp_path):
    url, sessions = store
    transcript = claude_copy(tmp_path)
    native_id = transcript.stem
    with transcript.open("a") as file:
        file.write(
            json.dumps(
                {"type": "brand-new-record-kind", "version": "9.9.9", "sessionId": native_id}
            )
            + "\n"
        )
        file.write("{not json\n")
    ingest(sessions, tmp_path / "claude", claude_code)

    assert health(sessions)[("claude_code", "9.9.9")] == (1, 1)
    [check] = [c for c in format_checks(url, now=datetime.now(UTC)) if c.name.startswith("claude")]
    assert check.level == "warn"
    assert "1 unknown records and 1 unreadable lines in version 9.9.9" in check.detail
    assert "may have changed its format" in check.detail


def test_clean_fixtures_report_the_version_and_no_warning(store, tmp_path):
    url, sessions = store
    claude_copy(tmp_path)
    ingest(sessions, tmp_path / "claude", claude_code)
    [check] = format_checks(url, now=datetime.now(UTC))
    assert check.name == "claude_code formats" and check.level == "ok"
    assert check.detail.startswith("version ")
    assert "everything read recently was recognised" in check.detail


def test_a_read_after_the_header_counts_under_the_sessions_version(store, tmp_path):
    url, sessions = store
    root = tmp_path / "codex"
    source = sorted((FIXTURES / "codex").rglob("rollout-*.jsonl"))[0]
    target = root / "sessions" / "2026" / "11" / "17" / source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    ingest(sessions, root, codex)
    with sessions() as db:
        version = db.scalars(
            select(Session.harness_version).where(Session.harness == "codex")
        ).first()
    assert version

    # The next read holds only the new line, without the rollout's header.
    with target.open("a") as file:
        file.write(json.dumps({"type": "a-later-record-kind", "payload": {}}) + "\n")
    ingest(sessions, root, codex)
    counts = health(sessions)
    assert counts[("codex", version)][0] == 1
    assert ("codex", "unknown") not in counts


def test_old_trouble_is_history_not_a_warning(store):
    url, sessions = store
    with sessions() as db:
        db.add_all(
            [
                SourceHealth(
                    adapter="pi",
                    format_version="2",
                    unknown_record_count=4,
                    parse_error_count=0,
                    first_seen_at=NOW - timedelta(days=60),
                    last_seen_at=NOW - timedelta(days=40),
                ),
                SourceHealth(
                    adapter="pi",
                    format_version="3",
                    unknown_record_count=0,
                    parse_error_count=0,
                    first_seen_at=NOW - timedelta(days=30),
                    last_seen_at=NOW - timedelta(hours=2),
                ),
                SourceHealth(
                    adapter="antigravity",
                    format_version="unknown",
                    unknown_record_count=2,
                    parse_error_count=0,
                    first_seen_at=NOW - timedelta(days=1),
                    last_seen_at=NOW,
                ),
            ]
        )
        db.commit()
    checks = {c.name: c for c in format_checks(url, now=NOW)}
    pi = checks["pi formats"]
    assert pi.level == "ok"
    assert pi.detail == (
        "version 3, last seen 2026-11-17; everything read recently was recognised"
        " (4 records from older versions were not)"
    )
    agy = checks["antigravity formats"]
    assert agy.level == "warn" and "records with no version since 2026-11-16" in agy.detail
