"""Read the process table, and a process's working directory and open files.

A pid is reused once its process ends, so every read is tied to the process's
start time, checked before and after the read. The probe never signals,
suspends or otherwise touches a process; it only reads what the operating
system reports about it.
"""

import os
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psutil

LSOF = "/usr/sbin/lsof"
LSOF_TIMEOUT_SECONDS = 10
# psutil reports the start time as a float; allow for its rounding.
START_TIME_TOLERANCE_SECONDS = 0.01

Runner = Callable[..., subprocess.CompletedProcess[str]]
# A process's program and arguments are asked for again while it is this new, and
# otherwise once in this many passes of the table.
FRESH_SECONDS = 10.0
ASK_AGAIN_PASSES = 60


@dataclass(frozen=True)
class ProcessIdentity:
    """A process, as opposed to a pid: the pid plus when that process started."""

    pid: int
    started_at: datetime


def _instant(created: float) -> datetime:
    """A process start time, rounded to the millisecond so repeated reads agree."""
    return datetime.fromtimestamp(round(created, 3), UTC)


# The process table holds a name of at most this many characters.
SHORT_NAME = 15


def _short_name(process: Any) -> str:
    """A process's name as the process table holds it, cut short if it is long.

    psutil's own ``name()`` completes a long name from the arguments, which it asks
    for each time: on a desktop, for hundreds of processes on every pass.
    """
    table_entry = getattr(process, "_proc", None)
    if table_entry is not None and hasattr(table_entry, "name"):
        return table_entry.name() or ""
    return process.name() or ""


def _full_name(short: str, argv: Sequence[str]) -> str:
    """The whole name, completed from the arguments already known, as psutil does it."""
    if len(short) >= SHORT_NAME and argv:
        whole = os.path.basename(argv[0])
        if whole.startswith(short):
            return whole
    return short


@dataclass(frozen=True)
class ProcessRow:
    """One entry of the process table. ``argv`` is raw: redact it before storing."""

    identity: ProcessIdentity
    ppid: int | None
    name: str
    exe: str | None
    argv: tuple[str, ...]


class ProcessGone(Exception):
    """The process ended, or its pid now belongs to another process."""


class ProcessProbe:
    """Working directory and open files of processes, through psutil.

    ``lsof_fallback`` is for macOS, where psutil may be refused a process that
    ``lsof`` can still describe. On Linux, ``/proc`` is all there is.
    """

    def __init__(
        self,
        *,
        lsof_fallback: bool,
        psutil_module: Any = psutil,
        run: Runner = subprocess.run,
    ) -> None:
        self._lsof_fallback = lsof_fallback
        self._psutil = psutil_module
        self._run = run
        # What each process runs, by (pid, start): asked for once and kept, see ``table``.
        self._programs: dict[tuple[int, float], tuple[str, str | None, tuple[str, ...]]] = {}
        self._passes = 0
        self._lock = threading.Lock()

    def identity(self, pid: int) -> ProcessIdentity | None:
        """The identity of whatever process has ``pid`` now, or ``None``."""
        try:
            started = self._psutil.Process(pid).create_time()
        except (self._psutil.NoSuchProcess, self._psutil.ZombieProcess):
            return None
        except self._psutil.AccessDenied:
            return None
        return ProcessIdentity(pid, _instant(started))

    def table(self) -> list[ProcessRow]:
        """Every process of the current user, as the operating system lists it now.

        Fields the system will not show are left empty rather than failing the
        whole read.
        """
        me = os.getuid()
        rows = []
        with self._lock:
            self._passes += 1
            now = time.time()
            programs: dict[tuple[int, float], tuple[str, str | None, tuple[str, ...]]] = {}
            refused = (
                self._psutil.AccessDenied,
                self._psutil.NoSuchProcess,
                self._psutil.ZombieProcess,
            )
            for process in self._psutil.process_iter():
                # These come from one cheap read per process. Its program and arguments cost a
                # further call each, which for hundreds of processes every pass adds up.
                try:
                    with process.oneshot():
                        if process.uids().real != me:
                            continue
                        created = process.create_time()
                        ppid = process.ppid()
                        short = _short_name(process)
                except refused:
                    continue
                pid = process.pid
                known = self._programs.get((pid, created))
                if (
                    known is None
                    or known[0] != short
                    # A new process is often still its parent's copy, about to run its own program.
                    or now - created < FRESH_SECONDS
                    # A process can replace its program at any age, so each is asked again in turn.
                    or (pid + self._passes) % ASK_AGAIN_PASSES == 0
                ):
                    known = (short, *self._program(process))
                programs[(pid, created)] = known
                rows.append(
                    ProcessRow(
                        identity=ProcessIdentity(pid, _instant(created)),
                        ppid=ppid,
                        name=_full_name(short, known[2]),
                        exe=known[1],
                        argv=known[2],
                    )
                )
            self._programs = programs
        return rows

    def _program(self, process: Any) -> tuple[str | None, tuple[str, ...]]:
        """What a process runs and with which arguments; empty where the system will not say."""
        refused = (
            self._psutil.AccessDenied,
            self._psutil.NoSuchProcess,
            self._psutil.ZombieProcess,
        )
        try:
            exe = process.exe() or None
        except refused:
            exe = None
        try:
            argv = tuple(process.cmdline() or ())
        except refused:
            argv = ()
        return exe, argv

    def _process(self, identity: ProcessIdentity) -> Any:
        try:
            process = self._psutil.Process(identity.pid)
            started = _instant(process.create_time())
        except (self._psutil.NoSuchProcess, self._psutil.ZombieProcess):
            raise ProcessGone(identity.pid) from None
        if abs((started - identity.started_at).total_seconds()) > START_TIME_TOLERANCE_SECONDS:
            raise ProcessGone(identity.pid)
        return process

    def _still(self, identity: ProcessIdentity) -> None:
        """Raise unless the same process still has the pid, so a read is not misattributed."""
        self._process(identity)

    def cwd(self, identity: ProcessIdentity) -> str | None:
        """The working directory, or ``None`` if this user may not see it."""
        process = self._process(identity)
        try:
            found = process.cwd()
        except self._psutil.AccessDenied:
            found = self._lsof_cwd(identity.pid) if self._lsof_fallback else None
        except (self._psutil.NoSuchProcess, self._psutil.ZombieProcess):
            raise ProcessGone(identity.pid) from None
        self._still(identity)
        return found or None

    def open_files(self, identity: ProcessIdentity) -> list[str] | None:
        """Paths of the regular files the process holds open, sorted, or ``None`` if hidden."""
        process = self._process(identity)
        try:
            found: list[str] | None = [entry.path for entry in process.open_files()]
        except self._psutil.AccessDenied:
            found = self._lsof_files(identity.pid) if self._lsof_fallback else None
        except (self._psutil.NoSuchProcess, self._psutil.ZombieProcess):
            raise ProcessGone(identity.pid) from None
        self._still(identity)
        return None if found is None else sorted(set(found))

    # -- lsof ------------------------------------------------------------------

    def _lsof(self, arguments: Sequence[str]) -> list[str] | None:
        try:
            result = self._run(
                [LSOF, "-n", "-P", "-w", *arguments],
                capture_output=True,
                text=True,
                timeout=LSOF_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        # lsof exits 1 both for "nothing found" and for "not permitted".
        if result.returncode not in (0, 1):
            return None
        return result.stdout.splitlines()

    def _lsof_cwd(self, pid: int) -> str | None:
        lines = self._lsof(["-a", "-p", str(pid), "-d", "cwd", "-Fn"])
        names = [line[1:] for line in lines or [] if line.startswith("n")]
        return names[0] if names else None

    def _lsof_files(self, pid: int) -> list[str] | None:
        lines = self._lsof(["-a", "-p", str(pid), "-Ftn"])
        if lines is None:
            return None
        return parse_lsof_regular_files(lines)


def parse_lsof_regular_files(lines: Sequence[str]) -> list[str]:
    """Regular-file paths from ``lsof -F tn`` output.

    Each open file is a ``t`` line with its type followed by an ``n`` line with
    its name; ``p`` and ``f`` lines begin a process and a descriptor.
    """
    found = []
    kind = None
    for line in lines:
        tag, value = line[:1], line[1:]
        if tag == "f":
            kind = None
        elif tag == "t":
            kind = value
        elif tag == "n" and kind == "REG" and value.startswith("/"):
            found.append(value)
    return found
