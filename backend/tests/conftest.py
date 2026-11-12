"""Synthetic harness data. Nothing here is copied from a real session."""

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from agdash.access import ReadOnlyAccess

WAL_ONLY_ROW = "committed, still only in the write-ahead log"
MAIN_FILE_ROW = "committed, in the main file"
JOURNAL_ROWS = 400

needs_file_permissions = pytest.mark.skipif(
    os.geteuid() == 0, reason="the superuser is not stopped by file permissions"
)


def build_wal_database(target: Path, scratch: Path) -> None:
    """Leave ``target`` and ``target-wal`` as a harness leaves them mid-session.

    One row has been checkpointed into the main file; a second is committed
    but exists only in the write-ahead log. There is no ``-shm`` file.
    """
    scratch.mkdir()
    live = scratch / target.name
    writer = sqlite3.connect(live)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("CREATE TABLE notes (body TEXT NOT NULL)")
        writer.execute("INSERT INTO notes VALUES (?)", (MAIN_FILE_ROW,))
        writer.commit()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        writer.execute("INSERT INTO notes VALUES (?)", (WAL_ONLY_ROW,))
        writer.commit()
        # Copied while the writer is open: closing it would fold the log away.
        shutil.copyfile(live, target)
        shutil.copyfile(f"{live}-wal", f"{target}-wal")
    finally:
        writer.close()
    shutil.rmtree(scratch)


def build_interrupted_database(target: Path, scratch: Path) -> None:
    """Leave ``target`` and ``target-journal`` as a writer killed mid-transaction would.

    Every committed row reads ``committed``. The main file already holds pages
    of an unfinished update; the rollback journal holds what they replaced.
    """
    scratch.mkdir()
    live = scratch / target.name
    writer = sqlite3.connect(live, isolation_level=None)
    try:
        writer.execute("PRAGMA journal_mode = DELETE")
        writer.execute("PRAGMA cache_size = 2")
        writer.execute("CREATE TABLE notes (body TEXT NOT NULL)")
        writer.execute("BEGIN")
        writer.executemany(
            "INSERT INTO notes VALUES (?)", [("committed" + "." * 400,)] * JOURNAL_ROWS
        )
        writer.execute("COMMIT")
        writer.execute("BEGIN")
        writer.execute("UPDATE notes SET body = replace(body, 'committed', 'unfinished')")
        shutil.copyfile(live, target)
        shutil.copyfile(f"{live}-journal", f"{target}-journal")
        writer.execute("ROLLBACK")
    finally:
        writer.close()
    shutil.rmtree(scratch)


@dataclass(frozen=True)
class HarnessTree:
    root: Path
    outside: Path
    transcript: Path
    wal_database: Path
    journal_database: Path
    secret: Path

    def access(self, **options) -> ReadOnlyAccess:
        return ReadOnlyAccess([self.root], snapshot_dir=self.outside / "snapshots", **options)


@pytest.fixture
def harness(tmp_path) -> HarnessTree:
    root = tmp_path / "harness"
    outside = tmp_path / "outside"
    (outside / "snapshots").mkdir(parents=True)
    secret = outside / "secret.txt"
    secret.write_text("not harness data\n")

    transcript = root / "sessions" / "2026" / "01" / "02" / "session-a.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text('{"type": "user", "text": "hello"}\n{"type": "assistant"}\n')
    subagent = root / "projects" / "-work-example" / "session-b" / "subagents" / "agent-1.jsonl"
    subagent.parent.mkdir(parents=True)
    subagent.write_text('{"type": "assistant"}\n')
    (root / "presence").mkdir()
    (root / "presence" / "stale.lock").touch()

    state = root / "state"
    state.mkdir()
    wal_database = state / "summaries.db"
    build_wal_database(wal_database, tmp_path / "scratch-wal")
    journal_database = state / "goals.sqlite"
    build_interrupted_database(journal_database, tmp_path / "scratch-journal")

    (root / "link-to-transcript").symlink_to(transcript)
    (root / "link-to-secret").symlink_to(secret)
    (root / "link-to-outside-directory").symlink_to(outside, target_is_directory=True)

    return HarnessTree(root, outside, transcript, wal_database, journal_database, secret)
