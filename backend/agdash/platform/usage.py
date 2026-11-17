"""What this process itself uses, as the operating system counts it.

Memory, the bytes it read from and wrote to storage, and the CPU time of its own
threads. A reader is only ever asked about the process it runs in: it takes no
process id from a caller and reads nothing about any other process.

Resident size is not what a process costs: on macOS memory the system has
compressed leaves it, and on Linux so does what was swapped out. The footprint
counts both, so that is the figure to show.

A number the system will not give is ``None``, never zero.
"""

import ctypes
import ctypes.util
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class OwnUsage:
    """This process, at one moment. The byte and second counts run from its start."""

    footprint_bytes: int | None = None
    resident_bytes: int | None = None
    read_bytes: int | None = None
    written_bytes: int | None = None
    # CPU seconds of each thread asked about; a thread that has ended is left out.
    thread_seconds: dict[int, float] = field(default_factory=dict)


class UsageReader(Protocol):
    def read(self, thread_ids: Iterable[int] = ()) -> OwnUsage:
        """This process now. ``thread_ids`` are ``threading.Thread.native_id`` values."""


# -- macOS: libproc -------------------------------------------------------------------

RUSAGE_INFO_V2 = 2
PROC_PIDTHREADID64INFO = 15
NANOSECONDS = 1e9


class RusageInfoV2(ctypes.Structure):
    """``struct rusage_info_v2`` of ``<sys/resource.h>``."""

    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        *(
            (name, ctypes.c_uint64)
            for name in (
                "ri_user_time",
                "ri_system_time",
                "ri_pkg_idle_wkups",
                "ri_interrupt_wkups",
                "ri_pageins",
                "ri_wired_size",
                "ri_resident_size",
                "ri_phys_footprint",
                "ri_proc_start_abstime",
                "ri_proc_exit_abstime",
                "ri_child_user_time",
                "ri_child_system_time",
                "ri_child_pkg_idle_wkups",
                "ri_child_interrupt_wkups",
                "ri_child_pageins",
                "ri_child_elapsed_abstime",
                "ri_diskio_bytesread",
                "ri_diskio_byteswritten",
            )
        ),
    ]


class ProcThreadInfo(ctypes.Structure):
    """``struct proc_threadinfo`` of ``<sys/proc_info.h>``; its times are nanoseconds."""

    _fields_ = [
        ("pth_user_time", ctypes.c_uint64),
        ("pth_system_time", ctypes.c_uint64),
        ("pth_cpu_usage", ctypes.c_int32),
        ("pth_policy", ctypes.c_int32),
        ("pth_run_state", ctypes.c_int32),
        ("pth_flags", ctypes.c_int32),
        ("pth_sleep_time", ctypes.c_int32),
        ("pth_curpri", ctypes.c_int32),
        ("pth_priority", ctypes.c_int32),
        ("pth_maxpriority", ctypes.c_int32),
        ("pth_name", ctypes.c_char * 64),
    ]


def _libproc() -> Any:
    return ctypes.CDLL(ctypes.util.find_library("proc") or "libproc.dylib")


class MacUsageReader:
    """Through libproc, which answers a process about itself without any privilege."""

    def __init__(
        self,
        *,
        library: Callable[[], Any] = _libproc,
        pid: Callable[[], int] = os.getpid,
    ) -> None:
        self._load = library
        self._library: Any = None
        self._pid = pid

    def _proc(self) -> Any:
        if self._library is None:
            self._library = self._load()
        return self._library

    def read(self, thread_ids: Iterable[int] = ()) -> OwnUsage:
        proc, pid = self._proc(), self._pid()
        usage = RusageInfoV2()
        known = proc.proc_pid_rusage(pid, RUSAGE_INFO_V2, ctypes.byref(usage)) == 0
        threads = {}
        for thread_id in thread_ids:
            info = ProcThreadInfo()
            size = ctypes.sizeof(info)
            got = proc.proc_pidinfo(
                pid, PROC_PIDTHREADID64INFO, ctypes.c_uint64(thread_id), ctypes.byref(info), size
            )
            if got == size:
                threads[thread_id] = (info.pth_user_time + info.pth_system_time) / NANOSECONDS
        if not known:
            return OwnUsage(thread_seconds=threads)
        return OwnUsage(
            footprint_bytes=usage.ri_phys_footprint,
            resident_bytes=usage.ri_resident_size,
            read_bytes=usage.ri_diskio_bytesread,
            written_bytes=usage.ri_diskio_byteswritten,
            thread_seconds=threads,
        )


# -- Linux: /proc/self ----------------------------------------------------------------

KIBIBYTE = 1024
# In a stat line, after the name in brackets: the 12th and 13th fields.
UTIME, STIME = 11, 12


def _ticks_per_second() -> float:
    return float(os.sysconf("SC_CLK_TCK"))


class LinuxUsageReader:
    """From the files the kernel keeps under ``/proc/self``."""

    def __init__(
        self,
        *,
        root: Path = Path("/proc/self"),
        ticks: Callable[[], float] = _ticks_per_second,
    ) -> None:
        self._root = root
        self._ticks = ticks

    def _text(self, name: str) -> str | None:
        try:
            return (self._root / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _fields(self, name: str) -> dict[str, int]:
        """The ``name: number`` lines of a file; a line that is anything else is left out."""
        found = {}
        for line in (self._text(name) or "").splitlines():
            key, _, rest = line.partition(":")
            value = rest.split()
            if value and value[0].isdigit():
                found[key.strip()] = int(value[0])
        return found

    def _thread_seconds(self, thread_id: int) -> float | None:
        line = self._text(f"task/{thread_id}/stat")
        if line is None:
            return None
        # The name may hold spaces and brackets; the numbers follow its last bracket.
        after = line.rpartition(")")[2].split()
        try:
            return (int(after[UTIME]) + int(after[STIME])) / self._ticks()
        except (IndexError, ValueError):
            return None

    def read(self, thread_ids: Iterable[int] = ()) -> OwnUsage:
        status, io = self._fields("status"), self._fields("io")
        resident = status.get("VmRSS")
        threads = {}
        for thread_id in thread_ids:
            seconds = self._thread_seconds(thread_id)
            if seconds is not None:
                threads[thread_id] = seconds
        return OwnUsage(
            footprint_bytes=None
            if resident is None
            else (resident + status.get("VmSwap", 0)) * KIBIBYTE,
            resident_bytes=None if resident is None else resident * KIBIBYTE,
            read_bytes=io.get("read_bytes"),
            written_bytes=io.get("write_bytes"),
            thread_seconds=threads,
        )
