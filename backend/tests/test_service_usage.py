"""What the service itself uses: read from the system, sampled, kept and served.

The readers for both operating systems are tested on whichever one runs the tests.
"""

import ctypes
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from agdash import cli
from agdash import service_usage as usage_module
from agdash.config import get_settings
from agdash.db.migrate import upgrade
from agdash.db.models import RawChunk, ServiceUsage
from agdash.main import create_app
from agdash.platform import Host, OperatingSystem, current_host
from agdash.platform.usage import (
    PROC_PIDTHREADID64INFO,
    RUSAGE_INFO_V2,
    LinuxUsageReader,
    MacUsageReader,
    OwnUsage,
)
from agdash.runtime import writer_engine
from agdash.service_usage import HOUR, MINUTE, Sample, Sampler, part_of

TOKEN = "usage-test-token"
NOON = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
MB = 1_000_000


@pytest.fixture
def url(tmp_path) -> str:
    database = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(database)
    return database


@pytest.fixture
def store(url):
    engine = writer_engine(url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def settings_as_found():
    yield
    get_settings.cache_clear()


def client(url: str, sampler: Sampler | None = None) -> TestClient:
    return TestClient(
        create_app(url, token=TOKEN, port=8765, usage=sampler),
        base_url="http://127.0.0.1:8765",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


# -- the readers ----------------------------------------------------------------------


class FakeLibproc:
    """Answers as libproc does, for one process with two threads that still run."""

    def __init__(self, *, refuses: bool = False) -> None:
        self.refuses = refuses
        self.asked: list[tuple[int, int]] = []

    def proc_pid_rusage(self, pid, flavour, reference) -> int:
        self.asked.append((pid, flavour))
        if self.refuses:
            return -1
        info = reference._obj
        info.ri_phys_footprint = 150 * MB
        info.ri_resident_size = 40 * MB
        info.ri_diskio_bytesread = 7 * MB
        info.ri_diskio_byteswritten = 3 * MB
        return 0

    def proc_pidinfo(self, pid, flavour, argument, reference, size) -> int:
        self.asked.append((pid, flavour))
        seconds = {11: (2.0, 0.5), 12: (0.25, 0.0)}.get(argument.value)
        if seconds is None:
            return 0  # the thread has ended
        info = reference._obj
        info.pth_user_time = int(seconds[0] * 1e9)
        info.pth_system_time = int(seconds[1] * 1e9)
        return ctypes.sizeof(info)


def test_macos_reads_footprint_storage_traffic_and_thread_time():
    library = FakeLibproc()
    reader = MacUsageReader(library=lambda: library, pid=lambda: 4242)
    found = reader.read([11, 12, 13])
    assert found == OwnUsage(
        footprint_bytes=150 * MB,
        resident_bytes=40 * MB,
        read_bytes=7 * MB,
        written_bytes=3 * MB,
        thread_seconds={11: 2.5, 12: 0.25},
    )
    assert {pid for pid, _ in library.asked} == {4242}, "only ever its own process"
    assert {flavour for _, flavour in library.asked} == {RUSAGE_INFO_V2, PROC_PIDTHREADID64INFO}


def test_macos_reports_nothing_it_was_refused_as_zero():
    reader = MacUsageReader(library=lambda: FakeLibproc(refuses=True), pid=lambda: 4242)
    found = reader.read([11])
    assert found.footprint_bytes is None and found.read_bytes is None
    assert found.thread_seconds == {11: 2.5}


def _proc(tmp_path: Path) -> Path:
    root = tmp_path / "self"
    (root / "task" / "21").mkdir(parents=True)
    (root / "task" / "22").mkdir()
    (root / "status").write_text(
        "Name:\tpython\nVmRSS:\t   40000 kB\nVmSwap:\t    1000 kB\nThreads:\t2\n"
    )
    (root / "io").write_text("rchar: 99\nread_bytes: 7000000\nwrite_bytes: 3000000\n")
    # A name may hold spaces and brackets; the numbers follow its last bracket.
    (root / "task" / "21" / "stat").write_text(
        "21 (a (odd) name) S 1 1 1 0 -1 0 0 0 0 0 200 50 0 0 20 0 2 0 1 0 0\n"
    )
    (root / "task" / "22" / "stat").write_text("22 (worker) S 1 1 1 0 -1 0 0 0 0 0 25 0 0\n")
    return root


def test_linux_reads_the_same_from_proc(tmp_path):
    reader = LinuxUsageReader(root=_proc(tmp_path), ticks=lambda: 100.0)
    assert reader.read([21, 22, 23]) == OwnUsage(
        footprint_bytes=41000 * 1024,
        resident_bytes=40000 * 1024,
        read_bytes=7_000_000,
        written_bytes=3_000_000,
        thread_seconds={21: 2.5, 22: 0.25},
    )


def test_linux_reports_what_it_cannot_read_as_unknown(tmp_path):
    root = _proc(tmp_path)
    (root / "io").unlink()
    (root / "status").write_text("Name:\tpython\n")
    (root / "task" / "22" / "stat").write_text("22 (worker) S\n")
    found = LinuxUsageReader(root=root, ticks=lambda: 100.0).read([21, 22])
    assert (found.footprint_bytes, found.resident_bytes) == (None, None)
    assert (found.read_bytes, found.written_bytes) == (None, None)
    assert found.thread_seconds == {21: 2.5}


def test_each_operating_system_gets_its_own_reader(tmp_path):
    assert isinstance(Host(OperatingSystem.MACOS, tmp_path, {}).usage_reader(), MacUsageReader)
    assert isinstance(Host(OperatingSystem.LINUX, tmp_path, {}).usage_reader(), LinuxUsageReader)


def test_this_process_can_be_read_for_real():
    spin = threading.Event()

    def work() -> None:
        while not spin.is_set():
            sum(range(2000))

    worker = threading.Thread(target=work, name="synthetic-worker", daemon=True)
    worker.start()
    try:
        reader = current_host().usage_reader()
        first = reader.read([worker.native_id])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            time.sleep(0.05)
            second = reader.read([worker.native_id])
            if second.thread_seconds[worker.native_id] > first.thread_seconds[worker.native_id]:
                break
    finally:
        spin.set()
        worker.join(timeout=5)
    assert second.thread_seconds[worker.native_id] > first.thread_seconds[worker.native_id]
    assert second.footprint_bytes and second.footprint_bytes >= 1_000_000
    assert second.resident_bytes and second.resident_bytes >= 1_000_000


# -- sampling -------------------------------------------------------------------------


class Machine:
    """A clock, a CPU counter and a reader that say what a test sets."""

    def __init__(self) -> None:
        self.now = NOON
        self.cpu = 0.0
        self.names: dict[int, str] = {1: "MainThread", 2: "agdash-probe", 3: "agdash-ingest"}
        self.threads: dict[int, float] = {1: 0.0, 2: 0.0, 3: 0.0}
        self.footprint: int | None = 100 * MB
        self.read: int | None = 0
        self.written: int | None = 0

    def passes(self, seconds: float, *, cpu: float = 0.0, **threads: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.cpu += cpu
        for name, spent in threads.items():
            thread_id = int(name.removeprefix("t"))
            self.threads[thread_id] = self.threads.get(thread_id, 0.0) + spent

    def read_usage(self, thread_ids=()) -> OwnUsage:
        return OwnUsage(
            footprint_bytes=self.footprint,
            resident_bytes=None if self.footprint is None else self.footprint // 3,
            read_bytes=self.read,
            written_bytes=self.written,
            thread_seconds={i: self.threads[i] for i in thread_ids if i in self.threads},
        )

    def sampler(self, store, store_file: Path | None = None) -> Sampler:
        class Reader:
            read = staticmethod(self.read_usage)

        return Sampler(
            store,
            Reader(),
            store_file=store_file,
            clock=lambda: self.now,
            cpu=lambda: self.cpu,
            threads=lambda: dict(self.names),
        )


def test_threads_are_grouped_by_what_they_do():
    assert part_of("agdash-probe") == "probe"
    assert part_of("MainThread") == "api"
    assert part_of("AnyIO worker thread") == "api"
    assert part_of("agdash-usage") == "monitor"
    assert part_of("something-else") == "other"


def test_a_sample_is_what_changed_since_the_one_before(store, tmp_path):
    machine = Machine()
    database = tmp_path / "agdash.db"
    sampler = machine.sampler(store, database)
    assert sampler.sample() is None, "the first reading has nothing to be counted from"

    machine.passes(10, cpu=1.0, t2=0.5, t3=0.25, t1=0.125)
    machine.read, machine.written, machine.footprint = 5 * MB, 2 * MB, 120 * MB
    taken = sampler.sample()
    assert taken is not None
    assert (taken.at, taken.seconds, taken.cpu_seconds) == (machine.now, 10.0, 1.0)
    assert taken.parts == {"probe": 0.5, "ingest": 0.25, "api": 0.125, "other": 0.125}
    assert (taken.read_bytes, taken.written_bytes) == (5 * MB, 2 * MB)
    assert (taken.footprint_bytes, taken.resident_bytes) == (120 * MB, 40 * MB)
    wal = database.with_name("agdash.db-wal")
    assert taken.store_bytes == database.stat().st_size + (
        wal.stat().st_size if wal.exists() else 0
    )
    assert taken.free_bytes and taken.free_bytes > 0
    assert sampler.recent() == [taken]


def test_a_thread_id_used_again_starts_from_nothing(store):
    machine = Machine()
    sampler = machine.sampler(store)
    machine.threads[2] = 40.0
    sampler.sample()
    # The probe thread ended and its id went to a new thread, 0.3 s old.
    machine.names[2], machine.threads[2] = "agdash-mirror", 0.3
    machine.passes(10, cpu=0.3)
    taken = sampler.sample()
    assert taken is not None and taken.parts == {"mirror": 0.3}


def test_a_counter_that_is_unknown_or_went_back_is_not_a_number(store):
    machine = Machine()
    sampler = machine.sampler(store)
    machine.read, machine.written = 9 * MB, None
    sampler.sample()
    machine.passes(10)
    machine.read, machine.footprint = 1 * MB, None
    taken = sampler.sample()
    assert taken is not None
    assert (taken.read_bytes, taken.written_bytes) == (None, None)
    assert (taken.footprint_bytes, taken.store_bytes, taken.free_bytes) == (None, None, None)
    assert taken.parts == {}


def rows(store, period: str) -> list[ServiceUsage]:
    with store() as db:
        return list(
            db.scalars(
                select(ServiceUsage)
                .where(ServiceUsage.period == period)
                .order_by(ServiceUsage.started_at)
            )
        )


def run_for(machine: Machine, sampler: Sampler, samples: int, **each) -> None:
    for _ in range(samples):
        machine.passes(10, **each)
        machine.read = (machine.read or 0) + MB
        taken = sampler.sample()
        assert taken is not None
        sampler.keep(taken)


def test_each_minute_is_kept_once_it_is_over(store):
    machine = Machine()
    sampler = machine.sampler(store)
    sampler.sample()
    run_for(machine, sampler, 6, cpu=0.5, t2=0.5)  # 12:00:10 to 12:01:00
    assert [(row.started_at, row.seconds) for row in rows(store, MINUTE)] == [(NOON, 50.0)]
    run_for(machine, sampler, 6, cpu=0.5, t2=0.5)
    kept = rows(store, MINUTE)
    assert [row.started_at for row in kept] == [NOON, NOON + timedelta(minutes=1)]
    assert kept[1].seconds == 60.0 and kept[1].cpu_seconds == 3.0
    assert kept[1].parts == {"probe": 3.0}
    assert kept[1].read_bytes == 6 * MB and kept[1].footprint_bytes == 100 * MB


def test_a_minute_the_service_stopped_and_started_in_is_one_row(store):
    machine = Machine()
    first = machine.sampler(store)
    first.sample()
    run_for(machine, first, 2, cpu=0.5, t2=0.5)
    first.flush()  # as the service does when it stops
    second = machine.sampler(store)
    second.sample()
    run_for(machine, second, 2, cpu=0.25, t3=0.25)
    second.flush()
    (minute,) = rows(store, MINUTE)
    assert minute.seconds == 40.0 and minute.cpu_seconds == 1.5
    assert minute.parts == {"probe": 1.0, "ingest": 0.5}


def test_finished_hours_are_folded_and_old_rows_dropped(store):
    machine = Machine()
    sampler = machine.sampler(store)
    with store.begin() as db:
        for started_at in (NOON - timedelta(hours=49), NOON - timedelta(days=91)):
            db.add(
                ServiceUsage(
                    period=MINUTE if started_at > NOON - timedelta(days=3) else HOUR,
                    started_at=started_at,
                    seconds=60.0,
                    cpu_seconds=1.0,
                    parts={},
                )
            )
    sampler.sample()
    run_for(machine, sampler, 6 * 61, cpu=0.1, t2=0.1)  # through 13:01
    hours = rows(store, HOUR)
    assert [row.started_at for row in hours] == [NOON - timedelta(hours=49), NOON]
    assert hours[1].seconds == pytest.approx(3590.0)
    assert hours[1].cpu_seconds == pytest.approx(35.9)
    assert hours[1].read_bytes == 359 * MB
    assert hours[0].cpu_seconds == 1.0, "an hour made of minutes that have since gone stays"
    assert min(row.started_at for row in rows(store, MINUTE)) == NOON
    sampler.fold(machine.now)
    assert len(rows(store, HOUR)) == 2, "folding again adds nothing"


def test_the_sampler_runs_and_stops_with_the_service(store, monkeypatch):
    monkeypatch.setattr(usage_module, "SAMPLE_SECONDS", 0.02)
    sampler = Sampler(store, current_host().usage_reader())
    stop = threading.Event()
    threads = sampler.run(stop)
    try:
        assert [thread.name for thread in threads] == ["agdash-usage"]
        deadline = time.monotonic() + 5
        while len(sampler.recent()) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(sampler.recent()) >= 3
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    with store() as db:
        assert db.scalar(select(func.count()).select_from(ServiceUsage)) >= 1, "kept on stopping"


# -- the API --------------------------------------------------------------------------


def minute(started_at: datetime, *, cpu: float = 3.0, **values) -> ServiceUsage:
    return ServiceUsage(
        period=values.pop("period", MINUTE),
        started_at=started_at,
        seconds=values.pop("seconds", 60.0),
        cpu_seconds=cpu,
        parts=values.pop("parts", {"probe": cpu}),
        **values,
    )


def this_minute() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0)


def test_without_a_sampler_the_kept_rows_are_served(url, store):
    latest = this_minute() - timedelta(minutes=2)
    with store.begin() as db:
        db.add(minute(latest - timedelta(minutes=1), cpu=6.0, footprint_bytes=90 * MB))
        db.add(
            minute(
                latest,
                cpu=3.0,
                parts={"probe": 2.0, "ingest": 1.0},
                footprint_bytes=120 * MB,
                read_bytes=60 * MB,
                written_bytes=None,
                store_bytes=5000 * MB,
                free_bytes=90_000 * MB,
            )
        )
    with client(url) as api:
        report = api.get("/api/service/usage").json()
        day = api.get("/api/service/usage", params={"range": "day"}).json()
    assert report["known"] is False and report["since"] is None
    assert (report["range"], report["step_seconds"], len(report["points"])) == ("hour", 60, 60)
    measured = [point for point in report["points"] if point["cpu_percent"] is not None]
    assert [point["cpu_percent"] for point in measured] == [10.0, 5.0]
    assert measured[1]["read_bytes_per_second"] == MB
    assert measured[1]["written_bytes_per_second"] is None, "unknown is not zero"
    assert report["measured_seconds"] == 120.0
    assert report["parts"] == [
        {"part": "probe", "cpu_seconds": 8.0, "cpu_percent": pytest.approx(100 * 8 / 120)},
        {"part": "ingest", "cpu_seconds": 1.0, "cpu_percent": pytest.approx(100 * 1 / 120)},
    ]
    assert report["now"]["footprint_bytes"] == 120 * MB
    assert report["now"]["free_bytes"] == 90_000 * MB
    assert (day["step_seconds"], len(day["points"])) == (300, 288)
    assert sum(point["cpu_percent"] is not None for point in day["points"]) in (1, 2)
    assert max(point["footprint_bytes"] or 0 for point in day["points"]) == 120 * MB


def test_ninety_days_come_from_hours_and_the_minutes_not_yet_folded(url, store):
    hour = this_minute().replace(minute=0)
    with store.begin() as db:
        db.add(minute(hour - timedelta(days=3), period=HOUR, seconds=3600.0, cpu=36.0))
        db.add(minute(hour - timedelta(hours=1), period=HOUR, seconds=3600.0, cpu=72.0))
        db.add(minute(hour - timedelta(minutes=30), cpu=60.0))  # already in the hour above
        db.add(minute(hour, cpu=30.0))
    with client(url) as api:
        report = api.get("/api/service/usage", params={"range": "quarter"}).json()
    assert (report["step_seconds"], len(report["points"])) == (21600, 360)
    assert report["measured_seconds"] == 3600 + 3600 + 60
    assert report["parts"][0]["cpu_seconds"] == 36.0 + 72.0 + 30.0


def test_with_a_sampler_the_last_hour_is_its_samples(url, store):
    sampler = Sampler(store, current_host().usage_reader())
    now = datetime.now(UTC)
    for back in (30, 20, 10):
        sampler._recent.append(
            Sample(
                at=now - timedelta(seconds=back - 10),
                seconds=10.0,
                cpu_seconds=0.5,
                parts={"probe": 0.3, "api": 0.2},
                footprint_bytes=(200 - back) * MB,
                read_bytes=10 * MB,
                written_bytes=0,
                store_bytes=4000 * MB,
            )
        )
    with client(url, sampler) as api:
        report = api.get("/api/service/usage", params={"range": "hour"}).json()
        day = api.get("/api/service/usage", params={"range": "day"}).json()
    assert report["known"] is True and report["since"] is not None
    assert (report["step_seconds"], len(report["points"])) == (10, 360)
    measured = [point for point in report["points"] if point["cpu_percent"] is not None]
    assert len(measured) == 3 and all(point["cpu_percent"] == 5.0 for point in measured)
    assert measured[-1]["written_bytes_per_second"] == 0, "a measured nothing is zero"
    assert report["points"][0]["cpu_percent"] is None, "nothing measured is not zero"
    assert [part["part"] for part in report["parts"]] == ["probe", "api"]
    assert report["now"]["footprint_bytes"] == 190 * MB
    assert day["now"]["footprint_bytes"] == 190 * MB, "now is live whatever the range"
    assert day["measured_seconds"] == 0


def test_usage_is_read_only_and_needs_the_token(url):
    with client(url) as api:
        assert api.get("/api/service/usage").status_code == 200
        assert api.get("/api/service/usage", params={"range": "year"}).status_code == 422
        for method in ("post", "put", "patch", "delete"):
            assert getattr(api, method)("/api/service/usage").status_code == 405
        assert api.get("/api/service/usage", headers={"Authorization": ""}).status_code == 401
        empty = api.get("/api/service/usage").json()
    assert empty["now"] is None and empty["parts"] == []
    assert all(point["cpu_percent"] is None for point in empty["points"])


def test_the_store_is_given_by_what_fills_it(url, store, tmp_path):
    with store.begin() as db:
        db.add(
            RawChunk(
                harness="pi",
                source_file="/files/synthetic.jsonl",
                first_offset=0,
                end_offset=10,
                line_count=1,
                data=b"x" * 5000,
            )
        )
    with client(url) as api:
        parts = api.get("/api/storage/parts").json()
    assert parts["archive_bytes"] == 5000
    assert parts["search_bytes"] is not None and parts["search_bytes"] >= 0
    assert parts["free_bytes"] is not None and parts["free_bytes"] >= 0
    assert parts["bytes"] >= parts["archive_bytes"] + parts["search_bytes"] + parts["free_bytes"]
    assert parts["rest_bytes"] == parts["bytes"] - sum(
        parts[name] for name in ("archive_bytes", "search_bytes", "free_bytes")
    )


# -- the command ----------------------------------------------------------------------


def test_the_command_prints_what_the_service_says(tmp_path, monkeypatch):
    host = Host(OperatingSystem.LINUX, tmp_path, {})
    report = {
        "since": "2026-11-17T11:00:00Z",
        "measured_seconds": 3540.0,
        "now": {
            "at": "2026-11-17T12:00:00Z",
            "cpu_percent": 5.7,
            "footprint_bytes": 157 * MB,
            "resident_bytes": 48 * MB,
            "read_bytes_per_second": 1.04 * MB,
            "written_bytes_per_second": None,
            "store_bytes": 7720 * MB,
            "free_bytes": 312_000 * MB,
        },
        "parts": [
            {"part": "probe", "cpu_seconds": 110.0, "cpu_percent": 3.1},
            {"part": "ingest", "cpu_seconds": 78.0, "cpu_percent": 2.2},
        ],
    }
    asked = []
    monkeypatch.setattr(cli, "_health", lambda: {"status": "ok"})
    monkeypatch.setattr(cli, "_service_usage", lambda token: asked.append(token) or report)
    monkeypatch.setattr("agdash.api.security.load_or_create_token", lambda secrets: "local-token")
    monkeypatch.setattr(Host, "secret_store", lambda self, **options: object())
    lines: list[str] = []
    assert cli.usage(host, out=lines.append) == 0
    text = "\n".join(lines)
    assert "5.7% of one core" in text
    assert "157 MB (resident 48.0 MB)" in text
    assert "read 1.0 MB/s, written not reported" in text
    assert "7.7 GB, 312 GB free" in text
    assert "probe      3.1% of one core" in text and "59 minutes" in text
    assert asked == ["local-token"] and "local-token" not in text


def test_the_command_says_when_nothing_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_health", lambda: None)
    said: list[str] = []
    assert cli.usage(Host(OperatingSystem.LINUX, tmp_path, {}), err=said.append) == 1
    assert "not answering" in said[0]
