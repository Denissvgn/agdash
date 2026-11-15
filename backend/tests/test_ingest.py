"""The ingest engine: each complete line once, across re-runs, moves, rewrites and crashes.

Files are synthetic. Lines go into agdash's own database through a test writer
that stores one event per line, keyed by the line's byte offset, as the
harness adapters will.
"""

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
from agdash.ingest import Cursor, Ingestor, discover_files
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
