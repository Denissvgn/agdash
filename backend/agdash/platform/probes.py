"""Read the process table, and a process's working directory and open files.

A pid is reused once its process ends, so every read is tied to the process's
start time, checked before and after the read. The probe never signals,
suspends or otherwise touches a process; it only reads what the operating
system reports about it.
"""

import os
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


def _instant(created: float) -> datetime:
    """A process start time, rounded to the millisecond so repeated reads agree."""
    return datetime.fromtimestamp(round(created, 3), UTC)


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
        attributes = ["pid", "ppid", "name", "exe", "cmdline", "create_time", "uids"]
        for process in self._psutil.process_iter(attributes):
            info = process.info
            uids = info.get("uids")
            created = info.get("create_time")
            if uids is None or uids.real != me or created is None:
                continue
            rows.append(
                ProcessRow(
                    identity=ProcessIdentity(info["pid"], _instant(created)),
                    ppid=info.get("ppid"),
                    name=info.get("name") or "",
                    exe=info.get("exe") or None,
                    argv=tuple(info.get("cmdline") or ()),
                )
            )
        return rows

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
