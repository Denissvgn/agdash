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
