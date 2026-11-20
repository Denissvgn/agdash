"""Find agent processes on this host and how they relate to each other.

One pass reads the process table, recognises each harness's processes, reads
the working directory and harness files of those that matter, and records for
every agent session the nearest agent process above it. Comparing two passes
tells which processes started and which ended.

Everything here only reads. Raw command lines are used to recognise a process
and are then redacted; only the redacted form is kept.
"""

import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from agdash.platform.probes import ProcessGone, ProcessIdentity, ProcessProbe, ProcessRow
from agdash.platform.roots import is_inside
from agdash.redaction import redact_argv
from agdash.vocab import Harness, Surface

MAX_ANCESTRY = 64


class Role(StrEnum):
    """What a harness process is for."""

    SESSION = "session"  # runs one agent session
    HOST = "host"  # serves several sessions, such as a desktop app's back end
    HELPER = "helper"  # belongs to a harness but runs no session


@dataclass(frozen=True)
class Classification:
    harness: Harness
    role: Role
    surface: Surface | None = None


@dataclass(frozen=True)
class ObservedProcess:
    """A harness process as one pass saw it. Holds no raw command line."""

    identity: ProcessIdentity
    ppid: int | None
    name: str
    harness: Harness
    role: Role
    surface: Surface | None
    argv_redacted: tuple[str, ...]
    cwd: str | None = None
    harness_files: tuple[str, ...] = ()
    parent: ProcessIdentity | None = None
    parent_harness: Harness | None = None


@dataclass(frozen=True)
class Snapshot:
    processes: tuple[ObservedProcess, ...]

    def by_identity(self) -> dict[ProcessIdentity, ObservedProcess]:
        return {process.identity: process for process in self.processes}


# -- recognising harness processes -------------------------------------------------

_IDE_NAMES = re.compile(
    r"^(code|code helper.*|code - insiders|cursor|cursor helper.*|windsurf|zed|idea|pycharm"
    r"|webstorm|goland|clion|rider|phpstorm|rubymine|datagrip|android studio|fleet)$",
    re.IGNORECASE,
)
_IDE_BUNDLES = ("/Visual Studio Code.app/", "/Cursor.app/", "/Windsurf.app/", "/Zed.app/")
_CLAUDE_DESKTOP_BUNDLE = "/Claude.app/"
_CODEX_DESKTOP_BUNDLES = ("/ChatGPT.app/", "/Codex.app/")

_CLAUDE_HELPER_COMMANDS = {"mcp", "doctor", "update", "install", "config", "migrate-installer"}
_CODEX_COMMANDS = {
    "exec": (Role.SESSION, Surface.EXEC),
    "e": (Role.SESSION, Surface.EXEC),
    "resume": (Role.SESSION, Surface.CLI),
    "app-server": (Role.HOST, None),
}
_CODEX_HELPER_COMMANDS = {
    "exec-server",
    "mcp",
    "mcp-server",
    "login",
    "logout",
    "apply",
    "a",
    "completion",
    "debug",
    "sandbox",
    "cloud",
    "proto",
    "help",
    "features",
}
_PI_PACKAGE = "pi-coding-agent"


def _base(path: str | None) -> str:
    return os.path.basename(path or "")


def _is_claude_release(path: str | None) -> bool:
    """Whether ``path`` is a release kept by Claude Code's own installer."""
    folders = Path(path or "").parts[-3:-1]
    return folders == ("claude", "versions")


def _first_command(argv: Sequence[str], known: set[str]) -> str | None:
    """The first argument that is a known subcommand; flags and their values are skipped."""
    for argument in argv[1:]:
        if argument in known:
            return argument
    return None


def _under_ide(ancestors: Sequence[ProcessRow]) -> bool:
    return any(
        _IDE_NAMES.match(row.name) or any(b in (row.exe or "") for b in _IDE_BUNDLES)
        for row in ancestors
    )


def _under_bundle(ancestors: Sequence[ProcessRow], bundles: Iterable[str]) -> bool:
    bundles = tuple(bundles)
    return any(any(b in (row.exe or "") for b in bundles) for row in ancestors)


def classify(row: ProcessRow, ancestors: Sequence[ProcessRow] = ()) -> Classification | None:
    """Which harness a process belongs to and what it does, or ``None`` for other processes.

    ``ancestors`` runs from the parent upwards and decides the surface.
    """
    exe, argv0 = _base(row.exe), _base(row.argv[0] if row.argv else "")

    # Claude Code. The same binary also runs bundled tools under other names
    # (a search tool, for example), so the name it was started under must match.
    # The npm package's binary is called claude.exe, on every system. When the system
    # will not say which binary runs, the name it was started under decides.
    # Its own installer keeps each release as claude/versions/<version> and links
    # "claude" to it, so that binary is named after its version; started by its own
    # path rather than through the link, it carries that name as well.
    installed = _is_claude_release(row.exe)
    if (exe in ("claude", "claude.exe", "") or installed) and (
        argv0 == "claude" or (installed and argv0 == exe)
    ):
        if _first_command(row.argv, _CLAUDE_HELPER_COMMANDS):
            return Classification(Harness.CLAUDE_CODE, Role.HELPER)
        if _under_bundle(ancestors, [_CLAUDE_DESKTOP_BUNDLE]):
            surface = Surface.DESKTOP
        elif _under_ide(ancestors):
            surface = Surface.IDE
        elif "-p" in row.argv or "--print" in row.argv:
            surface = Surface.EXEC
        else:
            surface = Surface.CLI
        return Classification(Harness.CLAUDE_CODE, Role.SESSION, surface)

    # Codex: one binary, its subcommand decides the role.
    if exe == "codex" or argv0 == "codex":
        command = _first_command(row.argv, set(_CODEX_COMMANDS) | _CODEX_HELPER_COMMANDS)
        if command in _CODEX_HELPER_COMMANDS:
            return Classification(Harness.CODEX, Role.HELPER)
        if command == "app-server":
            rest = row.argv[row.argv.index("app-server") + 1 :]
            # `codex app-server daemon ...` looks after the daemon and runs no session.
            if rest[:1] == ("daemon",):
                return Classification(Harness.CODEX, Role.HELPER)
            # The daemon the terminal UI hands its sessions to: a terminal back end.
            if "--managed-daemon" in rest:
                return Classification(Harness.CODEX, Role.HOST, Surface.CLI)
        role, surface = _CODEX_COMMANDS.get(command or "", (Role.SESSION, Surface.CLI))
        if role is Role.HOST:
            if _under_bundle(ancestors, _CODEX_DESKTOP_BUNDLES):
                surface = Surface.DESKTOP
            elif _under_ide(ancestors):
                surface = Surface.IDE
            else:
                surface = Surface.DESKTOP
        return Classification(Harness.CODEX, role, surface)

    # Antigravity: the desktop app hosts sessions; agy is its command-line client.
    if exe == "agy" or argv0 == "agy":
        return Classification(Harness.ANTIGRAVITY, Role.SESSION, Surface.CLI)
    if exe == "Antigravity" and "/Antigravity.app/" in (row.exe or ""):
        return Classification(Harness.ANTIGRAVITY, Role.HOST, Surface.DESKTOP)
    if "/Antigravity.app/" in (row.exe or ""):
        return Classification(Harness.ANTIGRAVITY, Role.HELPER)

    # Pi: a Node script, or a compiled binary called pi. Pi names its own process
    # "pi", which replaces the command line where the system lets it (Linux). When the
    # system will not say which binary runs, the name it was started under decides.
    script = _base(row.argv[1]) if len(row.argv) > 1 else ""
    if exe == "pi" or (
        exe in ("node", "bun", "")
        and (argv0 == "pi" or script == "pi" or any(_PI_PACKAGE in a for a in row.argv[1:2]))
    ):
        return Classification(Harness.PI, Role.SESSION, Surface.CLI)
    return None


# -- one pass --------------------------------------------------------------------


def ancestry(row: ProcessRow, rows_by_pid: Mapping[int, ProcessRow]) -> list[ProcessRow]:
    """Parent, grandparent and so on.

    A parent that started after its child cannot be its parent: its pid was
    reused after the real parent ended. The chain stops there.
    """
    chain: list[ProcessRow] = []
    current = row
    seen = {row.identity.pid}
    while len(chain) < MAX_ANCESTRY and current.ppid and current.ppid not in seen:
        parent = rows_by_pid.get(current.ppid)
        if parent is None or parent.identity.started_at > current.identity.started_at:
            break
        chain.append(parent)
        seen.add(parent.identity.pid)
        current = parent
    return chain


def _harness_files(files: Iterable[str] | None, roots: tuple[Path, ...]) -> tuple[str, ...]:
    """Only the open files inside harness directories; nothing else is kept."""
    if not files:
        return ()
    return tuple(sorted({f for f in files if is_inside(Path(f), roots)}))


def take_snapshot(
    rows: Sequence[ProcessRow],
    probe: ProcessProbe,
    harness_roots: tuple[Path, ...],
) -> Snapshot:
    """Recognise harness processes in ``rows`` and read what matters about each."""
    rows_by_pid = {row.identity.pid: row for row in rows}
    classified: dict[ProcessIdentity, tuple[ProcessRow, Classification, list[ProcessRow]]] = {}
    for row in rows:
        chain = ancestry(row, rows_by_pid)
        found = classify(row, chain)
        if found is not None:
            classified[row.identity] = (row, found, chain)

    observed = []
    for identity, (row, found, chain) in classified.items():
        cwd = files = None
        if found.role is not Role.HELPER:
            try:
                cwd = probe.cwd(identity)
                files = probe.open_files(identity)
            except ProcessGone:
                continue
        parent = next(
            (
                (above.identity, classified[above.identity][1].harness)
                for above in chain
                if above.identity in classified
                and classified[above.identity][1].role is not Role.HELPER
            ),
            (None, None),
        )
        observed.append(
            ObservedProcess(
                identity=identity,
                ppid=row.ppid,
                name=row.name,
                harness=found.harness,
                role=found.role,
                surface=found.surface,
                argv_redacted=tuple(redact_argv(row.argv)),
                cwd=cwd,
                harness_files=_harness_files(files, harness_roots),
                parent=parent[0] if found.role is Role.SESSION else None,
                parent_harness=parent[1] if found.role is Role.SESSION else None,
            )
        )
    observed.sort(key=lambda process: (process.identity.started_at, process.identity.pid))
    return Snapshot(tuple(observed))


# -- what a held file says about the session --------------------------------------

_ROLLOUT = re.compile(
    r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-"
    r"(?P<session>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl"
)


def codex_rollouts(process: ObservedProcess, codex_roots: tuple[Path, ...]) -> dict[str, str]:
    """Session id to rollout path, for every Codex rollout file the process holds open."""
    found = {}
    for path in process.harness_files:
        match = _ROLLOUT.fullmatch(os.path.basename(path))
        if match and is_inside(Path(path), codex_roots):
            found[match["session"]] = path
    return found


def is_terminal_back_end(process: ObservedProcess) -> bool:
    """A Codex daemon serving the terminal UI's sessions (see :func:`classify`)."""
    return (
        process.harness is Harness.CODEX
        and process.role is Role.HOST
        and process.surface is Surface.CLI
    )


def terminal_clients(snapshot: Snapshot) -> list[ObservedProcess]:
    """The Codex terminal UIs running: the clients a terminal back end serves."""
    return [
        process
        for process in snapshot.processes
        if process.harness is Harness.CODEX
        and process.role is Role.SESSION
        and process.surface is Surface.CLI
    ]


def parent_session(
    process: ObservedProcess,
    snapshot: Snapshot,
    session_of: Mapping[ProcessIdentity, Sequence[str]],
) -> str | None:
    """The session that started ``process``, through the nearest agent process above it.

    ``session_of`` maps a process to the sessions it is known to run, as the
    harness adapters found them. A host that runs several sessions at once
    cannot say which one started the child, so no parent is given then.
    """
    if process.parent is None:
        return None
    sessions = list(dict.fromkeys(session_of.get(process.parent, ())))
    return sessions[0] if len(sessions) == 1 else None


# -- comparing passes --------------------------------------------------------------


@dataclass
class Changes:
    started: list[ObservedProcess] = field(default_factory=list)
    ended: list[ObservedProcess] = field(default_factory=list)
    continuing: list[ObservedProcess] = field(default_factory=list)


class ProcessTracker:
    """Tells which harness processes started and ended between passes.

    Processes are told apart by pid and start time together, so a pid that is
    reused shows as one process ending and another starting, never as one.
    """

    def __init__(self) -> None:
        self._current: dict[ProcessIdentity, ObservedProcess] = {}

    def update(self, snapshot: Snapshot) -> Changes:
        latest = snapshot.by_identity()
        changes = Changes(
            started=[p for i, p in latest.items() if i not in self._current],
            ended=[p for i, p in self._current.items() if i not in latest],
            continuing=[p for i, p in latest.items() if i in self._current],
        )
        self._current = latest
        return changes


def session_process_fields(process: ObservedProcess) -> dict[str, Any]:
    """The ``session_processes`` columns this pass can fill. ``argv`` is already redacted."""
    return {
        "pid": process.identity.pid,
        "process_started_at": process.identity.started_at,
        "parent_pid": process.ppid,
        "argv_redacted": list(process.argv_redacted),
    }
