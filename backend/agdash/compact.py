"""Give room that removed data left inside the store's file back to the disk.

SQLite keeps a file the size it once grew to: pages that are no longer used wait
inside it for new data. After a large removal, or a change that makes the store
smaller, that can be gigabytes. Two things return it:

* Compacting writes the store anew, without the free pages, in place. It needs
  the store to itself and room on the volume for the new copy, and takes minutes
  on a large store, so the service does it only as it starts, and only when at
  least a quarter of the file is free. It also sets the store to give free pages
  back a few at a time from then on.
* Releasing gives free pages back a few at a time while the service runs, after
  it has removed old sessions or trimmed the archive. It works once the store has
  been compacted once.

Only a store that is a SQLite file is compacted. Nothing here touches a harness.

    python -m agdash.compact            how much is free inside the file
    python -m agdash.compact --apply    compact now; the service must not be running
"""

import argparse
import logging
import os
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine

from agdash.config import database_url
from agdash.db.migrate import sqlite_file, sync_database_url

log = logging.getLogger("agdash.compact")

MIN_FREE_BYTES = 256 * 1024 * 1024
MIN_FREE_SHARE = 0.25
INCREMENTAL = 2  # what PRAGMA auto_vacuum answers for a store that releases a few pages at a time
RELEASE_PAGES = 2000
RELEASE_PAUSE_SECONDS = 0.2
TEMPORARY = "SQLITE_TMPDIR"


@dataclass(frozen=True)
class Room:
    """A store's file: how large it is, and how much of it no data uses."""

    file_bytes: int
    free_bytes: int
    releases: bool  # whether free pages can be given back a few at a time

    @property
    def worth_compacting(self) -> bool:
        return (
            self.free_bytes >= MIN_FREE_BYTES
            and self.free_bytes >= self.file_bytes * MIN_FREE_SHARE
        )


@dataclass(frozen=True)
class Compacted:
    before_bytes: int
    after_bytes: int
    seconds: float


def _pragma(connection, name: str) -> int:
    return int(connection.exec_driver_sql(f"PRAGMA {name}").scalar() or 0)


def room(connection) -> Room:
    """Asked of any connection to a SQLite store; reading it changes nothing."""
    page = _pragma(connection, "page_size")
    return Room(
        file_bytes=_pragma(connection, "page_count") * page,
        free_bytes=_pragma(connection, "freelist_count") * page,
        releases=_pragma(connection, "auto_vacuum") == INCREMENTAL,
    )


def _engine(url: str) -> Engine:
    # Compacting cannot run inside a transaction.
    return create_engine(sync_database_url(url), isolation_level="AUTOCOMMIT")


def _volume_free(path: Path) -> int:
    volume = os.statvfs(path.parent)
    return volume.f_bavail * volume.f_frsize


def compact(url: str) -> Compacted | None:
    """Write the store anew without its free pages. None for a store that is not a file.

    The new copy is written beside the store, in its own private folder, and then
    through the write-ahead log, so the volume needs room for twice what the store
    will hold. SQLite takes the store for itself while it does this: another process
    that has it open waits, or is told the store is busy.
    """
    path = sqlite_file(url)
    if path is None or not path.exists():
        return None
    engine = _engine(url)
    earlier = os.environ.get(TEMPORARY)
    os.environ[TEMPORARY] = str(path.parent)
    started = time.monotonic()
    try:
        with engine.connect() as connection:
            before = room(connection).file_bytes
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.exec_driver_sql("PRAGMA auto_vacuum = INCREMENTAL")
            connection.exec_driver_sql("VACUUM")
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
            after = room(connection).file_bytes
    finally:
        if earlier is None:
            os.environ.pop(TEMPORARY, None)
        else:
            os.environ[TEMPORARY] = earlier
        engine.dispose()
    return Compacted(before, after, time.monotonic() - started)


def compact_if_worth_it(url: str) -> Compacted | None:
    """Compact when at least a quarter of the file is free and the volume has the room."""
    path = sqlite_file(url)
    if path is None or not path.exists():
        return None
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            found = room(connection)
    finally:
        engine.dispose()
    if not found.worth_compacting:
        return None
    needed = 2 * (found.file_bytes - found.free_bytes)
    if _volume_free(path) < needed:
        log.warning(
            "the store has %d MB free inside it, but its volume lacks the %d MB needed to"
            " compact it; it stays as it is",
            found.free_bytes // 1_000_000,
            needed // 1_000_000,
        )
        return None
    log.info(
        "compacting the store: %d MB of its %d MB are free; a large store takes some minutes",
        found.free_bytes // 1_000_000,
        found.file_bytes // 1_000_000,
    )
    done = compact(url)
    if done is not None:
        log.info(
            "compacted the store from %d MB to %d MB in %d s",
            done.before_bytes // 1_000_000,
            done.after_bytes // 1_000_000,
            done.seconds,
        )
    return done


def release(engine: Engine, *, stop: threading.Event | None = None) -> int:
    """Give free pages back to the disk, a few at a time; how many bytes went.

    Does nothing on a store that has not been compacted once, or is not SQLite.
    """
    if engine.dialect.name != "sqlite":
        return 0
    released = 0
    while not (stop is not None and stop.is_set()):
        with engine.connect() as connection:
            before = room(connection)
            if not before.releases or before.free_bytes == 0:
                return released
            connection.exec_driver_sql(f"PRAGMA incremental_vacuum({RELEASE_PAGES})")
            connection.commit()
            went = before.free_bytes - room(connection).free_bytes
        if went <= 0:
            return released
        released += went
        if stop is not None:
            stop.wait(RELEASE_PAUSE_SECONDS)
    return released


def main(argv: Sequence[str] | None = None, *, answering=None) -> int:
    parser = argparse.ArgumentParser(prog="agdash-compact", description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="compact now")
    args = parser.parse_args(argv)
    url = database_url()
    path = sqlite_file(url)
    if path is None or not path.exists():
        print("The store is not a file on this machine, so there is nothing to compact.")
        return 0
    engine = _engine(url)
    try:
        with engine.connect() as connection:
            found = room(connection)
    finally:
        engine.dispose()
    print(
        f"The store is {found.file_bytes // 1_000_000} MB,"
        f" of which {found.free_bytes // 1_000_000} MB is free inside the file."
    )
    if not args.apply:
        print("Pass --apply to compact it now, with the service stopped.")
        return 0
    if answering is not None and answering():
        print("The service is running. Stop it first: compacting needs the store to itself.")
        return 1
    done = compact(url)
    if done is not None:
        print(
            f"Compacted from {done.before_bytes // 1_000_000} MB"
            f" to {done.after_bytes // 1_000_000} MB in {done.seconds:.0f} s."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
