"""Read a process's working directory and open files, and nothing else.

A pid is reused once its process ends, so every read is tied to the process's
start time, checked before and after the read. The probe never signals,
suspends or otherwise touches a process; it only reads what the operating
system reports about it.
"""

import subprocess
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


@dataclass(frozen=True)
class ProcessIdentity:
    """A process, as opposed to a pid: the pid plus when that process started."""

    pid: int
    started_at: datetime


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

    def identity(self, pid: int) -> ProcessIdentity | None:
        """The identity of whatever process has ``pid`` now, or ``None``."""
        try:
            started = self._psutil.Process(pid).create_time()
        except (self._psutil.NoSuchProcess, self._psutil.ZombieProcess):
            return None
        except self._psutil.AccessDenied:
            return None
        return ProcessIdentity(pid, datetime.fromtimestamp(started, UTC))

    def _process(self, identity: ProcessIdentity) -> Any:
        try:
            process = self._psutil.Process(identity.pid)
            started = datetime.fromtimestamp(process.create_time(), UTC)
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
