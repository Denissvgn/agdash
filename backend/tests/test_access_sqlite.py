"""SQLite stores are read correctly from a private copy, without touching the original."""

import os
import shutil
import sqlite3

import pytest
from conftest import JOURNAL_ROWS, MAIN_FILE_ROW, WAL_ONLY_ROW, needs_file_permissions

from agdash.access import OutsideAllowedRoots, SnapshotTooLarge, SnapshotUnstable
from agdash.access.guard import assert_untouched


def _bodies(connection) -> list[str]:
    return [row[0] for row in connection.execute("SELECT body FROM notes ORDER BY rowid")]


def _make_read_only(root) -> None:
    for directory, _subdirectories, names in os.walk(root):
        for name in names:
            path = os.path.join(directory, name)
            if not os.path.islink(path):
                os.chmod(path, 0o400)
        os.chmod(directory, 0o500)


def _make_writable(root) -> None:
    for directory, _subdirectories, _names in os.walk(root):
        os.chmod(directory, 0o700)


@pytest.fixture
def read_only_harness(harness):
    _make_read_only(harness.root)
    yield harness
    _make_writable(harness.root)


@needs_file_permissions
def test_a_store_that_refuses_a_read_only_open_is_read_through_a_snapshot(read_only_harness):
    harness = read_only_harness
    plain = sqlite3.connect(f"file:{harness.wal_database}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            plain.execute("SELECT body FROM notes").fetchall()
    finally:
        plain.close()

    with (
        assert_untouched([harness.root]),
        harness.access().sqlite_snapshot(harness.wal_database) as snapshot,
    ):
        assert _bodies(snapshot) == [MAIN_FILE_ROW, WAL_ONLY_ROW]


def test_rows_still_in_the_write_ahead_log_are_read(harness):
    # The other way to open without writing skips the log and loses the newest row.
    immutable = sqlite3.connect(f"file:{harness.wal_database}?immutable=1", uri=True)
    try:
        assert _bodies(immutable) == [MAIN_FILE_ROW]
    finally:
        immutable.close()

    with harness.access().sqlite_snapshot(harness.wal_database) as snapshot:
        assert _bodies(snapshot) == [MAIN_FILE_ROW, WAL_ONLY_ROW]
        assert snapshot.execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_an_interrupted_write_is_rolled_back_in_the_copy(harness):
    with harness.access().sqlite_snapshot(harness.journal_database) as snapshot:
        bodies = _bodies(snapshot)
        assert snapshot.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert len(bodies) == JOURNAL_ROWS
    assert all(body.startswith("committed") for body in bodies)


def test_the_original_is_left_exactly_as_it_was(harness):
    with assert_untouched([harness.root]):
        with harness.access().sqlite_snapshot(harness.wal_database) as snapshot:
            _bodies(snapshot)
        with harness.access().sqlite_snapshot(harness.journal_database) as snapshot:
            _bodies(snapshot)
    assert not os.path.exists(f"{harness.wal_database}-shm")


def test_the_snapshot_cannot_be_written(harness):
    with harness.access().sqlite_snapshot(harness.wal_database) as snapshot:
        with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
            snapshot.execute("INSERT INTO notes VALUES ('x')")
        with pytest.raises(sqlite3.OperationalError, match="readonly|read-only"):
            snapshot.execute("CREATE TABLE other (x)")


def test_the_copy_is_deleted_afterwards(harness):
    snapshots = harness.outside / "snapshots"
    with harness.access().sqlite_snapshot(harness.wal_database):
        (workdir,) = snapshots.iterdir()
        assert workdir.stat().st_mode & 0o077 == 0
    assert list(snapshots.iterdir()) == []

    with pytest.raises(RuntimeError), harness.access().sqlite_snapshot(harness.wal_database):
        raise RuntimeError("the reader failed")
    assert list(snapshots.iterdir()) == []


def test_no_lock_is_taken_or_respected(harness, tmp_path):
    database = harness.root / "state" / "busy.sqlite"
    writer = sqlite3.connect(database, isolation_level=None, timeout=0)
    try:
        writer.execute("CREATE TABLE notes (body TEXT NOT NULL)")
        writer.execute("INSERT INTO notes VALUES ('before')")
        writer.execute("BEGIN EXCLUSIVE")

        # A reader that opens the database where it lies is locked out.
        in_place = sqlite3.connect(database, timeout=0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                in_place.execute("SELECT body FROM notes").fetchall()
        finally:
            in_place.close()

        with harness.access().sqlite_snapshot(database) as snapshot:
            assert _bodies(snapshot) == ["before"]
            # And the harness is never locked out by the snapshot.
            writer.execute("INSERT INTO notes VALUES ('during')")
            writer.execute("COMMIT")
            assert _bodies(snapshot) == ["before"]
    finally:
        writer.close()


def test_a_database_that_keeps_changing_is_reported(harness, monkeypatch):
    access = harness.access()
    states = iter(range(100))
    monkeypatch.setattr(access, "_database_state", lambda database: next(states))
    with pytest.raises(SnapshotUnstable), access.sqlite_snapshot(harness.wal_database):
        pass
    assert list((harness.outside / "snapshots").iterdir()) == []


def test_a_copy_is_retried_until_the_database_holds_still(harness, monkeypatch):
    access = harness.access()
    states = iter(["first", "changed", "second", "second"])
    monkeypatch.setattr(access, "_database_state", lambda database: next(states))
    with access.sqlite_snapshot(harness.wal_database) as snapshot:
        assert _bodies(snapshot) == [MAIN_FILE_ROW, WAL_ONLY_ROW]


def test_a_change_to_the_main_file_or_a_restarted_log_is_noticed(harness):
    access = harness.access()
    database = harness.wal_database
    before = access._database_state(database)
    assert access._database_state(database) == before

    with open(f"{database}-wal", "ab") as log:
        log.write(b"\0" * 64)
    assert access._database_state(database) == before, "a growing log is not a change"

    with open(f"{database}-wal", "r+b") as log:
        log.seek(16)
        log.write(b"\xff\xff\xff\xff")
    assert access._database_state(database) != before, "a restarted log is a change"

    journal_before = access._database_state(harness.journal_database)
    os.utime(harness.journal_database, ns=(1, 1))
    assert access._database_state(harness.journal_database) != journal_before


def test_a_checkpoint_between_copies_is_survived(harness, monkeypatch):
    """The log disappears after it was listed: the attempt is abandoned, not half-used."""
    access = harness.access()
    copy_file = access._copy_file
    calls = []

    def copy_then_checkpoint(source, target, budget):
        calls.append(source.name)
        if source.name.endswith("-wal"):
            os.remove(source)
        return copy_file(source, target, budget)

    monkeypatch.setattr(access, "_copy_file", copy_then_checkpoint)
    with access.sqlite_snapshot(harness.wal_database) as snapshot:
        assert _bodies(snapshot) == [MAIN_FILE_ROW]
    assert calls == ["summaries.db", "summaries.db-wal", "summaries.db"]


def test_a_database_over_the_limit_is_refused(harness):
    access = harness.access(max_snapshot_bytes=1024)
    with pytest.raises(SnapshotTooLarge), access.sqlite_snapshot(harness.journal_database):
        pass
    assert list((harness.outside / "snapshots").iterdir()) == []


def test_a_sidecar_that_points_outside_the_roots_is_refused(harness):
    os.remove(f"{harness.wal_database}-wal")
    os.symlink(harness.secret, f"{harness.wal_database}-wal")
    with (
        pytest.raises(OutsideAllowedRoots),
        harness.access().sqlite_snapshot(harness.wal_database),
    ):
        pass


def test_awkward_file_names_are_handled(harness):
    awkward = harness.root / "state" / "con versations #1?mode=memory&x=%41.db"
    shutil.copyfile(harness.wal_database, awkward)
    shutil.copyfile(f"{harness.wal_database}-wal", f"{awkward}-wal")
    with harness.access().sqlite_snapshot(awkward) as snapshot:
        assert _bodies(snapshot) == [MAIN_FILE_ROW, WAL_ONLY_ROW]


def test_a_file_that_is_not_a_database_fails_on_the_query(harness):
    with harness.access().sqlite_snapshot(harness.transcript) as snapshot:
        with pytest.raises(sqlite3.DatabaseError):
            snapshot.execute("SELECT * FROM sqlite_master").fetchall()
