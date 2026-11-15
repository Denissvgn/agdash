"""The raw archive, retention and export."""

import json
import os
import random
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agdash import export as export_module
from agdash import retention as retention_module
from agdash.access import ReadOnlyAccess
from agdash.adapters import claude_code, codex, pi
from agdash.archive import Archiving, raw_lines
from agdash.db.migrate import upgrade
from agdash.db.models import Event, RawChunk, Session, SessionProcess
from agdash.export import ExportRefused, render, write_export
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.main import create_app
from agdash.redaction import redact_text
from agdash.retention import apply_retention
from agdash.vocab import EventKind, SessionState

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
TOKEN = "export-test-token"


def ingest(store, module, root: Path, *, archive: bool = True):
    access = ReadOnlyAccess([root], snapshot_dir=root.parent / "snapshots-elsewhere")
    writer = Archiving(module.Writer(), module.HARNESS, enabled=archive)
    return Ingestor(access, SqlLedger(store, writer), harness=module.HARNESS).scan(
        discover_files(access, [root], module.PATTERNS)
    )


@pytest.fixture
def url(tmp_path) -> str:
    database = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(database)
    return database


@pytest.fixture
def store(url):
    engine = create_engine(url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def filled(store):
    for module, folder in ((claude_code, "claude"), (codex, "codex"), (pi, "pi/agent")):
        ingest(store, module, FIXTURES / folder)
    return store


def pi_session(db) -> Session:
    return db.scalars(select(Session).where(Session.harness == "pi")).one()


# -- the raw archive ----------------------------------------------------------------


def test_the_archive_keeps_every_line_redacted_and_compressed(filled):
    (source,) = (FIXTURES / "pi" / "agent").glob("sessions/*/*.jsonl")
    with filled() as db:
        lines = list(raw_lines(db, pi_session(db).id))
        stored = db.scalar(
            select(func.sum(func.length(RawChunk.data))).where(RawChunk.harness == "pi")
        )
    original = source.read_text().splitlines()
    assert lines == [redact_text(line) for line in original]
    assert stored < len(source.read_bytes()), "compressed"


def test_reading_again_archives_nothing_twice(filled):
    with filled() as db:
        before = db.scalar(select(func.count()).select_from(RawChunk))
    ingest(filled, pi, FIXTURES / "pi" / "agent")
    with filled() as db:
        assert db.scalar(select(func.count()).select_from(RawChunk)) == before


def test_secrets_in_raw_lines_are_redacted(store, tmp_path):
    key = "sk-ant-api03-" + "Z" * 60
    folder = tmp_path / "pi" / "agent" / "sessions" / "--work--"
    folder.mkdir(parents=True)
    (folder / "2024-12-01T00-00-00-000Z_1c6a7d2f-8b3e-4d4c-af20-3e4d5c6b7a81.jsonl").write_text(
        json.dumps(
            {
                "type": "message",
                "message": {"role": "user", "content": [{"type": "text", "text": f"use {key}"}]},
            }
        )
        + "\n"
    )
    ingest(store, pi, tmp_path / "pi" / "agent")
    with store() as db:
        (line,) = raw_lines(db, pi_session(db).id)
    assert key not in line and "[REDACTED" in line


def test_the_archive_can_be_turned_off(store):
    ingest(store, pi, FIXTURES / "pi" / "agent", archive=False)
    with store() as db:
        assert db.scalar(select(func.count()).select_from(RawChunk)) == 0
        assert db.scalar(select(func.count()).select_from(Event)) > 0


# -- retention ----------------------------------------------------------------------


def add(
    db,
    name: str,
    *,
    days_ago: float,
    state=SessionState.UNKNOWN,
    parent=None,
    process_days_ago: float | None = None,
) -> Session:
    moment = NOW - timedelta(days=days_ago)
    session = Session(
        harness="pi",
        native_id=name,
        state=state,
        last_activity_at=moment,
        started_at=moment,
        parent_session_id=parent.id if parent else None,
    )
    db.add(session)
    db.flush()
    db.add(
        Event(
            session_id=session.id,
            sequence=0,
            kind=EventKind.STATUS,
            payload={"status": "x"},
            source_file=f"/files/{name}.jsonl",
        )
    )
    if process_days_ago is not None:
        seen = NOW - timedelta(days=process_days_ago)
        db.add(
            SessionProcess(
                session_id=session.id,
                pid=100 + session.id,
                process_started_at=seen,
                first_seen_at=seen,
                last_seen_at=seen,
            )
        )
    db.flush()
    return session


def names(db) -> set[str]:
    return set(db.scalars(select(Session.native_id)))


def test_old_finished_sessions_go_and_take_their_data_with_them(store):
    with store() as db:
        add(db, "old", days_ago=40, state=SessionState.ENDED)
        add(db, "unknown", days_ago=40)
        add(db, "recent", days_ago=5, state=SessionState.ENDED)
        db.add(
            RawChunk(
                harness="pi",
                source_file="/files/old.jsonl",
                first_offset=0,
                end_offset=1,
                line_count=1,
                data=b"x",
            )
        )
        db.commit()
        outcome = apply_retention(db, 30, now=NOW)
        db.commit()
        assert names(db) == {"recent"}
        assert (outcome.sessions, outcome.events, outcome.raw_chunks) == (2, 2, 1)
        assert db.scalar(select(func.count()).select_from(Event)) == 1


@pytest.mark.parametrize("state", [SessionState.ACTIVE, SessionState.WAITING, SessionState.IDLE])
def test_a_session_that_is_still_running_is_never_removed(store, state):
    with store() as db:
        add(db, "running", days_ago=400, state=state)
        db.commit()
        assert apply_retention(db, 1, now=NOW).sessions == 0
        assert names(db) == {"running"}


def test_a_recently_seen_process_keeps_an_old_session(store):
    with store() as db:
        add(db, "quiet", days_ago=90, process_days_ago=1)
        db.commit()
        outcome = apply_retention(db, 30, now=NOW)
        assert outcome.sessions == 0 and outcome.kept_because_alive == 1


def test_families_are_kept_or_removed_together(store):
    with store() as db:
        parent = add(db, "parent", days_ago=60, state=SessionState.ENDED)
        add(db, "child", days_ago=2, state=SessionState.ENDED, parent=parent)
        lone = add(db, "lone-parent", days_ago=60, state=SessionState.ENDED)
        add(db, "lone-child", days_ago=60, parent=lone)
        db.commit()
        apply_retention(db, 30, now=NOW)
        db.commit()
        assert names(db) == {"parent", "child"}


def test_a_dry_run_changes_nothing(store):
    with store() as db:
        add(db, "old", days_ago=40, state=SessionState.ENDED)
        db.commit()
        outcome = apply_retention(db, 30, now=NOW, dry_run=True)
        assert outcome.sessions == 1 and names(db) == {"old"}
    with pytest.raises(ValueError):
        with store() as db:
            apply_retention(db, 0, now=NOW)


def test_retention_never_removes_a_running_session_whatever_the_mix(store):
    rng = random.Random(20261116)
    alive = {SessionState.ACTIVE, SessionState.WAITING, SessionState.IDLE}
    with store() as db:
        made = []
        for n in range(300):
            parent = rng.choice(made) if made and rng.random() < 0.3 else None
            made.append(
                add(
                    db,
                    f"s{n}",
                    days_ago=rng.uniform(0, 200),
                    state=rng.choice(list(SessionState)),
                    parent=parent,
                    process_days_ago=rng.choice([None, rng.uniform(0, 100)]),
                )
            )
        db.commit()
        running = {s.native_id for s in made if s.state in alive}
        for days in (1, 7, 30, 90):
            apply_retention(db, days, now=NOW)
            db.commit()
            assert running <= names(db), days


def test_the_retention_command_is_off_unless_asked(url, capsys, monkeypatch):
    from agdash.config import get_settings

    monkeypatch.setenv("AGDASH_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        assert retention_module.main([]) == 0
        assert "off" in capsys.readouterr().out
        assert retention_module.main(["--days", "30"]) == 0
        assert "Would remove 0 sessions" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


# -- export -------------------------------------------------------------------------


def test_jsonl_export_holds_the_session_and_every_event_in_order(filled):
    with filled() as db:
        session = pi_session(db)
        count = db.scalar(
            select(func.count()).select_from(Event).where(Event.session_id == session.id)
        )
        lines = [json.loads(line) for line in "".join(render(db, session, "jsonl")).splitlines()]
    assert lines[0]["type"] == "session" and lines[0]["harness"] == "pi"
    events = lines[1:]
    assert len(events) == count
    sequences = [e["sequence"] for e in events]
    assert sequences == sorted(sequences)


def test_markdown_export_reads_as_a_transcript(filled):
    with filled() as db:
        text = "".join(render(db, pi_session(db), "markdown"))
    assert text.startswith("# ") and "## Prompt" in text and "**Tool call** `" in text
    assert "<details><summary>Thinking</summary>" in text and "_Tokens:" in text


def test_markdown_fences_survive_backticks_and_long_output():
    from agdash.export import _fence

    assert _fence("a ```` b").startswith("`````\n")
    assert export_module._cut("x" * 5000).endswith("(1000 more characters)")


def test_raw_export_is_the_archived_lines(filled):
    with filled() as db:
        session = pi_session(db)
        assert "".join(render(db, session, "raw")).splitlines() == list(raw_lines(db, session.id))


def test_an_export_goes_only_to_the_new_file_the_person_names(filled, tmp_path):
    target_dir = tmp_path / "chosen"
    target_dir.mkdir()
    before = set(os.listdir(tmp_path))
    with filled() as db:
        written = write_export(render(db, pi_session(db), "markdown"), target_dir / "s.md", [])
    assert written > 0 and os.listdir(target_dir) == ["s.md"]
    assert set(os.listdir(tmp_path)) == before, "nothing else was written"
    assert stat.S_IMODE((target_dir / "s.md").stat().st_mode) == 0o600


def test_an_export_refuses_to_overwrite_to_go_inside_a_harness_or_to_a_missing_folder(
    filled, tmp_path
):
    harness = tmp_path / ".codex"
    harness.mkdir()
    existing = tmp_path / "existing.md"
    existing.write_text("keep me")
    with filled() as db:
        session = pi_session(db)
        for target, reason in (
            (existing, "exists"),
            (harness / "s.md", "harness"),
            (tmp_path / "missing" / "s.md", "not a folder"),
        ):
            with pytest.raises(ExportRefused, match=reason):
                write_export(render(db, session, "jsonl"), target, [harness])
    assert existing.read_text() == "keep me" and list(harness.iterdir()) == []


def test_the_export_command_writes_where_it_is_told(filled, url, tmp_path, monkeypatch, capsys):
    from agdash.config import get_settings

    monkeypatch.setenv("AGDASH_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        with filled() as db:
            session_id = pi_session(db).id
        out = tmp_path / "export.jsonl"
        assert export_module.main([str(session_id), "--format", "jsonl", "--out", str(out)]) == 0
        assert out.read_text().startswith('{"type": "session"')
        assert export_module.main([str(session_id), "--format", "jsonl", "--out", str(out)]) == 1
        assert export_module.main(["999999", "--out", str(tmp_path / "x.md")]) == 1
        assert not (tmp_path / "x.md").exists()
    finally:
        get_settings.cache_clear()


def test_the_api_offers_the_export_as_a_download_and_writes_nothing(filled, url, tmp_path):
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    with filled() as db:
        session = pi_session(db)
        expected = "".join(render(db, session, "markdown"))
        session_id = session.id
    with TestClient(
        create_app(url, token=TOKEN, port=8765),
        base_url="http://127.0.0.1:8765",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        response = client.get(f"/api/sessions/{session_id}/export", params={"format": "markdown"})
        assert client.get("/api/sessions/999999/export").status_code == 404
        assert client.get(f"/api/sessions/{session_id}/export?format=pdf").status_code == 422
    assert response.status_code == 200 and response.text == expected
    assert response.headers["content-disposition"] == (
        f'attachment; filename="agdash-session-{session_id}.md"'
    )
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
