"""The process probe: recognising harness processes, their files, parents and lifetimes.

Process trees here are synthetic, except where a test starts its own short-lived
child process to read it. No real agent process is read or touched.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agdash.collector.processes import (
    Classification,
    ObservedProcess,
    ProcessTracker,
    Role,
    Snapshot,
    ancestry,
    classify,
    codex_rollouts,
    parent_session,
    session_process_fields,
    take_snapshot,
)
from agdash.db.models import SessionProcess
from agdash.platform import OperatingSystem, current_host
from agdash.platform.probes import ProcessGone, ProcessIdentity, ProcessProbe, ProcessRow
from agdash.vocab import Harness, Surface

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
CLAUDE_DESKTOP = "/Applications/Claude.app/Contents/MacOS/Claude"
CLAUDE_CODE = "/Users/someone/Library/Application Support/Claude/claude-code/2.1.0/claude"
CODEX = "/opt/homebrew/bin/codex"
CODEX_DESKTOP = "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT"
SESSION_ID = "0b5f6c1e-7a2d-4c3b-9e1f-2d3c4b5a6f70"


def row(pid, ppid=1, *, name=None, exe=None, argv=(), started=0) -> ProcessRow:
    return ProcessRow(
        identity=ProcessIdentity(pid, T0 + timedelta(seconds=started)),
        ppid=ppid,
        name=name or os.path.basename(exe or (argv[0] if argv else "proc")),
        exe=exe,
        argv=tuple(argv),
    )


# -- recognising processes ---------------------------------------------------------


@pytest.mark.parametrize(
    ("process", "ancestors", "expected"),
    [
        (
            row(10, exe=CLAUDE_CODE, argv=[CLAUDE_CODE, "--output-format", "stream-json"]),
            [row(5, exe=CLAUDE_DESKTOP)],
            Classification(Harness.CLAUDE_CODE, Role.SESSION, Surface.DESKTOP),
        ),
        (
            row(10, exe="/usr/local/bin/claude", argv=["claude"]),
            [row(5, exe="/bin/zsh")],
            Classification(Harness.CLAUDE_CODE, Role.SESSION, Surface.CLI),
        ),
        (
            row(10, exe="/usr/local/bin/claude", argv=["claude", "-p", "summarise"]),
            [],
            Classification(Harness.CLAUDE_CODE, Role.SESSION, Surface.EXEC),
        ),
        (
            row(10, exe="/usr/local/bin/claude", argv=["claude"]),
            [row(5, name="Code Helper (Plugin)", exe="/Applications/Visual Studio Code.app/x")],
            Classification(Harness.CLAUDE_CODE, Role.SESSION, Surface.IDE),
        ),
        (
            row(10, exe="/usr/local/bin/claude", argv=["claude", "mcp", "serve"]),
            [],
            Classification(Harness.CLAUDE_CODE, Role.HELPER),
        ),
        (row(10, exe=CLAUDE_CODE, argv=["ugrep", "-r", "needle"]), [], None),
        (
            row(10, exe=CODEX, argv=["codex", "exec", "--json", "fix it"]),
            [],
            Classification(Harness.CODEX, Role.SESSION, Surface.EXEC),
        ),
        (
            row(10, exe=CODEX, argv=["codex", "-c", "model=x", "exec", "fix it"]),
            [],
            Classification(Harness.CODEX, Role.SESSION, Surface.EXEC),
        ),
        (
            row(10, exe=CODEX, argv=["codex"]),
            [],
            Classification(Harness.CODEX, Role.SESSION, Surface.CLI),
        ),
        (
            row(
                10,
                exe="/Applications/ChatGPT.app/Contents/Resources/codex",
                argv=["codex", "app-server"],
            ),
            [row(5, exe=CODEX_DESKTOP)],
            Classification(Harness.CODEX, Role.HOST, Surface.DESKTOP),
        ),
        (
            row(10, exe=CODEX, argv=["codex", "app-server"]),
            [row(5, name="Code Helper (Plugin)")],
            Classification(Harness.CODEX, Role.HOST, Surface.IDE),
        ),
        (
            row(10, exe=CODEX, argv=["codex", "exec-server"]),
            [],
            Classification(Harness.CODEX, Role.HELPER),
        ),
        (
            row(10, exe=CODEX, argv=["codex", "login"]),
            [],
            Classification(Harness.CODEX, Role.HELPER),
        ),
        (
            row(10, exe="/usr/local/bin/agy", argv=["agy"]),
            [],
            Classification(Harness.ANTIGRAVITY, Role.SESSION, Surface.CLI),
        ),
        (
            row(
                10,
                exe="/Applications/Antigravity.app/Contents/MacOS/Antigravity",
                argv=["Antigravity"],
            ),
            [],
            Classification(Harness.ANTIGRAVITY, Role.HOST, Surface.DESKTOP),
        ),
        (
            row(
                10,
                exe="/Applications/Antigravity.app/Contents/Resources/bin/language_server",
                argv=["ls"],
            ),
            [],
            Classification(Harness.ANTIGRAVITY, Role.HELPER),
        ),
        (
            row(10, exe="/usr/local/bin/node", argv=["node", "/usr/local/bin/pi"]),
            [],
            Classification(Harness.PI, Role.SESSION, Surface.CLI),
        ),
        (
            row(
                10,
                exe="/usr/local/bin/node",
                argv=["node", "/x/node_modules/@scope/pi-coding-agent/dist/cli.js"],
            ),
            [],
            Classification(Harness.PI, Role.SESSION, Surface.CLI),
        ),
        (row(10, exe="/usr/local/bin/node", argv=["node", "server.js"]), [], None),
        (row(10, exe="/bin/zsh", argv=["zsh", "-c", "claude-cwd-file"]), [], None),
        (row(10, exe=None, argv=()), [], None),
    ],
)
def test_harness_processes_are_recognised(process, ancestors, expected):
    assert classify(process, ancestors) == expected


# -- ancestry and pid reuse ---------------------------------------------------------


def test_ancestry_walks_up_to_the_first_missing_parent():
    rows = [row(1, 0, started=-100), row(5, 1), row(6, 5, started=1), row(7, 6, started=2)]
    by_pid = {r.identity.pid: r for r in rows}
    assert [r.identity.pid for r in ancestry(by_pid[7], by_pid)] == [6, 5, 1]


def test_a_parent_younger_than_its_child_is_a_reused_pid():
    child = row(7, ppid=6, started=10)
    impostor = row(6, ppid=1, started=20)
    assert ancestry(child, {6: impostor, 7: child}) == []


def test_a_pid_cycle_does_not_loop():
    a, b = row(5, ppid=6), row(6, ppid=5)
    assert len(ancestry(a, {5: a, 6: b})) == 1


class FakeProbe:
    def __init__(self, cwd=None, files=None, gone=()):
        self.cwd_by_pid = cwd or {}
        self.files_by_pid = files or {}
        self.gone = set(gone)
        self.read = []

    def cwd(self, identity):
        self.read.append(identity.pid)
        if identity.pid in self.gone:
            raise ProcessGone(identity.pid)
        return self.cwd_by_pid.get(identity.pid)

    def open_files(self, identity):
        return self.files_by_pid.get(identity.pid, [])


def _tree():
    """A Claude Code desktop session whose shell started a codex exec, plus noise."""
    return [
        row(1, 0, exe="/sbin/launchd", started=-1000),
        row(100, 1, exe=CLAUDE_DESKTOP, started=-500),
        row(
            200,
            100,
            exe=CLAUDE_CODE,
            argv=[CLAUDE_CODE, "--output-format", "stream-json"],
            started=-400,
        ),
        row(210, 200, exe=CLAUDE_CODE, argv=["ugrep", "needle"], started=-300),
        row(300, 200, exe="/bin/zsh", argv=["zsh", "-c", "codex exec ..."], started=-200),
        row(
            400,
            300,
            exe=CODEX,
            argv=["codex", "exec", "--api-key", "sk-" + "a1B2" * 12, "fix the tests"],
            started=-100,
        ),
        row(500, 1, exe="/usr/bin/vim", argv=["vim", "notes.txt"], started=-50),
    ]


def test_a_snapshot_keeps_harness_processes_and_their_relations(tmp_path):
    codex_root = tmp_path / ".codex"
    rollout = codex_root / "sessions/2026/01/02" / f"rollout-2026-01-02T03-04-05-{SESSION_ID}.jsonl"
    probe = FakeProbe(
        cwd={200: "/work/app", 400: "/work/app"},
        files={400: [str(rollout), "/etc/hosts", str(tmp_path / "unrelated.txt")]},
    )
    snapshot = take_snapshot(_tree(), probe, (codex_root,))
    by_pid = {p.identity.pid: p for p in snapshot.processes}

    assert set(by_pid) == {200, 400}, "only agent processes are kept"
    claude, codex = by_pid[200], by_pid[400]
    assert (claude.harness, claude.surface, claude.parent) == (
        Harness.CLAUDE_CODE,
        Surface.DESKTOP,
        None,
    )
    assert (codex.harness, codex.surface) == (Harness.CODEX, Surface.EXEC)
    assert codex.parent == claude.identity
    assert codex.parent_harness is Harness.CLAUDE_CODE
    assert codex.harness_files == (str(rollout),), "files outside harness roots are dropped"
    assert codex_rollouts(codex, (codex_root,)) == {SESSION_ID: str(rollout)}
    assert 500 not in probe.read and 210 not in probe.read


def test_a_cross_harness_child_is_linked_to_the_parent_session(tmp_path):
    snapshot = take_snapshot(_tree(), FakeProbe(), (tmp_path,))
    codex = next(p for p in snapshot.processes if p.harness is Harness.CODEX)
    claude = next(p for p in snapshot.processes if p.harness is Harness.CLAUDE_CODE)
    assert (
        parent_session(codex, snapshot, {claude.identity: ["claude-session-1"]})
        == "claude-session-1"
    )
    # A host running several sessions cannot say which one started the child.
    assert parent_session(codex, snapshot, {claude.identity: ["one", "two"]}) is None
    assert parent_session(codex, snapshot, {}) is None
    assert parent_session(claude, snapshot, {claude.identity: ["claude-session-1"]}) is None


def test_stored_arguments_are_redacted(tmp_path):
    secret = "sk-" + "a1B2" * 12
    snapshot = take_snapshot(_tree(), FakeProbe(), (tmp_path,))
    assert secret not in repr(snapshot)
    codex = next(p for p in snapshot.processes if p.harness is Harness.CODEX)
    assert codex.argv_redacted[:3] == ("codex", "exec", "--api-key")
    assert codex.argv_redacted[-1] == "fix the tests"
    fields = session_process_fields(codex)
    assert secret not in repr(fields)
    assert set(fields) <= {column.name for column in SessionProcess.__table__.columns}


def test_a_process_that_ends_mid_pass_is_left_out(tmp_path):
    snapshot = take_snapshot(_tree(), FakeProbe(gone={400}), (tmp_path,))
    assert [p.identity.pid for p in snapshot.processes] == [200]


def _observed(pid, started) -> ObservedProcess:
    return ObservedProcess(
        identity=ProcessIdentity(pid, T0 + timedelta(seconds=started)),
        ppid=1,
        name="codex",
        harness=Harness.CODEX,
        role=Role.SESSION,
        surface=Surface.CLI,
        argv_redacted=("codex",),
    )


def test_a_reused_pid_is_two_processes_not_one():
    tracker = ProcessTracker()
    first = tracker.update(Snapshot((_observed(4242, 0), _observed(77, 0))))
    assert {p.identity.pid for p in first.started} == {4242, 77}

    second = tracker.update(Snapshot((_observed(4242, 30), _observed(77, 0))))
    assert [p.identity for p in second.ended] == [_observed(4242, 0).identity]
    assert [p.identity for p in second.started] == [_observed(4242, 30).identity]
    assert [p.identity.pid for p in second.continuing] == [77]

    third = tracker.update(Snapshot(()))
    assert {p.identity for p in third.ended} == {
        _observed(4242, 30).identity,
        _observed(77, 0).identity,
    }


# -- real processes -----------------------------------------------------------------
#
# A tiny C program stands in for an agent binary. It is built at test time and
# named "codex", because a process keeps a compiled program's name on every
# system, while interpreters and copied system binaries do not.

STAND_IN = r"""
#include <fcntl.h>
#include <stdlib.h>
#include <unistd.h>

int main(void) {
    const char *hold = getenv("HOLD_FILE");
    if (hold) open(hold, O_WRONLY | O_APPEND);
    const char *spawn = getenv("SPAWN");
    if (spawn) {
        if (fork() == 0) {
            unsetenv("SPAWN");
            unsetenv("HOLD_FILE");
            execl("/bin/sh", "sh", "-c", spawn, (char *)0);
            _exit(127);
        }
    }
    sleep(60);
    return 0;
}
"""


def _compiler_available() -> bool:
    if shutil.which("cc") is None:
        return False
    # Without the command line tools, /usr/bin/cc on macOS opens an install dialog.
    if current_host().os is OperatingSystem.MACOS:
        return (
            subprocess.run(["xcode-select", "-p"], capture_output=True, check=False).returncode == 0
        )
    return True


@pytest.fixture(scope="module")
def stand_in(tmp_path_factory) -> Path:
    if not _compiler_available():
        pytest.skip("needs a C compiler to build the stand-in agent binary")
    directory = tmp_path_factory.mktemp("stand-in")
    source = directory / "stand_in.c"
    source.write_text(STAND_IN)
    binary = directory / "codex"
    subprocess.run(["cc", "-o", str(binary), str(source)], check=True, capture_output=True)
    return binary


def _find(probe, roots, predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = take_snapshot(probe.table(), probe, roots)
        found = [p for p in snapshot.processes if predicate(p)]
        if found:
            return found
        time.sleep(0.1)
    return []


def test_a_codex_process_is_mapped_to_the_rollout_file_it_holds_open(tmp_path, stand_in):
    codex_root = tmp_path / "codex-home"
    session = str(uuid.uuid4())
    rollout = codex_root / "sessions/2026/01/02" / f"rollout-2026-01-02T03-04-05-{session}.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("")
    work = tmp_path / "work"
    work.mkdir()

    env = {"PATH": os.environ.get("PATH", ""), "HOLD_FILE": str(rollout)}
    child = subprocess.Popen([str(stand_in)], cwd=work, env=env, start_new_session=True)
    try:
        probe = current_host().process_probe()
        found = _find(
            probe,
            (codex_root,),
            lambda p: p.identity.pid == child.pid and p.harness_files,
        )
        assert len(found) == 1, "the stand-in is recognised as a Codex process holding a file"
        process = found[0]
        assert (process.harness, process.surface) == (Harness.CODEX, Surface.CLI)
        assert os.path.realpath(process.cwd) == os.path.realpath(work)
        held = codex_rollouts(process, (codex_root,))
        assert set(held) == {session}
        assert os.path.realpath(held[session]) == os.path.realpath(rollout)
        assert child.poll() is None, "reading the process left it running"
    finally:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=10)


def test_a_real_child_is_linked_to_the_agent_process_above_it(tmp_path, stand_in):
    """codex, then a shell, then codex exec: the exec is linked to the codex above the shell."""
    env = {"PATH": os.environ.get("PATH", ""), "SPAWN": f'"{stand_in}" exec; true'}
    parent = subprocess.Popen([str(stand_in)], cwd=tmp_path, env=env, start_new_session=True)
    try:
        probe = current_host().process_probe()
        found = _find(
            probe,
            (tmp_path,),
            lambda p: (
                p.harness is Harness.CODEX
                and p.identity.pid != parent.pid
                and p.parent is not None
                and p.parent.pid == parent.pid
            ),
        )
        assert len(found) == 1, "the grandchild is linked to the agent process above it"
        assert found[0].parent_harness is Harness.CODEX
        assert found[0].ppid != parent.pid, "the link reaches past the shell in between"
    finally:
        os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=10)


# -- the process table asks for what a process runs once, not on every pass ---------


class FakeSystem:
    """A psutil stand-in whose processes count how often their arguments are asked for."""

    class NoSuchProcess(Exception):
        pass

    class ZombieProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    class Entry:
        def __init__(self, system, pid: int, name: str, created: float, argv: tuple[str, ...]):
            self._system = system
            self.pid, self.name, self.created, self.argv = pid, name, created, argv

        @property
        def _proc(self):
            # The table's own entry: a name of at most fifteen characters, and no more asked.
            return type("TableEntry", (), {"name": lambda _: self.name[:15]})()

        @contextlib.contextmanager
        def oneshot(self):
            yield

        def uids(self):
            return type("Uids", (), {"real": os.getuid()})()

        def create_time(self) -> float:
            return self.created

        def ppid(self) -> int:
            return 1

        def exe(self) -> str:
            return f"/usr/bin/{self.name}"

        def cmdline(self) -> list[str]:
            self._system.asked.append(self.pid)
            return list(self.argv)

    def __init__(self) -> None:
        self.entries: list[FakeSystem.Entry] = []
        self.asked: list[int] = []

    def add(self, pid: int, name: str, age: float, *argv: str) -> "FakeSystem.Entry":
        entry = self.Entry(self, pid, name, time.time() - age, argv)
        self.entries.append(entry)
        return entry

    def process_iter(self):
        return iter(list(self.entries))


def test_a_long_name_is_completed_from_the_arguments_already_known():
    system = FakeSystem()
    system.add(200, "Synthetic Helper (Renderer)", 3600, "/Apps/Synthetic Helper (Renderer)", "--x")
    system.add(201, "a-fifteen-chars", 3600, "/usr/bin/other", "a-fifteen-chars-and-more")
    probe = ProcessProbe(lsof_fallback=False, psutil_module=system)
    for _ in range(3):
        names = {row.identity.pid: row.name for row in probe.table()}
    # The table cuts the first short; the second's program is not named like it, so it stays.
    assert names == {200: "Synthetic Helper (Renderer)", 201: "a-fifteen-chars"}
    # Each was asked for its arguments once, not on every pass.
    assert sorted(system.asked) == [200, 201]


def test_a_process_is_asked_what_it_runs_once_and_again_only_now_and_then():
    from agdash.platform import probes

    system = FakeSystem()
    old = system.add(100, "claude", 3600, "claude", "--resume")
    system.add(101, "codex", 3600, "codex")
    probe = ProcessProbe(lsof_fallback=False, psutil_module=system)

    first = {row.identity.pid: row for row in probe.table()}
    assert first[100].argv == ("claude", "--resume") and first[100].exe == "/usr/bin/claude"
    assert sorted(system.asked) == [100, 101]

    # Over the next passes each is asked again once in turn, not on every pass.
    system.asked.clear()
    for _ in range(probes.ASK_AGAIN_PASSES):
        rows = probe.table()
    assert [row.argv for row in rows] == [("claude", "--resume"), ("codex",)]
    assert sorted(system.asked) == [100, 101]

    # One that has just started may still be its parent's copy, so it is asked on each pass.
    system.asked.clear()
    young = system.add(102, "zsh", 1, "zsh")
    probe.table()
    young.name, young.argv = "claude", ("claude",)
    assert {row.identity.pid: row.argv for row in probe.table()}[102] == ("claude",)
    assert system.asked.count(102) == 2

    # A process that became another program is asked again at once, whatever its age.
    system.entries.remove(young)
    system.asked.clear()
    old.name, old.argv = "node", ("node", "server.js")
    assert {row.identity.pid: row for row in probe.table()}[100].argv == ("node", "server.js")
    assert 100 in system.asked
