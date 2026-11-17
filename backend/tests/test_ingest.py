"""The ingest engine: each complete line once, across re-runs, moves, rewrites and crashes.

Files are synthetic. Lines go into agdash's own database through a test writer
that stores one event per line, keyed by the line's byte offset, as the
harness adapters will.
"""

import io
import os
import shutil
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.access.guard import assert_untouched
from agdash.db.migrate import upgrade
from agdash.db.models import Event, IngestCursor, Session
from agdash.ingest import Cursor, Ingestor, discover_files, match_files, pattern_folders
from agdash.ingest.compressed import UnreadableArchive, compress, decompress
from agdash.ingest.engine import split_lines
from agdash.ingest.ledger import SqlLedger
from agdash.ingest.watch import run
from agdash.vocab import EventKind

HARNESS = "codex"
NAME = "rollout-2026-01-02T03-04-05-0b5f6c1e-7a2d-4c3b-9e1f-2d3c4b5a6f70.jsonl"
PATTERNS = ["sessions/*/*/*/rollout-*.jsonl", "archived_sessions/rollout-*.jsonl"]


def record(n: int) -> bytes:
    return b'{"type":"event","n":%d}\n' % n


def write_lines(session, cursor: Cursor, lines) -> None:
    """One event per line, keyed by the file name and the line's offset: idempotent."""
    native_id = os.path.basename(cursor.path)
    owner = session.scalars(
        select(Session).where(Session.harness == cursor.harness, Session.native_id == native_id)
    ).one_or_none()
    if owner is None:
        owner = Session(harness=cursor.harness, native_id=native_id)
        session.add(owner)
        session.flush()
    known = set(session.scalars(select(Event.sequence).where(Event.session_id == owner.id)))
    for line in lines:
        if line.offset in known:
            continue
        session.add(
            Event(
                session_id=owner.id,
                sequence=line.offset,
                kind=EventKind.STATUS,
                payload={"status": "line", "extra": {"text": line.data.decode()}},
            )
        )


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def home(tmp_path) -> Path:
    root = tmp_path / "codex-home"
    (root / "sessions/2026/01/02").mkdir(parents=True)
    (root / "archived_sessions").mkdir()
    return root


def make(store, home, writer=write_lines, **options):
    access = ReadOnlyAccess([home], snapshot_dir=home.parent / "snapshots-elsewhere")
    ingestor = Ingestor(access, SqlLedger(store, writer), harness=HARNESS, **options)
    return access, ingestor


def scan(access, ingestor, home):
    return ingestor.scan(discover_files(access, [home], PATTERNS))


def texts(store) -> list[str]:
    with store() as session:
        rows = session.execute(select(Event.payload).order_by(Event.sequence)).scalars()
        return [payload["extra"]["text"] for payload in rows]


def cursors(store) -> list[tuple[str, int]]:
    with store() as session:
        return [
            (
                os.path.relpath(c.path, c.path.rsplit("/codex-home/", 1)[0] + "/codex-home"),
                c.byte_offset,
            )
            for c in session.scalars(select(IngestCursor).order_by(IngestCursor.path))
        ]


# -- lines --------------------------------------------------------------------------


def test_lines_are_split_at_newlines_and_a_tail_waits():
    lines, end = split_lines(b"a\nbb\nccc", 100)
    assert [(line.offset, line.end, line.data) for line in lines] == [
        (100, 102, b"a"),
        (102, 105, b"bb"),
    ]
    assert end == 105
    assert split_lines(b"", 7) == ([], 7)
    assert split_lines(b"no newline", 0) == ([], 0)
    assert [line.data for line in split_lines(b"\n\n", 0)[0]] == [b"", b""]


def test_discovery_follows_the_patterns(home):
    (home / "sessions/2026/01/02" / NAME).write_bytes(record(1))
    (home / "archived_sessions" / NAME.replace("05-0b5", "06-0b5")).write_bytes(record(1))
    (home / "sessions/2026/01/02/notes.txt").write_text("x")
    (home / "session_index.jsonl").write_text("{}\n")
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    found = [os.path.relpath(p, home.resolve()) for p in discover_files(access, [home], PATTERNS)]
    assert found == [
        "archived_sessions/" + NAME.replace("05-0b5", "06-0b5"),
        "sessions/2026/01/02/" + NAME,
    ]
    assert discover_files(access, [home / "absent"], PATTERNS) == []


# -- acceptance: re-runs, partial lines, moves ---------------------------------------


def test_rerunning_ingest_over_the_same_files_adds_no_rows(store, home):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1) + record(2) + record(3))
    access, ingestor = make(store, home)

    assert scan(access, ingestor, home).lines == 3
    assert scan(access, ingestor, home).lines == 0
    assert len(texts(store)) == 3

    # A backfill from nothing: every cursor gone, everything read again, nothing added.
    with store.begin() as session:
        session.query(IngestCursor).delete()
    assert scan(access, ingestor, home).lines == 3
    assert len(texts(store)) == 3
    assert cursors(store) == [("sessions/2026/01/02/" + NAME, path.stat().st_size)]


def test_a_partial_last_line_is_ingested_once_when_complete(store, home):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1) + record(2) + b'{"type":"event","n":3')
    access, ingestor = make(store, home)

    report = scan(access, ingestor, home)
    assert report.lines == 2
    assert report.files[0].waiting_bytes == len(b'{"type":"event","n":3')
    assert texts(store) == ['{"type":"event","n":1}', '{"type":"event","n":2}']

    with open(path, "ab") as target:
        target.write(b"}\n")
    assert scan(access, ingestor, home).lines == 1
    assert scan(access, ingestor, home).lines == 0
    assert texts(store)[-1] == '{"type":"event","n":3}'
    assert len(texts(store)) == 3


def test_a_file_moved_to_the_archive_mid_run_is_neither_lost_nor_doubled(store, home):
    live = home / "sessions/2026/01/02" / NAME
    archived = home / "archived_sessions" / NAME
    live.write_bytes(record(1) + record(2))
    access, ingestor = make(store, home)
    assert scan(access, ingestor, home).lines == 2

    with open(live, "ab") as target:
        target.write(record(3))
    os.rename(live, archived)
    with open(archived, "ab") as target:
        target.write(record(4))

    report = scan(access, ingestor, home)
    assert report.lines == 2
    assert report.files[0].moved_from is not None
    assert texts(store) == [f'{{"type":"event","n":{n}}}' for n in (1, 2, 3, 4)]
    assert cursors(store) == [("archived_sessions/" + NAME, archived.stat().st_size)]
    assert scan(access, ingestor, home).lines == 0


def test_a_file_that_vanishes_between_listing_and_reading_is_picked_up_later(store, home):
    live = home / "sessions/2026/01/02" / NAME
    live.write_bytes(record(1))
    access, ingestor = make(store, home)
    listed = discover_files(access, [home], PATTERNS)
    os.rename(live, home / "archived_sessions" / NAME)

    report = ingestor.scan(listed)
    assert report.lines == 0 and len(report.vanished) == 1
    assert scan(access, ingestor, home).lines == 1
    assert len(texts(store)) == 1


def test_an_archive_copy_with_a_new_inode_adds_nothing_twice(store, home):
    live = home / "sessions/2026/01/02" / NAME
    live.write_bytes(record(1) + record(2))
    access, ingestor = make(store, home)
    scan(access, ingestor, home)

    shutil.copyfile(live, home / "archived_sessions" / NAME)
    live.unlink()
    scan(access, ingestor, home)
    assert len(texts(store)) == 2


# -- rewrites, batches, crashes -------------------------------------------------------


def test_a_truncated_file_is_read_again_from_the_start(store, home):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1) + record(2) + record(3))
    access, ingestor = make(store, home)
    scan(access, ingestor, home)

    path.write_bytes(record(9))
    report = scan(access, ingestor, home)
    assert report.files[0].restarted
    assert report.lines == 1


def test_a_file_replaced_at_the_same_path_is_read_from_the_start(store, home):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1))
    access, ingestor = make(store, home)
    scan(access, ingestor, home)

    replacement = path.with_name("replacement.tmp")
    replacement.write_bytes(record(7) + record(8) + record(9))
    os.replace(replacement, path)
    report = scan(access, ingestor, home)
    assert report.files[0].restarted
    assert report.lines == 3


class MemoryLedger:
    def __init__(self, cursor=None):
        self.cursors = {} if cursor is None else {cursor.path: cursor}
        self.lines = []

    def cursor_at(self, harness, path):
        return self.cursors.get(path)

    def cursor_of(self, harness, device, inode):
        return next(
            (c for c in self.cursors.values() if (c.device, c.inode) == (device, inode)), None
        )

    def commit(self, previous, cursor, lines):
        if previous is not None and previous.path != cursor.path:
            self.cursors.pop(previous.path, None)
        self.cursors[cursor.path] = cursor
        self.lines.extend(lines)


def test_a_reused_inode_under_another_name_is_not_taken_for_a_move(home):
    """The old file was deleted and its inode reused: the new file is read from its start."""
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1) + record(2))
    found = path.stat()
    gone = Cursor(
        HARNESS,
        str(home.resolve() / "sessions/2026/01/02/rollout-other.jsonl"),
        str(found.st_dev),
        str(found.st_ino),
        offset=len(record(1)),
        size=999,
    )
    ledger = MemoryLedger(gone)
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    report = Ingestor(access, ledger, harness=HARNESS).scan([path])
    assert report.lines == 2, "nothing at the start of the new file was skipped"
    assert report.files[0].moved_from is None


def test_batches_and_long_lines(home):
    path = home / "sessions/2026/01/02" / NAME
    long_line = b"L" * 300
    path.write_bytes(record(1) + long_line + b"\n" + record(2) + b"X" * 5000 + b"\n" + record(3))
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    ledger = MemoryLedger()
    ingestor = Ingestor(access, ledger, harness=HARNESS, batch_bytes=64, max_line_bytes=1024)

    report = ingestor.scan([path])
    assert [line.data for line in ledger.lines] == [
        record(1)[:-1],
        long_line,
        record(2)[:-1],
        record(3)[:-1],
    ]
    assert report.files[0].oversized_lines == 1
    assert ledger.cursors[str(path.resolve())].offset == path.stat().st_size
    assert [line.offset for line in ledger.lines] == sorted(line.offset for line in ledger.lines)


def test_an_unfinished_long_line_waits(home):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1) + b"L" * 500)
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    ledger = MemoryLedger()
    report = Ingestor(access, ledger, harness=HARNESS, batch_bytes=64, max_line_bytes=1024).scan(
        [path]
    )
    assert len(ledger.lines) == 1
    assert report.files[0].waiting_bytes == 500


def test_a_line_limit_below_the_batch_size_is_refused(home):
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    with pytest.raises(ValueError):
        Ingestor(access, MemoryLedger(), harness=HARNESS, batch_bytes=1024, max_line_bytes=512)


def test_a_failed_write_leaves_the_cursor_where_it_was(store, home):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1) + record(2))
    failures = {"left": 1}

    def flaky(session, cursor, lines):
        write_lines(session, cursor, lines)
        if failures["left"]:
            failures["left"] -= 1
            raise RuntimeError("the database went away")

    access, ingestor = make(store, home, writer=flaky)
    with pytest.raises(RuntimeError):
        scan(access, ingestor, home)
    assert texts(store) == []
    assert cursors(store) == []

    assert scan(access, ingestor, home).lines == 2
    assert len(texts(store)) == 2


def test_files_outside_the_roots_are_refused(store, home, tmp_path):
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_bytes(record(1))
    access, ingestor = make(store, home)
    report = ingestor.scan([outside])
    assert report.refused == [str(outside)]
    assert texts(store) == []


def test_ingest_leaves_the_harness_directory_untouched(store, home):
    (home / "sessions/2026/01/02" / NAME).write_bytes(record(1) + record(2) + b'{"partial"')
    (home / "archived_sessions" / NAME.replace("05-0b5", "06-0b5")).write_bytes(record(3))
    access, ingestor = make(store, home)
    with assert_untouched([home]):
        assert scan(access, ingestor, home).lines == 3
        assert scan(access, ingestor, home).lines == 0


# -- keeping up ----------------------------------------------------------------------


def test_only_the_folders_patterns_begin_with_are_listed(home, monkeypatch):
    assert pattern_folders(PATTERNS) == ["archived_sessions", "sessions"]
    assert pattern_folders(["a/b/*.x", "a/*.x", "index.jsonl"]) == ["a"]
    assert pattern_folders(["*.jsonl", "sessions/*.jsonl"]) == [""]

    (home / "sessions/2026/01/02" / NAME).write_bytes(record(1))
    (home / "session_index.jsonl").write_text("{}\n")
    # What a harness keeps beside its sessions: never listed, however much it is.
    (home / "worktrees/checkout/sessions/2026/01/02").mkdir(parents=True)
    (home / "worktrees/checkout/sessions/2026/01/02" / NAME).write_bytes(record(1))
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    walked = []
    walk = access.walk_states
    monkeypatch.setattr(
        access, "walk_states", lambda path, wanted: walked.append(Path(path)) or walk(path, wanted)
    )
    found = discover_files(access, [home], [*PATTERNS, "session_index.jsonl"])
    real = home.resolve()
    assert found == [real / "session_index.jsonl", real / "sessions/2026/01/02" / NAME]
    assert walked == [real / "archived_sessions", real / "sessions"]


def test_a_linked_sessions_folder_is_not_entered(home):
    elsewhere = home / "elsewhere/2026/01/02"
    elsewhere.mkdir(parents=True)
    (elsewhere / NAME).write_bytes(record(1))
    (home / "sessions/2026/01/02").rmdir()
    for folder in ("sessions/2026/01", "sessions/2026", "sessions"):
        (home / folder).rmdir()
    (home / "sessions").symlink_to(home / "elsewhere")
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    assert discover_files(access, [home], PATTERNS) == []


def test_changed_paths_are_matched_without_listing_a_folder(home, tmp_path, monkeypatch):
    session_file = home / "sessions/2026/01/02" / NAME
    session_file.write_bytes(record(1))
    (home / "sessions/2026/01/02/notes.txt").write_text("x")
    (home / "worktrees").mkdir()
    (home / "worktrees" / NAME).write_bytes(record(1))
    (tmp_path / "outside.jsonl").write_bytes(record(1))
    (home / "archived_sessions/rollout-link.jsonl").symlink_to(tmp_path / "outside.jsonl")
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    monkeypatch.setattr(access, "walk_states", lambda *_: pytest.fail("a folder was listed"))
    changed = [
        session_file,
        home / "sessions/2026/01/02/notes.txt",
        home / "sessions/2026/01/02",  # a folder
        home / "sessions/2026/01/02/rollout-gone.jsonl",  # deleted again
        home / "worktrees" / NAME,  # not where sessions are kept
        home / "archived_sessions/rollout-link.jsonl",  # leaves the roots
        tmp_path / "outside.jsonl",
    ]
    assert match_files(access, [home], PATTERNS, changed) == [session_file.resolve()]
    assert match_files(access, [home], PATTERNS, changed) == discover_files_plain(home)


def discover_files_plain(home: Path) -> list[Path]:
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    return discover_files(access, [home], PATTERNS)


def _count(store) -> int:
    with store() as session:
        return session.scalar(select(func.count()).select_from(Event))


@pytest.mark.parametrize("force_polling", [True, False], ids=["polling", "file-events"])
def test_the_watch_loop_ingests_lines_as_they_are_written(store, home, force_polling):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1))
    access, ingestor = make(store, home)
    stop = threading.Event()
    worker = threading.Thread(
        target=run,
        kwargs={
            "directories": [home],
            "discover": lambda: discover_files(access, [home], PATTERNS),
            "scan": ingestor.scan,
            "stop": stop,
            "poll_seconds": 5.0 if not force_polling else 0.5,
            "force_polling": force_polling,
            "debounce_ms": 50,
        },
        daemon=True,
    )
    worker.start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and _count(store) < 1:
            time.sleep(0.05)
        assert _count(store) == 1, "the first pass read what was already there"
        with open(path, "ab") as target:
            target.write(record(2))
        while time.monotonic() < deadline and _count(store) < 2:
            time.sleep(0.05)
        assert _count(store) == 2, "a new line was picked up while the loop ran"
    finally:
        stop.set()
        worker.join(timeout=20)
    assert not worker.is_alive()


class Recorder:
    """A scan that only notes what it was asked to read, and when."""

    def __init__(self, seconds: float = 0.0) -> None:
        self.calls: list[list[Path]] = []
        self.progress: list[tuple[int, int]] = []
        self.discovered = 0
        self._seconds = seconds

    def scan(self, paths: list[Path]) -> None:
        time.sleep(self._seconds)
        self.calls.append(list(paths))

    def read(self) -> list[Path]:
        return [path for call in self.calls for path in call]


def run_until(condition, *, timeout: float = 60.0, **options) -> None:
    """Run the watch loop until ``condition()`` holds, then stop it.

    The wait is long because a loaded machine can take many seconds to report a file
    event; the loop stops as soon as the condition holds, so it costs nothing otherwise.
    """
    stop = threading.Event()
    worker = threading.Thread(target=run, kwargs={"stop": stop, **options}, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not condition():
            time.sleep(0.02)
        assert condition(), "the loop did not get there in time"
    finally:
        stop.set()
        worker.join(timeout=20)
    assert not worker.is_alive()


class Changes:
    """Stands in for the operating system's file events: the test says what changed, and when.

    How soon a real watcher reports depends on the machine and its load, which is not what
    these tests are about. ``watcher`` is given to the loop in the real one's place.
    """

    def __init__(self) -> None:
        self.waiting: list[Path] = []
        self.watched: frozenset[Path] = frozenset()
        changes = self

        class Watcher:
            failed = False

            def __init__(self, directories, **_options) -> None:
                self.directories = directories
                changes.watched = directories

            def take(self, seconds: float, stop: threading.Event) -> set[Path]:
                if not changes.waiting:
                    stop.wait(min(seconds, 0.01))
                found, changes.waiting = set(changes.waiting), []
                return found

            def close(self) -> None:
                pass

        self.watcher = Watcher

    def report(self, path: Path) -> None:
        self.waiting.append(path)


def test_the_first_read_goes_in_the_order_given_and_says_how_far_it_is(home):
    files = [home / "sessions/2026/01/02" / NAME.replace("05-0b5", f"0{n}-0b5") for n in range(5)]
    for path in files:
        path.write_bytes(record(1))
    seen = Recorder()
    run_until(
        lambda: seen.progress[-1:] == [(5, 5)],
        directories=[home / "sessions"],
        discover=lambda: sorted(files),
        scan=seen.scan,
        order=lambda paths: list(reversed(paths)),
        progress=lambda done, total: seen.progress.append((done, total)),
        slice_seconds=0.0,  # one file a slice
        poll_seconds=30.0,
    )
    assert seen.read()[:5] == sorted(files, reverse=True)
    assert seen.progress == [(0, 5), (1, 5), (2, 5), (3, 5), (4, 5), (5, 5)]


def test_a_file_written_during_the_first_read_does_not_wait_for_its_end(home):
    # The recorder reads nothing, so the old files need only be named.
    old = [home / "archived_sessions" / f"rollout-old-{n:03}.jsonl" for n in range(200)]
    live = home / "sessions/2026/01/02" / NAME
    live.write_bytes(record(1))
    seen = Recorder(seconds=0.005)
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    changes = Changes()
    reported_at: list[int] = []

    def read_after_the_change() -> int | None:
        """Which scan first read the live file once it was reported changed, if any has yet."""
        if len(seen.calls) >= 20 and not reported_at:
            reported_at.append(len(seen.calls))
            changes.report(live)
        if not reported_at:
            return None
        later = enumerate(seen.calls[reported_at[0] :], start=reported_at[0])
        return next((n for n, call in later if call == [live.resolve()]), None)

    run_until(
        lambda: read_after_the_change() is not None,
        directories=[home / "sessions", home / "archived_sessions"],
        discover=lambda: [*old, live],  # the live file would come last
        scan=seen.scan,
        match=lambda paths: match_files(access, [home], PATTERNS, paths),
        slice_seconds=0.02,
        watcher_type=changes.watcher,
    )
    # Read at the next gap between slices, a handful of files on, not after all two hundred.
    assert read_after_the_change() - reported_at[0] < 40


def test_a_change_is_looked_up_directly_not_by_listing_everything_again(home):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1))
    (home / "sessions/2026/01/02/notes.txt").write_text("x")
    seen = Recorder()
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    changes = Changes()
    reported: list[bool] = []

    def discover() -> list[Path]:
        seen.discovered += 1
        return discover_files(access, [home], PATTERNS)

    def changed_and_read() -> bool:
        if len(seen.calls) == 1 and not reported:
            reported.append(True)
            # What a watcher names: the session file, and a file beside it that is not one.
            changes.report(path)
            changes.report(home / "sessions/2026/01/02/notes.txt")
        return len(seen.calls) >= 2

    run_until(
        changed_and_read,
        directories=[home / "sessions"],
        discover=discover,
        scan=seen.scan,
        match=lambda paths: match_files(access, [home], PATTERNS, paths),
        poll_seconds=600.0,  # no full poll within the test
        watcher_type=changes.watcher,
    )
    assert seen.calls[1] == [path.resolve()]
    assert seen.discovered == 1


def test_a_single_file_is_watched_like_a_folder(home):
    index = home / "session_index.jsonl"
    index.write_text("{}\n")
    seen = Recorder()
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    changes = Changes()
    reported: list[bool] = []

    def changed_and_read() -> bool:
        if len(seen.calls) == 1 and not reported:
            reported.append(True)
            changes.report(index)
        return len(seen.calls) >= 2

    run_until(
        changed_and_read,
        # A folder that is not there yet is left out; the file is watched as it is.
        directories=lambda: [index, home / "not-made-yet"],
        discover=lambda: [index.resolve()],
        scan=seen.scan,
        match=lambda paths: match_files(access, [home], ["session_index.jsonl"], paths),
        poll_seconds=600.0,
        watcher_type=changes.watcher,
    )
    assert changes.watched == frozenset({index})
    assert seen.calls[1] == [index.resolve()]


def _signature(path: Path) -> tuple[int, int]:
    found = path.stat()
    return found.st_size, found.st_mtime_ns


def test_a_full_poll_reads_only_what_changed_since_it_was_last_read(home):
    quiet = home / "archived_sessions" / "rollout-quiet.jsonl"
    busy = home / "sessions/2026/01/02" / NAME
    for path in (quiet, busy):
        path.write_bytes(record(1))
    seen = Recorder()

    def discover() -> list[Path]:
        seen.discovered += 1
        return [quiet, busy]

    written_at: list[int] = []

    def polled_then_written() -> bool:
        # Written once a few polls are over, at whichever poll this is looked at: a
        # loaded machine can run several polls between two looks.
        if seen.discovered >= 4 and not written_at:
            with open(busy, "ab") as target:
                target.write(record(2))
            written_at.append(seen.discovered)
        return bool(written_at) and seen.discovered >= written_at[0] + 4

    # Nothing is watched, so every pass after the first read is a full poll.
    run_until(
        polled_then_written,
        directories=[],
        discover=discover,
        scan=seen.scan,
        signature=_signature,
        poll_seconds=0.05,
    )
    assert seen.calls[:2] == [[quiet], [busy]], "the first read takes every file"
    later = [call for call in seen.calls[2:] if call]
    assert later == [[busy]], "after it, only the file that was written to"


def test_a_full_poll_tells_what_changed_from_the_listing_alone(home):
    from agdash.ingest import discover_states

    quiet = home / "archived_sessions" / "rollout-quiet.jsonl"
    busy = home / "sessions/2026/01/02" / NAME
    for path in (quiet, busy):
        path.write_bytes(record(1))
    access = ReadOnlyAccess([home], snapshot_dir=home.parent)
    seen = Recorder()
    asked: list[Path] = []

    def listed() -> dict[Path, object]:
        seen.discovered += 1
        return dict(discover_states(access, [home], PATTERNS))

    def signature(path: Path) -> tuple[int, int, int]:
        asked.append(path)
        found = path.stat()
        return found.st_size, found.st_mtime_ns, found.st_ino

    written_at: list[int] = []

    def polled_then_written() -> bool:
        # At whichever poll this is looked at, as in the test above.
        if seen.discovered >= 3 and not written_at:
            with open(busy, "ab") as target:
                target.write(record(2))
            written_at.append(seen.discovered)
        return bool(written_at) and seen.discovered >= written_at[0] + 4

    run_until(
        polled_then_written,
        directories=[],
        discover=lambda: sorted(discover_states(access, [home], PATTERNS)),
        scan=seen.scan,
        signature=signature,
        discover_signed=listed,
        poll_seconds=0.05,
    )
    real = [quiet.resolve(), busy.resolve()]
    assert seen.calls[:2] == [[real[0]], [real[1]]], "the first read takes every file"
    assert [call for call in seen.calls[2:] if call] == [[real[1]]]
    # Each file was asked about once, when first read; the polls never asked again.
    assert sorted(asked) == sorted(real)


def test_a_scan_that_failed_is_tried_again_at_the_next_full_poll(home):
    path = home / "sessions/2026/01/02" / NAME
    path.write_bytes(record(1))
    calls: list[list[Path]] = []

    def scan(paths: list[Path]) -> bool:
        calls.append(list(paths))
        return len(calls) > 2  # the store was busy twice

    run_until(
        lambda: len(calls) >= 5,
        directories=[],
        discover=lambda: [path],
        scan=scan,
        signature=_signature,
        poll_seconds=0.05,
    )
    assert calls[:5] == [[path], [path], [path], [], []]


# -- compressed archives ------------------------------------------------------------

ARCHIVE_PATTERNS = PATTERNS + ["archived_sessions/rollout-*.jsonl.zst"]


def write_by_transcript(session, cursor: Cursor, lines) -> None:
    """Like ``write_lines``, with a compressed copy counted as its original."""
    plain = Cursor(**{**cursor.__dict__, "path": cursor.path.removesuffix(".zst")})
    write_lines(session, plain, lines)


def scan_archives(access, ingestor, home):
    return ingestor.scan(discover_files(access, [home], ARCHIVE_PATTERNS))


def test_a_compressed_archive_lines_up_with_its_original(store, home):
    live = home / "sessions/2026/01/02" / NAME
    live.write_bytes(record(1) + record(2) + record(3))
    access, ingestor = make(store, home, write_by_transcript)
    scan_archives(access, ingestor, home)

    (home / "archived_sessions" / f"{NAME}.zst").write_bytes(compress(live.read_bytes()))
    live.unlink()
    report = scan_archives(access, ingestor, home)
    assert texts(store) == [record(n).decode().strip() for n in (1, 2, 3)]
    (archive,) = [f for f in report.files if f.path.endswith(".zst")]
    assert archive.lines == 3, "every line is handed over; the writer finds them stored"
    with store() as session:
        offsets = session.scalars(select(Event.sequence).order_by(Event.sequence)).all()
    assert offsets == [0, len(record(1)), len(record(1)) * 2]


def test_an_unchanged_archive_is_not_decompressed_again(store, home, monkeypatch):
    (home / "archived_sessions" / f"{NAME}.zst").write_bytes(compress(record(1) + record(2)))
    access, ingestor = make(store, home)
    scan_archives(access, ingestor, home)
    from agdash.ingest import engine

    def refuse(*_args, **_kwargs):
        raise AssertionError("decompressed again")

    monkeypatch.setattr(engine, "iter_decompressed", refuse)
    second = scan_archives(access, ingestor, home)
    assert second.lines == 0 and len(texts(store)) == 2


def test_an_archive_without_a_final_newline_keeps_its_last_line(store, home):
    (home / "archived_sessions" / f"{NAME}.zst").write_bytes(
        compress(record(1) + record(2).rstrip(b"\n"))
    )
    access, ingestor = make(store, home)
    scan_archives(access, ingestor, home)
    assert len(texts(store)) == 2


def test_an_archive_still_being_written_waits_until_it_is_complete(store, home):
    target = home / "archived_sessions" / f"{NAME}.zst"
    whole = compress(record(1) + record(2))
    target.write_bytes(whole[: len(whole) // 2])
    access, ingestor = make(store, home)
    (first,) = scan_archives(access, ingestor, home).files
    assert first.unreadable and texts(store) == [] and cursors(store) == []
    target.write_bytes(whole)
    (second,) = scan_archives(access, ingestor, home).files
    assert not second.unreadable and second.lines == 2


def test_an_archive_that_decompresses_past_the_limit_is_not_read(store, home):
    (home / "archived_sessions" / f"{NAME}.zst").write_bytes(compress(record(1) * 1000))
    access, ingestor = make(store, home, max_archive_bytes=1000)
    (report,) = scan_archives(access, ingestor, home).files
    assert report.unreadable and texts(store) == []


def test_several_frames_are_read_as_one_text():
    data = compress(record(1)) + compress(record(2))
    assert decompress(io.BytesIO(data), 1 << 20) == record(1) + record(2)
    with pytest.raises(UnreadableArchive):
        decompress(io.BytesIO(b"not zstd at all"), 1 << 20)


def test_a_large_archive_is_stored_in_bounded_batches(store, home):
    archive = home / "archived_sessions" / f"{NAME}.zst"
    whole = b"".join(record(n) for n in range(400))
    archive.write_bytes(compress(whole))
    commits = []

    def counting(session, cursor, lines):
        commits.append(sum(len(line.data) for line in lines))
        write_lines(session, cursor, lines)

    access, ingestor = make(store, home, counting, batch_bytes=1024, max_line_bytes=4096)
    (report,) = scan_archives(access, ingestor, home).files
    assert report.lines == 400 and not report.unreadable
    assert len(commits) > 5 and max(commits) < 2048, "no batch holds the whole archive"
    assert texts(store) == [record(n).rstrip(b"\n").decode() for n in range(400)]
    with store() as session:
        (cursor,) = session.scalars(select(IngestCursor)).all()
    assert (cursor.byte_offset, cursor.size) == (len(whole), archive.stat().st_size)


def test_an_archive_read_cut_short_resumes_after_the_last_batch(store, home):
    archive = home / "archived_sessions" / f"{NAME}.zst"
    archive.write_bytes(compress(b"".join(record(n) for n in range(300))))
    calls = []

    def failing(session, cursor, lines):
        calls.append(lines[0].offset)
        if len(calls) == 4:
            raise RuntimeError("the machine went to sleep")
        write_lines(session, cursor, lines)

    access, ingestor = make(store, home, failing, batch_bytes=1024, max_line_bytes=4096)
    with pytest.raises(RuntimeError):
        scan_archives(access, ingestor, home)
    stored = len(texts(store))
    assert 0 < stored < 300
    seen = []

    def watching(session, cursor, lines):
        seen.extend(line.offset for line in lines)
        write_lines(session, cursor, lines)

    access, ingestor = make(store, home, watching, batch_bytes=1024, max_line_bytes=4096)
    (report,) = scan_archives(access, ingestor, home).files
    assert not report.restarted and report.lines == 300 - stored
    assert min(seen) > 0, "the stored batches are not handed over again"
    assert texts(store) == [record(n).rstrip(b"\n").decode() for n in range(300)]


def test_an_oversized_line_inside_an_archive_is_skipped_and_the_rest_kept(store, home):
    archive = home / "archived_sessions" / f"{NAME}.zst"
    huge = b'{"type":"event","n":"' + b"x" * 20_000 + b'"}\n'
    archive.write_bytes(compress(record(1) + huge + record(2)))
    access, ingestor = make(store, home, batch_bytes=1024, max_line_bytes=4096)
    (report,) = scan_archives(access, ingestor, home).files
    assert report.oversized_lines == 1 and report.lines == 2
    assert texts(store) == [record(1).rstrip(b"\n").decode(), record(2).rstrip(b"\n").decode()]
