"""What the service itself costs this machine, sampled as it runs.

Every ten seconds the sampler asks the operating system about its own process:
CPU time, in all and for each of the service's threads, memory, and the bytes read
from and written to storage. It also notes how large the store is and how much
room its volume has left. The last hour of samples stays in memory; each minute
is kept as a row, minutes are folded into hours, and old rows are dropped, so the
record stays small however long the service runs.

Nothing here looks at a harness, an agent or any other process.
"""

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.db.models import ServiceUsage
from agdash.platform.usage import OwnUsage, UsageReader

log = logging.getLogger("agdash.usage")

SAMPLE_SECONDS = 10.0
RECENT_SAMPLES = 360  # an hour of them
MINUTE, HOUR = "minute", "hour"
KEEP = {MINUTE: timedelta(hours=48), HOUR: timedelta(days=90)}

OTHER = "other"
# The service's threads by what they do. A thread not named here, and CPU time no
# thread of ours accounts for, counts as "other".
PARTS = {
    "agdash-ingest": "ingest",
    "agdash-probe": "probe",
    "agdash-watch": "watch",
    "agdash-mirror": "mirror",
    "agdash-retention": "retention",
    "agdash-usage": "monitor",
    "agdash-webhook": "api",
    "MainThread": "api",
}
API_THREADS = ("AnyIO worker thread", "asyncio_")


def part_of(thread_name: str) -> str:
    if thread_name in PARTS:
        return PARTS[thread_name]
    return "api" if thread_name.startswith(API_THREADS) else OTHER


@dataclass(frozen=True)
class Sample:
    """What the service used over ``seconds`` ending at ``at``."""

    at: datetime
    seconds: float
    cpu_seconds: float
    parts: Mapping[str, float]
    footprint_bytes: int | None = None
    resident_bytes: int | None = None
    read_bytes: int | None = None
    written_bytes: int | None = None
    store_bytes: int | None = None
    free_bytes: int | None = None

    @property
    def started_at(self) -> datetime:
        return self.at - timedelta(seconds=self.seconds)


@dataclass(frozen=True)
class _Reading:
    at: datetime
    cpu_seconds: float
    usage: OwnUsage


def own_threads() -> dict[int, str]:
    """This process's Python threads by the id the operating system knows them by."""
    return {
        thread.native_id: thread.name
        for thread in threading.enumerate()
        if thread.native_id is not None
    }


def store_size(path: Path | None) -> int | None:
    """A store file with its write-ahead log, which holds what is not yet folded in."""
    if path is None:
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    try:
        return size + path.with_name(f"{path.name}-wal").stat().st_size
    except OSError:
        return size


def _room(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        volume = os.statvfs(path.parent)
    except OSError:
        return None
    return volume.f_bavail * volume.f_frsize


def _grown(now: int | None, before: int | None) -> int | None:
    """How far a counter moved; unknown when either end is, or when it went backwards."""
    if now is None or before is None or now < before:
        return None
    return now - before


def _sum(values: Iterable[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _largest(values: Iterable[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _last(values: Iterable[int | None]) -> int | None:
    known = [value for value in values if value is not None]
    return known[-1] if known else None


def _minute(moment: datetime) -> datetime:
    return moment.replace(second=0, microsecond=0)


def _hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


class Sampler:
    """Samples the service's own process, and keeps what it found."""

    def __init__(
        self,
        sessions: sessionmaker[DbSession],
        reader: UsageReader,
        *,
        store_file: Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cpu: Callable[[], float] = time.process_time,
        threads: Callable[[], Mapping[int, str]] = own_threads,
    ) -> None:
        self._sessions = sessions
        self._reader = reader
        self._store_file = store_file
        self._clock = clock
        self._cpu = cpu
        self._threads = threads
        self._lock = threading.Lock()
        self._recent: deque[Sample] = deque(maxlen=RECENT_SAMPLES)
        self._before: _Reading | None = None
        self._names: Mapping[int, str] = {}
        self._pending: list[Sample] = []
        self.since = clock()

    # -- what the API reads -----------------------------------------------------------

    def recent(self) -> list[Sample]:
        """The last hour's samples, oldest first."""
        with self._lock:
            return list(self._recent)

    # -- sampling ---------------------------------------------------------------------

    def sample(self) -> Sample | None:
        """Read the process once. The first reading only sets what the next is counted from."""
        names = self._threads()
        now = _Reading(self._clock(), self._cpu(), self._reader.read(names))
        before, self._before = self._before, now
        earlier_names, self._names = self._names, names
        if before is None:
            return None
        seconds = (now.at - before.at).total_seconds()
        if seconds <= 0:
            return None
        cpu = max(0.0, now.cpu_seconds - before.cpu_seconds)
        parts: dict[str, float] = {}
        for thread_id, spent in now.usage.thread_seconds.items():
            # An id is used again once its thread has ended; a thread under another name
            # is a new one, and all of its time so far falls in this stretch.
            same = earlier_names.get(thread_id) == names.get(thread_id)
            earlier = before.usage.thread_seconds.get(thread_id, 0.0) if same else 0.0
            part = part_of(names.get(thread_id, ""))
            parts[part] = parts.get(part, 0.0) + max(0.0, spent - earlier)
        unaccounted = cpu - sum(parts.values())
        if unaccounted > 0:
            parts[OTHER] = parts.get(OTHER, 0.0) + unaccounted
        taken = Sample(
            at=now.at,
            seconds=seconds,
            cpu_seconds=cpu,
            parts={part: round(spent, 6) for part, spent in parts.items() if spent > 0},
            footprint_bytes=now.usage.footprint_bytes,
            resident_bytes=now.usage.resident_bytes,
            read_bytes=_grown(now.usage.read_bytes, before.usage.read_bytes),
            written_bytes=_grown(now.usage.written_bytes, before.usage.written_bytes),
            store_bytes=store_size(self._store_file),
            free_bytes=_room(self._store_file),
        )
        with self._lock:
            self._recent.append(taken)
        return taken

    # -- keeping ----------------------------------------------------------------------

    def keep(self, taken: Sample) -> None:
        """Hold a sample for its minute's row; write the minute before once it is over."""
        if self._pending and _minute(self._pending[0].at) != _minute(taken.at):
            finished = _hour(self._pending[0].at) != _hour(taken.at)
            self.flush()
            if finished:
                self.fold(taken.at)
        self._pending.append(taken)

    def flush(self) -> None:
        """Write the samples held so far into their minute's row."""
        pending, self._pending = self._pending, []
        if not pending:
            return
        with self._sessions.begin() as db:
            _add(db, MINUTE, _minute(pending[0].at), pending)

    def fold(self, now: datetime) -> None:
        """Make an hour's row of each finished hour's minutes, and drop rows past their time."""
        with self._sessions.begin() as db:
            done = set(db.scalars(select(ServiceUsage.started_at).where(_is(HOUR))))
            minutes = db.scalars(
                select(ServiceUsage)
                .where(_is(MINUTE), ServiceUsage.started_at < _hour(now))
                .order_by(ServiceUsage.started_at)
            ).all()
            hours: dict[datetime, list[ServiceUsage]] = {}
            for row in minutes:
                if _hour(row.started_at) not in done:
                    hours.setdefault(_hour(row.started_at), []).append(row)
            for started_at, rows in hours.items():
                _add(db, HOUR, started_at, rows)
            for period, kept in KEEP.items():
                db.execute(
                    delete(ServiceUsage).where(_is(period), ServiceUsage.started_at < now - kept)
                )

    def run(self, stop: threading.Event) -> list[threading.Thread]:
        def loop() -> None:
            try:
                self.fold(self._clock())
            except Exception:
                log.exception("could not fold earlier usage into hours; the next hour retries")
            while not stop.is_set():
                try:
                    taken = self.sample()
                    if taken is not None:
                        self.keep(taken)
                except Exception:
                    log.exception("a usage sample failed; the next one retries")
                stop.wait(SAMPLE_SECONDS)
            try:
                self.flush()
            except Exception:
                log.exception("could not keep the last usage samples")

        thread = threading.Thread(target=loop, name="agdash-usage", daemon=True)
        thread.start()
        return [thread]


def _is(period: str):
    return ServiceUsage.period == period


def _add(db: DbSession, period: str, started_at: datetime, pieces: Iterable) -> None:
    """Add samples or rows to the row of their period, which may hold some already.

    A minute has a row already when the service stopped and started again within it.
    """
    pieces = list(pieces)
    row = db.scalars(
        select(ServiceUsage).where(_is(period), ServiceUsage.started_at == started_at)
    ).one_or_none()
    if row is None:
        row = ServiceUsage(
            period=period, started_at=started_at, seconds=0.0, cpu_seconds=0.0, parts={}
        )
        db.add(row)
    parts = dict(row.parts or {})
    for piece in pieces:
        for part, spent in piece.parts.items():
            parts[part] = round(parts.get(part, 0.0) + spent, 6)
    row.seconds += sum(piece.seconds for piece in pieces)
    row.cpu_seconds += sum(piece.cpu_seconds for piece in pieces)
    row.parts = parts
    row.footprint_bytes = _largest([row.footprint_bytes, *(p.footprint_bytes for p in pieces)])
    row.resident_bytes = _largest([row.resident_bytes, *(p.resident_bytes for p in pieces)])
    row.read_bytes = _sum([row.read_bytes, *(p.read_bytes for p in pieces)])
    row.written_bytes = _sum([row.written_bytes, *(p.written_bytes for p in pieces)])
    row.store_bytes = _last([row.store_bytes, *(p.store_bytes for p in pieces)])
    row.free_bytes = _last([row.free_bytes, *(p.free_bytes for p in pieces)])
