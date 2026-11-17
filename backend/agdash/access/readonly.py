"""The one way agdash reads harness data.

Everything a harness wrote is reached through :class:`ReadOnlyAccess`. It
enforces three things:

* a path is only read if, with every symbolic link resolved, it lies inside an
  allowlisted root;
* files are opened for reading only, and only regular files are opened, so a
  socket, pipe or device in a harness directory is never touched;
* nothing is locked, created or changed in a harness directory. SQLite stores
  are never opened where they lie: a WAL-mode database opened in place, even
  read-only, makes SQLite create ``-shm`` and ``-wal`` files beside it and
  take locks the harness can see. They are copied to a private directory and
  the copy is opened instead.

The class has no method that writes.
"""

import os
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import IO
from urllib.parse import quote

READ_MODES = frozenset({"r", "rt", "rb"})
SQLITE_SIDECARS = ("-wal", "-journal")
SQLITE_HEADER_BYTES = 100
WAL_HEADER_BYTES = 32
COPY_CHUNK_BYTES = 1024 * 1024
DEFAULT_MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024
SNAPSHOT_ATTEMPTS = 3


class AccessError(Exception):
    """A read was refused or could not be done safely."""


class OutsideAllowedRoots(AccessError, PermissionError):
    """The path does not lie inside an allowlisted root."""


class WriteRefused(AccessError, PermissionError):
    """Something other than a plain read was asked for."""


class NotARegularFile(AccessError):
    """The path is a directory, socket, pipe or device, not a file."""


class SnapshotUnstable(AccessError):
    """The database kept changing while it was being copied. Try again later."""


class SnapshotTooLarge(AccessError):
    """The database is larger than the snapshot limit."""


class ReadOnlyAccess:
    """Read-only access to the files under a fixed set of root directories."""

    def __init__(
        self,
        roots: Iterable[str | os.PathLike[str]],
        *,
        snapshot_dir: str | os.PathLike[str] | None = None,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
    ) -> None:
        self._roots = tuple(Path(root) for root in roots)
        if not self._roots:
            raise ValueError("at least one root is required")
        for root in self._roots:
            if not root.is_absolute():
                raise ValueError(f"root must be an absolute path: {root}")
        self._snapshot_dir = Path(snapshot_dir) if snapshot_dir is not None else None
        self._max_snapshot_bytes = max_snapshot_bytes
        # Fail at start-up, not at the first database read.
        self._private_snapshot_parent()

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    # -- paths ---------------------------------------------------------------

    def _real_roots(self) -> list[Path]:
        # Resolved on every call: a root may not exist yet, or may be a link.
        return [Path(os.path.realpath(root)) for root in self._roots]

    def _is_inside_roots(self, real_path: Path) -> bool:
        return any(real_path == root or root in real_path.parents for root in self._real_roots())

    def resolve(self, path: str | os.PathLike[str], *, strict: bool = True) -> Path:
        """Return the real path, or raise if it leaves the allowlisted roots.

        Symbolic links are followed first, so a link inside a root that points
        outside it is refused. With ``strict`` the path must exist.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            raise ValueError(f"path must be absolute: {candidate}")
        real_path = Path(os.path.realpath(candidate, strict=False))
        if not self._is_inside_roots(real_path):
            raise OutsideAllowedRoots(f"not inside an allowlisted root: {candidate}")
        if strict:
            os.stat(real_path)
        return real_path

    def exists(self, path: str | os.PathLike[str]) -> bool:
        return os.path.exists(self.resolve(path, strict=False))

    def stat(self, path: str | os.PathLike[str]) -> os.stat_result:
        return os.stat(self.resolve(path))

    def list_dir(self, path: str | os.PathLike[str]) -> list[Path]:
        """The entries of a directory, sorted by name. Entries are not resolved."""
        real_path = self.resolve(path)
        with os.scandir(real_path) as entries:
            return sorted(real_path / entry.name for entry in entries)

    def walk(self, path: str | os.PathLike[str]) -> Iterator[Path]:
        """Every readable regular file below ``path``, in a stable order.

        Linked directories are not entered. A linked file is yielded only if
        its target is inside the allowlisted roots.
        """
        for found, _state in self.walk_states(path):
            yield found

    def walk_states(
        self, path: str | os.PathLike[str], wanted: Callable[[str], bool] | None = None
    ) -> Iterator[tuple[Path, os.stat_result]]:
        """``walk``, with each file's state as the listing saw it.

        For telling which of thousands of files changed without asking about each
        of them again, which would resolve every path against every root. With
        ``wanted``, only files whose real path it accepts are asked about and given:
        a harness keeps many files beside its sessions, and each costs a call.
        """
        # Below a resolved folder, entered without following links, a plain file's path
        # is already its real one, inside the roots. Only a link has to be resolved.
        waiting = [str(self.resolve(path))]
        while waiting:
            directory = waiting.pop()
            try:
                with os.scandir(directory) as found:
                    entries = sorted(found, key=lambda entry: entry.name)
            except OSError:
                continue
            folders = []
            for entry in entries:
                try:
                    if entry.is_symlink():
                        real_path = self.resolve(entry.path)
                        if wanted is not None and not wanted(str(real_path)):
                            continue
                        state = os.stat(real_path)
                        if stat.S_ISREG(state.st_mode):
                            yield real_path, state
                    elif entry.is_dir(follow_symlinks=False):
                        folders.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        if wanted is None or wanted(entry.path):
                            yield Path(entry.path), entry.stat(follow_symlinks=False)
                except (OutsideAllowedRoots, OSError):
                    continue
            waiting.extend(reversed(folders))

    # -- files ---------------------------------------------------------------

    def open(
        self,
        path: str | os.PathLike[str],
        mode: str = "rb",
        *,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> IO:
        """Open a regular file for reading. Any other mode raises :class:`WriteRefused`."""
        if mode not in READ_MODES:
            raise WriteRefused(f"only {sorted(READ_MODES)} are allowed, not {mode!r}")
        real_path = self.resolve(path)
        # Checked before opening: opening a pipe or a device can have effects.
        if not stat.S_ISREG(os.stat(real_path).st_mode):
            raise NotARegularFile(f"not a regular file: {real_path}")
        # O_NOFOLLOW and the second check close the gap in which the file could
        # be swapped for a link or a pipe; O_NONBLOCK keeps a pipe from hanging.
        descriptor = os.open(real_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise NotARegularFile(f"not a regular file: {real_path}")
            if mode == "rb":
                return os.fdopen(descriptor, "rb")
            return os.fdopen(descriptor, "r", encoding=encoding or "utf-8", errors=errors)
        except BaseException:
            os.close(descriptor)
            raise

    # -- SQLite --------------------------------------------------------------

    @contextmanager
    def sqlite_snapshot(self, path: str | os.PathLike[str]) -> Iterator[sqlite3.Connection]:
        """Yield a query-only connection to a private copy of a SQLite database.

        The database, its write-ahead log and any rollback journal are copied
        without taking a lock, so committed rows that are still only in the log
        are visible and the harness is never blocked. The copy is deleted when
        the block exits.

        Raises :class:`SnapshotUnstable` if the database was rewritten during
        each of several copy attempts. A copy taken at a bad moment that slips
        through shows up as ``sqlite3.DatabaseError`` on a query.
        """
        real_path = self.resolve(path)
        workdir = Path(
            tempfile.mkdtemp(prefix="agdash-snapshot-", dir=self._private_snapshot_parent())
        )
        try:
            copy = self._copy_database(real_path, workdir)
            with closing(sqlite3.connect(f"file:{quote(str(copy))}?mode=rw", uri=True)) as db:
                db.execute("PRAGMA query_only = ON")
                # Views and triggers in a file we did not write get no special powers.
                db.execute("PRAGMA trusted_schema = OFF")
                yield db
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _private_snapshot_parent(self) -> Path:
        parent = Path(os.path.realpath(self._snapshot_dir or tempfile.gettempdir()))
        if self._is_inside_roots(parent):
            raise AccessError(f"snapshot directory is inside a harness root: {parent}")
        return parent

    def _sidecars(self, database: Path) -> list[Path]:
        candidates = [database.with_name(database.name + suffix) for suffix in SQLITE_SIDECARS]
        return [sidecar for sidecar in candidates if os.path.lexists(sidecar)]

    def _read_head(self, path: Path, length: int) -> bytes:
        with self.open(path) as source:
            return source.read(length)

    def _database_state(self, database: Path) -> tuple:
        """What must stay the same for a copy of the database to be whole.

        The main file must not change at all. The write-ahead log may grow,
        because SQLite ignores an unfinished tail, but it must not be restarted;
        a restart rewrites its header.
        """
        main = os.stat(database)
        state: list = [
            (main.st_ino, main.st_size, main.st_mtime_ns),
            self._read_head(database, SQLITE_HEADER_BYTES),
        ]
        for suffix in SQLITE_SIDECARS:
            sidecar = database.with_name(database.name + suffix)
            if not os.path.lexists(sidecar):
                state.append(None)
            elif suffix == "-wal":
                state.append(self._read_head(sidecar, WAL_HEADER_BYTES))
            else:
                journal = os.stat(sidecar)
                state.append((journal.st_size, journal.st_mtime_ns))
        return tuple(state)

    def _copy_file(self, source_path: Path, target_path: Path, budget: int) -> int:
        copied = 0
        with self.open(source_path) as source, open(target_path, "xb") as target:
            while chunk := source.read(COPY_CHUNK_BYTES):
                copied += len(chunk)
                if copied > budget:
                    raise SnapshotTooLarge(f"larger than the snapshot limit: {source_path}")
                target.write(chunk)
        return copied

    def _copy_database(self, database: Path, workdir: Path) -> Path:
        for attempt in range(SNAPSHOT_ATTEMPTS):
            attempt_dir = workdir / str(attempt)
            attempt_dir.mkdir()
            try:
                before = self._database_state(database)
                # The main file first: whatever a writer had to save before
                # touching it is then already in the sidecar copied after it.
                budget = self._max_snapshot_bytes
                budget -= self._copy_file(database, attempt_dir / database.name, budget)
                for sidecar in self._sidecars(database):
                    budget -= self._copy_file(sidecar, attempt_dir / sidecar.name, budget)
                if self._database_state(database) == before:
                    return attempt_dir / database.name
            except FileNotFoundError:
                # A sidecar vanished between listing and copying: a checkpoint ran.
                pass
            shutil.rmtree(attempt_dir, ignore_errors=True)
        raise SnapshotUnstable(f"kept changing while being copied: {database}")
