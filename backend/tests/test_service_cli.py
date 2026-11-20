"""The collector loop, the service runtime and the ``agdash`` command.

The home directory is a temporary one holding the synthetic fixtures where each
harness keeps its files. No test touches the real service manager, keyring or
harness data; the service-manager tests record the commands instead.
"""

import json
import os
import plistlib
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agdash import cli
from agdash.access.guard import assert_untouched
from agdash.collector.run import Collector
from agdash.db.migrate import upgrade
from agdash.db.models import RawChunk, Session, SessionProcess, SessionStateChange
from agdash.platform import Host, OperatingSystem
from agdash.platform.probes import ProcessGone, ProcessIdentity, ProcessRow
from agdash.platform.service import LAUNCHD_LABEL, SYSTEMD_UNIT, ServiceState
from agdash.vocab import SessionState

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
CLAUDE_SESSION = "53c374fd-8306-45c0-afe8-3d92d3c5b049"
CODEX_SESSION = "76f11fad-ff5f-4d44-bb76-832c79d3037f"
NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)


def make_home(tmp_path: Path) -> Path:
    """A home directory with each harness's synthetic files where the harness keeps them."""
    home = tmp_path / "home"
    shutil.copytree(FIXTURES / "claude", home / ".claude")
    shutil.copytree(FIXTURES / "codex", home / ".codex")
    shutil.copytree(FIXTURES / "pi", home / ".pi")
    shutil.copytree(FIXTURES / "antigravity" / "app", home / ".gemini" / "antigravity")
    shutil.copytree(FIXTURES / "antigravity" / "cli", home / ".gemini" / "antigravity-cli")
    return home


class FakeProbe:
    """A process table the test controls."""

    def __init__(self) -> None:
        self.rows: list[ProcessRow] = []
        self.files: dict[int, list[str]] = {}

    def table(self) -> list[ProcessRow]:
        return list(self.rows)

    def cwd(self, identity: ProcessIdentity) -> str | None:
        if identity.pid not in {row.identity.pid for row in self.rows}:
            raise ProcessGone(identity.pid)
        return "/work/example"

    def open_files(self, identity: ProcessIdentity) -> list[str] | None:
        return self.files.get(identity.pid, [])


def process(pid: int, name: str, argv: tuple[str, ...]) -> ProcessRow:
    return ProcessRow(
        identity=ProcessIdentity(pid, NOW - timedelta(minutes=5)),
        ppid=1,
        name=name,
        exe=f"/usr/local/bin/{name}",
        argv=argv,
    )


@pytest.fixture
def setup(tmp_path):
    home = make_home(tmp_path)
    host = Host(OperatingSystem.LINUX, home, {})
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    (tmp_path / "snapshots").mkdir()
    probe = FakeProbe()
    clock = {"now": NOW}
    collector = Collector(
        sessions, host, snapshot_dir=tmp_path / "snapshots", probe=probe, clock=lambda: clock["now"]
    )
    yield collector, sessions, probe, home, clock
    engine.dispose()


def session(db, harness: str, native: str) -> Session:
    return db.scalars(
        select(Session).where(Session.harness == harness, Session.native_id == native)
    ).one()


# -- the collector ------------------------------------------------------------------


def test_backfill_reads_every_harness_and_archives_raw_lines(setup):
    collector, sessions, *_ = setup
    totals = collector.backfill()
    assert totals.lines > 0 and totals.refused == 0
    assert set(totals.by_harness) == {"claude_code", "codex", "pi", "antigravity"}
    assert totals.summaries > 0
    with sessions() as db:
        harnesses = set(db.scalars(select(Session.harness).distinct()))
        assert harnesses == {"claude_code", "codex", "pi", "antigravity"}
        assert db.scalar(select(func.count()).select_from(RawChunk)) > 0
    assert collector.backfill().lines == 0, "a second pass reads nothing new"


def test_only_where_session_files_can_be_is_watched(setup):
    collector, _, _, home, _ = setup
    # What a harness keeps beside its sessions is neither listed nor watched.
    (home / ".codex/worktrees/checkout").mkdir(parents=True)
    (home / ".pi/agent/install/node_modules").mkdir(parents=True)
    real = home.resolve()
    assert collector.watched() == sorted(
        [
            real / ".claude/projects",
            real / ".codex/archived_sessions",
            real / ".codex/session_index.jsonl",
            real / ".codex/sessions",
            # Antigravity keeps its summaries beside its transcripts, so each is watched whole.
            real / ".gemini/antigravity",
            real / ".gemini/antigravity-cli",
            real / ".pi/agent/sessions",
        ]
    )
    assert {path for path in collector.discover() if "worktrees" in path.parts} == set()


def test_a_changed_file_is_matched_to_its_harness_without_listing(setup, monkeypatch):
    collector, _, _, home, _ = setup
    found = collector.discover()
    rollout = next(home.glob(f".codex/sessions/*/*/*/rollout-*-{CODEX_SESSION}.jsonl")).resolve()
    transcript = next(home.glob(f".claude/projects/*/{CLAUDE_SESSION}.jsonl")).resolve()
    monkeypatch.setattr(
        type(collector._access), "walk", lambda *_: pytest.fail("a folder was listed")
    )
    changed = [rollout, transcript, home / ".codex/goals_1.sqlite", home / ".codex/config.toml"]
    assert sorted(collector.matching(changed)) == sorted([rollout, transcript])
    assert sorted(collector.matching(found)) == sorted(found)


def test_what_runs_now_is_read_first_then_the_newest(setup):
    collector, _, probe, home, _ = setup
    found = collector.discover()
    # Every file a day apart, oldest first in name order: read in that order, the live
    # sessions would wait for all the history before them.
    for age, path in enumerate(sorted(found, reverse=True)):
        when = (NOW - timedelta(days=age)).timestamp()
        os.utime(path, (when, when))
    rollout = next(home.glob(f".codex/sessions/*/*/*/rollout-*-{CODEX_SESSION}.jsonl")).resolve()
    transcript = next(home.glob(f".claude/projects/*/{CLAUDE_SESSION}.jsonl")).resolve()
    subagents = sorted(transcript.with_suffix("").glob("subagents/*.jsonl"))
    (home / ".claude/sessions/5151.json").write_text(
        json.dumps({"pid": 5151, "sessionId": CLAUDE_SESSION, "status": "busy"})
    )
    probe.rows = [
        process(4242, "codex", ("codex", "exec", "do it")),
        process(5151, "claude", ("claude",)),
    ]
    probe.files = {4242: [str(rollout)]}

    ordered = collector.urgent_first(found)
    running = {rollout, transcript, *subagents}
    assert set(ordered[: len(running)]) == running, "sessions with a process, and their subagents"
    sizes = [path.stat().st_size for path in ordered[: len(running)]]
    assert sizes == sorted(sizes), "the smallest first, so one long transcript holds none back"
    rest = ordered[len(running) :]
    assert sorted(rest) == sorted(set(found) - running)
    written = [path.stat().st_mtime for path in rest]
    assert written == sorted(written, reverse=True), "then history, newest first"


def test_reading_goes_newest_first_when_the_processes_cannot_be_seen(setup):
    collector, _, probe, _, _ = setup
    found = collector.discover()
    for age, path in enumerate(found):
        when = (NOW - timedelta(days=age)).timestamp()
        os.utime(path, (when, when))
    probe.table = lambda: 1 / 0
    assert collector.urgent_first(list(reversed(found))) == found


def test_the_start_up_read_reports_how_far_it_is_and_when_it_is_done(setup):
    collector, sessions, *_ = setup
    assert collector.reading() is None, "nothing is known before the loops run"
    total = len(collector.discover())
    stop = threading.Event()
    seen = []
    threads = collector.run(stop, poll_seconds=30.0)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            reading = collector.reading()
            seen.append(reading)
            if reading.finished:
                break
            time.sleep(0.005)
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=20)
    assert seen[0].finished is False
    assert seen[-1] == type(seen[-1])(done=total, total=total, finished=True)
    assert [reading.done for reading in seen] == sorted(reading.done for reading in seen)
    with sessions() as db:
        harnesses = set(db.scalars(select(Session.harness).distinct()))
    assert harnesses == {"claude_code", "codex", "pi", "antigravity"}


def test_a_conversation_that_can_no_longer_change_is_not_looked_at_on_every_pass(setup):
    from agdash.collector.state import HarnessStatus

    collector, sessions, _, _, _ = setup
    collector.backfill()
    with sessions() as db:
        kept = db.scalars(
            select(Session).where(Session.harness == "antigravity").order_by(Session.id)
        ).all()
        assert len(kept) >= 4
        old, recent, archived, ended = kept[:4]
        for session in kept:
            session.state = SessionState.UNKNOWN
            session.last_activity_at = NOW - timedelta(days=30)
        ended.state = SessionState.ENDED
        db.commit()
        # What Antigravity's own summaries say of each: its status, when, and whether archived.
        collector._harness_status = {
            old.native_id: (HarnessStatus.IDLE, NOW - timedelta(days=3), False),
            recent.native_id: (HarnessStatus.BUSY, NOW - timedelta(minutes=5), False),
            archived.native_id: (HarnessStatus.IDLE, NOW - timedelta(days=3), True),
            ended.native_id: (HarnessStatus.IDLE, NOW - timedelta(days=3), True),
        }
        looked_at = {session.id for session in collector._candidates(db, NOW, set())}
        # The one that changed lately, and the one archived but not yet marked ended.
        assert looked_at & {s.id for s in kept} == {recent.id, archived.id}
    collector.probe()
    with sessions() as db:
        assert db.get(Session, archived.id).state is SessionState.ENDED
        # Once marked ended it is left alone too.
        assert not {s.id for s in collector._candidates(db, NOW, set())} & {archived.id, old.id}


def test_a_pass_finds_the_sessions_to_look_at_without_reading_all_of_them(setup):
    from sqlalchemy import event

    collector, sessions, _, _, _ = setup
    collector.backfill()
    asked: list[tuple[str, tuple]] = []
    with sessions() as db:
        engine = db.get_bind()

        def note(_conn, _cursor, statement, parameters, _context, _many):
            asked.append((statement, parameters))

        event.listen(engine, "before_cursor_execute", note)
        try:
            some = db.scalars(select(Session.id).limit(2)).all()
            asked.clear()
            collector._harness_status = {"synthetic-conversation": (None, NOW, False)}
            collector._candidates(db, NOW, set(some))
        finally:
            event.remove(engine, "before_cursor_execute", note)
        assert len(asked) == 4
        for statement, parameters in asked:
            plan = db.connection().exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters)
            steps = [row[3] for row in plan]
            # Each question is answered from an index: none goes through every session.
            assert steps and all(step.startswith("SEARCH sessions") for step in steps), steps


def test_antigravity_summaries_are_stored_again_only_when_their_file_changed(setup, monkeypatch):
    from agdash.adapters import antigravity

    collector, _, _, home, _ = setup
    reads: list[str] = []
    read = antigravity.read_summaries

    def counted(access, base):
        reads.append(base.name)
        return read(access, base)

    monkeypatch.setattr(antigravity, "read_summaries", counted)
    assert collector.refresh_summaries() > 0
    assert sorted(reads) == ["antigravity", "antigravity-cli"]
    statuses = dict(collector._harness_status)
    assert statuses

    # Nothing changed: nothing is read or stored, and what was learnt is kept.
    reads.clear()
    assert collector.refresh_summaries() == 0
    assert reads == [] and collector._harness_status == statuses

    # One base's store is written to: that one is read again, the other is not.
    store = home / ".gemini" / "antigravity-cli" / antigravity.SUMMARIES
    state = store.stat()
    os.utime(store, ns=(state.st_atime_ns, state.st_mtime_ns + 1_000_000_000))
    assert collector.refresh_summaries() > 0
    assert reads == ["antigravity-cli"]

    # A backfill reads everything, whatever was seen before.
    reads.clear()
    collector.backfill()
    assert sorted(reads) == ["antigravity", "antigravity-cli"]


def test_a_running_codex_process_makes_its_session_live_and_its_exit_ends_it(setup):
    collector, sessions, probe, home, clock = setup
    collector.backfill()
    rollout = next(home.glob(f".codex/sessions/*/*/*/rollout-*-{CODEX_SESSION}.jsonl"))
    probe.rows = [process(4242, "codex", ("codex", "exec", "do it"))]
    probe.files = {4242: [str(rollout)]}
    assert collector.probe() >= 1
    with sessions() as db:
        live = session(db, "codex", CODEX_SESSION)
        assert live.state in (SessionState.ACTIVE, SessionState.IDLE, SessionState.WAITING)
        assert live.state_evidence["observed"]["process"] == "running"
        (row,) = db.scalars(select(SessionProcess).where(SessionProcess.session_id == live.id))
        assert row.pid == 4242

    probe.rows, probe.files = [], {}
    clock["now"] = NOW + timedelta(seconds=2)
    collector.probe()
    with sessions() as db:
        ended = session(db, "codex", CODEX_SESSION)
        assert ended.state is SessionState.ENDED
        assert ended.state_evidence["basis"] == "process_exited"
        assert ended.ended_at == NOW + timedelta(seconds=2)


def test_a_codex_daemon_session_runs_while_its_terminal_ui_does(setup):
    collector, sessions, probe, home, clock = setup
    collector.backfill()
    rollout = next(home.glob(f".codex/sessions/*/*/*/rollout-*-{CODEX_SESSION}.jsonl"))
    daemon = ("codex", "app-server", "--listen", "unix://", "--managed-daemon")
    # The daemon holds the rollout; the terminal UI holds none.
    probe.rows = [process(7000, "codex", daemon), process(7100, "codex", ("codex",))]
    probe.files = {7000: [str(rollout)]}
    assert collector.probe() >= 1
    with sessions() as db:
        live = session(db, "codex", CODEX_SESSION)
        assert live.state in (SessionState.ACTIVE, SessionState.IDLE, SessionState.WAITING)
        pids = set(
            db.scalars(select(SessionProcess.pid).where(SessionProcess.session_id == live.id))
        )
        assert pids == {7100}, "the session is the terminal UI's"

    # The UI quits; the daemon stays and still holds the rollout.
    probe.rows = [process(7000, "codex", daemon)]
    clock["now"] = NOW + timedelta(seconds=2)
    collector.probe()
    with sessions() as db:
        ended = session(db, "codex", CODEX_SESSION)
        assert ended.state is SessionState.ENDED
        assert ended.state_evidence["basis"] == "process_exited"


def test_a_codex_daemon_alone_runs_no_session(setup):
    collector, sessions, probe, home, _ = setup
    collector.backfill()
    rollout = next(home.glob(f".codex/sessions/*/*/*/rollout-*-{CODEX_SESSION}.jsonl"))
    probe.rows = [process(7000, "codex", ("codex", "app-server", "--managed-daemon"))]
    probe.files = {7000: [str(rollout)]}
    collector.probe()
    with sessions() as db:
        quiet = session(db, "codex", CODEX_SESSION)
        assert quiet.state not in (SessionState.ACTIVE, SessionState.WAITING)
        assert not db.scalars(
            select(SessionProcess).where(SessionProcess.session_id == quiet.id)
        ).all()


def test_a_claude_session_takes_the_registry_status(setup):
    collector, sessions, probe, home, _ = setup
    collector.backfill()
    registry = home / ".claude" / "sessions" / "5151.json"
    registry.write_text(
        json.dumps(
            {
                "pid": 5151,
                "sessionId": CLAUDE_SESSION,
                "status": "busy",
                "statusUpdatedAt": int(NOW.timestamp() * 1000),
            }
        )
    )
    probe.rows = [process(5151, "claude", ("claude",))]
    collector.probe()
    with sessions() as db:
        busy = session(db, "claude_code", CLAUDE_SESSION)
        assert busy.state is SessionState.ACTIVE
        assert busy.state_evidence["basis"] == "harness_busy"


UNWRITTEN_SESSION = "9d2f4b1c-6a3e-4f70-8c15-0b7e2a9d4c36"


def test_a_claude_session_with_no_transcript_is_shown_from_the_registry(setup):
    collector, sessions, probe, home, clock = setup
    collector.backfill()
    (home / ".claude" / "sessions" / "6161.json").write_text(
        json.dumps(
            {
                "pid": 6161,
                "sessionId": UNWRITTEN_SESSION,
                "cwd": "/work/unwritten",
                "entrypoint": "sdk-cli",
                "status": "busy",
                "statusUpdatedAt": int(NOW.timestamp() * 1000),
            }
        )
    )
    # Claude Code's own installer links "claude" to a binary named after its version.
    started = NOW - timedelta(minutes=5)
    probe.rows = [
        ProcessRow(
            identity=ProcessIdentity(6161, started),
            ppid=1,
            name="2.1.0",
            exe="/home/someone/.local/share/claude/versions/2.1.0",
            argv=("/home/someone/.local/bin/claude", "--print", "--no-session-persistence"),
        )
    ]

    def stored(db) -> list[Session]:
        return db.scalars(select(Session).where(Session.native_id == UNWRITTEN_SESSION)).all()

    collector.probe()
    with sessions() as db:
        assert stored(db) == [], "one pass could be an id held only while starting up"

    clock["now"] = NOW + timedelta(seconds=2)
    collector.probe()
    with sessions() as db:
        (shown,) = stored(db)
        assert (shown.harness, shown.surface) == ("claude_code", "exec")
        assert shown.cwd == "/work/unwritten"
        assert (shown.started_at, shown.last_activity_at) == (started, None)
        assert shown.state is SessionState.ACTIVE
        assert shown.state_evidence["basis"] == "harness_busy"
        (row,) = db.scalars(select(SessionProcess).where(SessionProcess.session_id == shown.id))
        assert row.pid == 6161

    # A transcript that comes after all fills the same session in.
    transcript = next(home.glob(f".claude/projects/*/{CLAUDE_SESSION}.jsonl"))
    late = transcript.with_name(f"{UNWRITTEN_SESSION}.jsonl")
    late.write_text(transcript.read_text().replace(CLAUDE_SESSION, UNWRITTEN_SESSION))
    assert collector.scan([late.resolve()]).lines > 0
    with sessions() as db:
        (filled,) = stored(db)
        assert filled.id == shown.id and filled.last_activity_at is not None

    probe.rows = []
    clock["now"] = NOW + timedelta(seconds=4)
    collector.probe()
    with sessions() as db:
        (ended,) = stored(db)
        assert ended.state is SessionState.ENDED
        assert ended.state_evidence["basis"] == "process_exited"


def test_a_registry_entry_with_no_process_behind_it_stores_nothing(setup):
    collector, sessions, probe, home, clock = setup
    collector.backfill()
    (home / ".claude" / "sessions" / "6161.json").write_text(
        json.dumps({"pid": 6161, "sessionId": UNWRITTEN_SESSION, "status": "busy"})
    )
    # The pid now belongs to something that is not Claude Code.
    probe.rows = [process(6161, "zsh", ("zsh",))]
    for seconds in (0, 2, 4):
        clock["now"] = NOW + timedelta(seconds=seconds)
        collector.probe()
    with sessions() as db:
        assert not db.scalars(select(Session).where(Session.native_id == UNWRITTEN_SESSION)).all()


def test_a_state_is_written_only_when_it_changes(setup):
    collector, sessions, probe, home, clock = setup
    collector.backfill()
    rollout = next(home.glob(f".codex/sessions/*/*/*/rollout-*-{CODEX_SESSION}.jsonl"))
    probe.rows = [process(4242, "codex", ("codex", "exec"))]
    probe.files = {4242: [str(rollout)]}
    collector.probe()
    with sessions() as db:
        first = session(db, "codex", CODEX_SESSION).updated_at
    for step in range(1, 4):
        clock["now"] = NOW + timedelta(seconds=2 * step)
        assert collector.probe() == 0
    with sessions() as db:
        assert session(db, "codex", CODEX_SESSION).updated_at == first


def test_each_change_of_state_is_kept_as_one_row_with_its_basis(setup):
    collector, sessions, probe, home, clock = setup
    collector.backfill()
    rollout = next(home.glob(f".codex/sessions/*/*/*/rollout-*-{CODEX_SESSION}.jsonl"))

    def history(db):
        live = session(db, "codex", CODEX_SESSION)
        return db.scalars(
            select(SessionStateChange)
            .where(SessionStateChange.session_id == live.id)
            .order_by(SessionStateChange.id)
        ).all()

    with assert_untouched([home]):
        probe.rows = [process(4242, "codex", ("codex", "exec"))]
        probe.files = {4242: [str(rollout)]}
        collector.probe()
        with sessions() as db:
            (first,) = history(db)
            live = session(db, "codex", CODEX_SESSION)
            assert (first.state, first.previous, first.changed_at) == (live.state, None, NOW)
            assert first.basis.value == live.state_evidence["basis"]
            rows = db.scalar(select(func.count()).select_from(SessionStateChange))
        for step in range(1, 4):
            clock["now"] = NOW + timedelta(seconds=2 * step)
            collector.probe()
        with sessions() as db:
            assert db.scalar(select(func.count()).select_from(SessionStateChange)) == rows
        probe.rows, probe.files = [], {}
        clock["now"] = NOW + timedelta(seconds=10)
        collector.probe()
        with sessions() as db:
            first, ended = history(db)
            assert ended.state is SessionState.ENDED and ended.basis.value == "process_exited"
            assert ended.previous is first.state
            assert ended.changed_at == NOW + timedelta(seconds=10)


def test_states_carry_on_after_a_restart(setup, tmp_path):
    collector, sessions, probe, home, clock = setup
    collector.backfill()
    rollout = next(home.glob(f".codex/sessions/*/*/*/rollout-*-{CODEX_SESSION}.jsonl"))
    probe.rows = [process(4242, "codex", ("codex", "exec"))]
    probe.files = {4242: [str(rollout)]}
    collector.probe()
    probe.rows, probe.files = [], {}
    clock["now"] = NOW + timedelta(seconds=2)
    collector.probe()
    restarted = Collector(
        sessions,
        Host(OperatingSystem.LINUX, home, {}),
        snapshot_dir=tmp_path / "snapshots",
        probe=FakeProbe(),
        clock=lambda: NOW + timedelta(minutes=1),
    )
    restarted.probe()
    with sessions() as db:
        assert session(db, "codex", CODEX_SESSION).state is SessionState.ENDED, (
            "not back to unknown"
        )


# -- install and uninstall ------------------------------------------------------------


def recording_runner(calls: list[list[str]]):
    def run(command, **_kwargs):
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


@pytest.mark.parametrize("system", [OperatingSystem.MACOS, OperatingSystem.LINUX])
def test_install_starts_the_service_at_login_as_its_own_process(
    tmp_path, system, monkeypatch, capsys
):
    monkeypatch.setenv("AGDASH_PORT", "8899")
    calls: list[list[str]] = []
    host = Host(system, tmp_path / "home", {})
    manager = host.service_manager(
        run=recording_runner(calls), **({"uid": 501} if system is OperatingSystem.MACOS else {})
    )
    assert cli.main(["install"], host=host, manager=manager) == 0
    text = manager.unit_path.read_text()
    program = [sys.executable, "-m", "agdash.cli", "serve"]
    if system is OperatingSystem.MACOS:
        document = plistlib.loads(text.encode())
        assert document["Label"] == LAUNCHD_LABEL
        assert document["ProgramArguments"] == program
        assert document["RunAtLoad"] is True, "starts at login"
        assert document["KeepAlive"] == {"SuccessfulExit": False}, "restarts after a crash"
        assert document["EnvironmentVariables"] == {"AGDASH_PORT": "8899"}
        assert calls[-1][:2] == ["launchctl", "bootstrap"]
    else:
        assert "WantedBy=default.target" in text, "starts at login"
        assert "Restart=on-failure" in text
        assert f'ExecStart="{sys.executable}" "-m" "agdash.cli" "serve"' in text
        assert ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT] in calls
    assert stat.S_IMODE(manager.unit_path.stat().st_mode) & 0o022 == 0, "nobody else can change it"
    assert "starts at login" in capsys.readouterr().out


@pytest.mark.parametrize("system", [OperatingSystem.MACOS, OperatingSystem.LINUX])
def test_uninstall_removes_the_definition_and_leaves_the_data(tmp_path, system, capsys):
    calls: list[list[str]] = []
    host = Host(system, tmp_path / "home", {})
    manager = host.service_manager(
        run=recording_runner(calls), **({"uid": 501} if system is OperatingSystem.MACOS else {})
    )
    cli.main(["install"], host=host, manager=manager)
    data = host.data_dir()
    data.mkdir(parents=True, exist_ok=True)
    for name in ("agdash.db", "secrets.json"):
        (data / name).write_text("kept")
    assert manager.unit_path.exists()
    assert cli.main(["uninstall"], host=host, manager=manager) == 0
    assert not manager.unit_path.exists()
    assert {"agdash.db", "secrets.json"} <= {p.name for p in data.iterdir()}
    assert all((data / name).read_text() == "kept" for name in ("agdash.db", "secrets.json"))
    printed = capsys.readouterr().out
    assert "no longer starts at login" in printed and str(data) in printed
    assert manager.state() is ServiceState.NOT_INSTALLED


# -- status, doctor and backfill ----------------------------------------------------


class FakeManager:
    def __init__(self, state=ServiceState.NOT_INSTALLED, unit_path=Path("/tmp/agdashd.unit")):
        self._state = state
        self.unit_path = unit_path

    def state(self):
        return self._state


def test_the_token_goes_to_a_pipe_and_never_to_a_terminal(tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path)
    host = Host(OperatingSystem.LINUX, home, {})
    assert cli.main(["token"], host=host) == 0
    first = capsys.readouterr().out
    assert len(first) >= 32 and "\n" not in first
    assert cli.main(["token"], host=host) == 0
    assert capsys.readouterr().out == first  # the same token each time
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert cli.main(["token"], host=host) == 2
    shown = capsys.readouterr()
    assert first not in shown.out and first not in shown.err
    assert "does not print the token to a terminal" in shown.err


def test_status_and_doctor_report_without_secrets(tmp_path, monkeypatch, capsys):
    home = make_home(tmp_path)
    host = Host(OperatingSystem.LINUX, home, {})
    monkeypatch.setenv("AGDASH_DATABASE_URL", f"sqlite:///{host.data_dir() / 'agdash.db'}")
    from agdash.config import get_settings

    get_settings.cache_clear()
    try:
        assert cli.main(["backfill"], host=host) == 0
        backfilled = capsys.readouterr().out
        assert "new lines" in backfilled and "codex:" in backfilled
        data = host.data_dir()
        assert stat.S_IMODE(data.stat().st_mode) == 0o700
        assert stat.S_IMODE((data / "agdash.db").stat().st_mode) == 0o600
        assert cli.main(["status"], host=host, manager=FakeManager()) == 0
        status = capsys.readouterr().out
        assert "not installed" in status and "sessions" in status
        assert cli.main(["doctor"], host=host, manager=FakeManager(ServiceState.RUNNING)) == 0
        report = capsys.readouterr().out
        assert "[ok  ] Listening address" in report and "[ok  ] Store file" in report
        assert "[ok  ] codex files" in report
        monkeypatch.setenv("AGDASH_HOST", "0.0.0.0")
        get_settings.cache_clear()
        assert cli.main(["doctor"], host=host, manager=FakeManager()) == 1
        assert "[FAIL] Listening address" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


def test_a_harness_that_is_not_installed_is_reported_so_and_not_as_a_fault(
    tmp_path, monkeypatch, capsys
):
    home = tmp_path / "home"
    shutil.copytree(FIXTURES / "codex", home / ".codex")  # only Codex is installed
    host = Host(OperatingSystem.LINUX, home, {})
    monkeypatch.setenv("AGDASH_DATABASE_URL", f"sqlite:///{tmp_path / 'agdash.db'}")
    from agdash.config import get_settings

    get_settings.cache_clear()
    try:
        assert cli.main(["backfill"], host=host) == 0
        assert "codex:" in capsys.readouterr().out
        assert cli.main(["doctor"], host=host, manager=FakeManager(ServiceState.RUNNING)) == 0
        report = capsys.readouterr().out
        for harness in ("claude_code", "antigravity", "pi"):
            assert f"[ok  ] {harness} files: not installed on this host" in report
        assert "[ok  ] codex files" in report and "Harnesses" not in report

        empty = Host(OperatingSystem.LINUX, tmp_path / "empty", {})
        (tmp_path / "empty").mkdir()
        assert cli.main(["doctor"], host=empty, manager=FakeManager(ServiceState.RUNNING)) == 0
        assert "[warn] Harnesses: none installed" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


def test_the_command_passes_through_to_the_other_tools(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["export", "--help"])
    assert stopped.value.code == 0
    assert "session" in capsys.readouterr().out


# -- the service, end to end --------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_service_collects_serves_and_stops_cleanly(tmp_path):
    home = make_home(tmp_path)
    port = free_port()
    database = tmp_path / "service.db"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "XDG_DATA_HOME": str(tmp_path / "xdg"),
        "AGDASH_PORT": str(port),
        "AGDASH_DATABASE_URL": f"sqlite:///{database}",
    }
    service = subprocess.Popen(
        [sys.executable, "-m", "agdash.cli", "serve"],
        env=env,
        cwd=Path(__file__).parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60
        answered = sessions = 0
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    answered = 1
            except OSError:
                pass
            if answered and database.exists():
                engine = create_engine(f"sqlite:///{database}")
                try:
                    with engine.connect() as connection:
                        sessions = connection.execute(
                            select(func.count()).select_from(Session)
                        ).scalar_one()
                except Exception:
                    sessions = 0
                engine.dispose()
                if sessions:
                    break
            time.sleep(0.5)
        assert answered, "the service answers on loopback"
        assert sessions > 0, "the collector read the harness files"
        service.send_signal(signal.SIGTERM)
        output, _ = service.communicate(timeout=30)
        assert service.returncode == 0, output[-2000:]
    finally:
        if service.poll() is None:
            service.kill()
    assert database.exists(), "the data stays after the service stops"


def test_the_installed_service_runs_the_frozen_binary_itself(monkeypatch):
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli.sys, "executable", "/Applications/agdash/agdash")
    assert cli.service_program() == ("/Applications/agdash/agdash", "serve")


def test_the_installed_service_runs_this_python_when_not_frozen(monkeypatch):
    monkeypatch.delattr(cli.sys, "frozen", raising=False)
    assert cli.service_program()[1:] == ("-m", "agdash.cli", "serve")
