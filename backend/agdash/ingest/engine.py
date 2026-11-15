"""Read new lines from the files harnesses append to, each exactly once.

Every file has a cursor: where it was read up to, and which file it is,
identified by device and inode so that a file renamed into an archive folder
keeps its place. A pass reads only complete lines from the cursor onwards. A
line still being written waits for its newline. The lines and the advanced
cursor are stored together by the ledger, in one transaction, so a crash
between the two cannot count a line twice.

A file that is replaced or truncated is read again from the start. Writers are
expected to store lines idempotently (by session and byte offset), so a second
read adds nothing.

A compressed file is an archive, written once. It is read whole, decompressed,
and its offsets are those of the decompressed text, so an archived copy of a
transcript produces the same offsets as the original did. Its cursor records
the compressed size; the file is read again only if that changes.

Every read goes through the read-only access layer.
"""

import fnmatch
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from agdash.access import OutsideAllowedRoots, ReadOnlyAccess
from agdash.ingest.compressed import UnreadableArchive, decompress, is_compressed

DEFAULT_BATCH_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_LINE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
NEWLINE = b"\n"


@dataclass(frozen=True)
class Cursor:
    """How far a file has been read. ``device`` and ``inode`` are decimal text."""

    harness: str
    path: str
    device: str
    inode: str
    offset: int
    size: int


@dataclass(frozen=True)
class Line:
    """One complete line: its bytes without the newline, and where it sits in the file."""

    offset: int
    end: int
    data: bytes


class Ledger(Protocol):
    """Where cursors live and where lines go."""

    def cursor_at(self, harness: str, path: str) -> Cursor | None: ...

    def cursor_of(self, harness: str, device: str, inode: str) -> Cursor | None: ...

    def commit(self, previous: Cursor | None, cursor: Cursor, lines: Sequence[Line]) -> None:
        """Store ``lines`` and replace ``previous`` with ``cursor``, all or nothing."""
        ...


@dataclass
class FileReport:
    path: str
    lines: int = 0
    moved_from: str | None = None
    restarted: bool = False
    oversized_lines: int = 0
    waiting_bytes: int = 0
    unreadable: bool = False  # a compressed file that is damaged or still being written


@dataclass
class ScanReport:
    files: list[FileReport] = field(default_factory=list)
    vanished: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)

    @property
    def lines(self) -> int:
        return sum(report.lines for report in self.files)


def discover_files(
    access: ReadOnlyAccess, roots: Iterable[Path], patterns: Sequence[str]
) -> list[Path]:
    """Files below ``roots`` whose path relative to their root matches one of ``patterns``."""
    found = []
    for root in roots:
        if not access.exists(root):
            continue
        real_root = access.resolve(root)
        for path in access.walk(root):
            relative = os.path.relpath(path, real_root)
            if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                found.append(path)
    return sorted(set(found))


def split_lines(data: bytes, start: int) -> tuple[list[Line], int]:
    """Complete lines in ``data``, which begins at file offset ``start``, and where they end."""
    lines = []
    position = 0
    while True:
        newline = data.find(NEWLINE, position)
        if newline < 0:
            break
        lines.append(Line(start + position, start + newline + 1, data[position:newline]))
        position = newline + 1
    return lines, start + position


class Ingestor:
    """Reads one harness's files and hands their new lines to a ledger."""

    def __init__(
        self,
        access: ReadOnlyAccess,
        ledger: Ledger,
        *,
        harness: str,
        batch_bytes: int = DEFAULT_BATCH_BYTES,
        max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
        max_archive_bytes: int = DEFAULT_MAX_ARCHIVE_BYTES,
    ) -> None:
        if max_line_bytes < batch_bytes:
            raise ValueError("a line limit smaller than a batch would split lines")
        self._access = access
        self._ledger = ledger
        self._harness = harness
        self._batch = batch_bytes
        self._max_line = max_line_bytes
        self._max_archive = max_archive_bytes

    def scan(self, paths: Iterable[Path]) -> ScanReport:
        report = ScanReport()
        for path in paths:
            try:
                report.files.append(self.scan_file(path))
            except FileNotFoundError:
                # Gone between listing and reading: moved or deleted. A move is
                # picked up at its new path by inode on a later pass.
                report.vanished.append(str(path))
            except OutsideAllowedRoots:
                report.refused.append(str(path))
        return report

    def scan_file(self, path: Path) -> FileReport:
        found = self._access.stat(path)
        path_text = str(self._access.resolve(path))
        device, inode = str(found.st_dev), str(found.st_ino)
        report = FileReport(path_text)
        if is_compressed(path_text):
            return self._scan_archive(path, path_text, found, report)

        previous = self._ledger.cursor_at(self._harness, path_text)
        if previous is None or previous.inode != inode or previous.device != device:
            moved = self._ledger.cursor_of(self._harness, device, inode)
            if moved is not None and moved.path != path_text and self._was_moved(moved, path_text):
                report.moved_from = moved.path
                stale = previous
                previous = moved
                if stale is not None:  # another file used to live at this path
                    report.restarted = True
            elif previous is not None:
                report.restarted = True  # replaced at the same path
                previous = None

        offset = previous.offset if previous else 0
        if offset > found.st_size:  # truncated since the last pass
            report.restarted = True
            offset = 0

        while True:
            lines, end, oversized, waiting = self._read(path, offset, found.st_size)
            report.oversized_lines += oversized
            report.waiting_bytes = waiting
            cursor = Cursor(self._harness, path_text, device, inode, end, found.st_size)
            if lines or end != offset or self._cursor_changed(previous, cursor):
                self._ledger.commit(previous, cursor, lines)
                previous = cursor
            report.lines += len(lines)
            if end == offset or end >= found.st_size:
                return report
            offset = end

    def _scan_archive(
        self, path: Path, path_text: str, found: os.stat_result, report: FileReport
    ) -> FileReport:
        device, inode = str(found.st_dev), str(found.st_ino)
        previous = self._ledger.cursor_at(self._harness, path_text)
        same_file = previous is not None and (previous.device, previous.inode) == (device, inode)
        if same_file and previous.size == found.st_size:
            return report  # read already, and unchanged since
        report.restarted = previous is not None
        try:
            with self._access.open(path) as source:
                data = decompress(source, self._max_archive)
        except UnreadableArchive:
            report.unreadable = True
            report.waiting_bytes = found.st_size
            return report
        lines, end = split_lines(data, 0)
        if end < len(data):  # an archive is complete, so its last line is too
            lines.append(Line(end, len(data), data[end:]))
            end = len(data)
        kept = [line for line in lines if len(line.data) < self._max_line]
        report.oversized_lines = len(lines) - len(kept)
        cursor = Cursor(self._harness, path_text, device, inode, end, found.st_size)
        self._ledger.commit(previous, cursor, kept)
        report.lines = len(kept)
        return report

    def _was_moved(self, cursor: Cursor, path_text: str) -> bool:
        """Whether the file under ``cursor`` really moved to ``path_text``.

        An inode is reused once its file is deleted, so a matching inode alone
        could take a new, unrelated file for a moved one and skip its start.
        A move keeps the file name, as an archive move does, and leaves nothing
        with that inode at the old path.
        """
        if os.path.basename(cursor.path) != os.path.basename(path_text):
            return False
        try:
            still = self._access.stat(cursor.path)
        except (FileNotFoundError, OutsideAllowedRoots):
            return True
        return str(still.st_ino) != cursor.inode or str(still.st_dev) != cursor.device

    @staticmethod
    def _cursor_changed(previous: Cursor | None, cursor: Cursor) -> bool:
        return previous is None or previous != cursor

    def _read(self, path: Path, offset: int, size: int) -> tuple[list[Line], int, int, int]:
        """Lines from one batch starting at ``offset``, with oversized lines skipped.

        Returns the lines, where reading should continue, how many oversized
        lines were skipped, and how many bytes wait for their newline.
        """
        if offset >= size:
            return [], offset, 0, 0
        with self._access.open(path) as source:
            source.seek(offset)
            data = source.read(min(self._batch, size - offset))
            lines, end = split_lines(data, offset)
            if lines or offset + len(data) >= size:
                return lines, end, 0, offset + len(data) - end
            # No newline in a whole batch: one long line. Read on to its end.
            consumed = len(data)
            while consumed < self._max_line:
                more = source.read(min(self._batch, size - offset - consumed))
                if not more:
                    return [], offset, 0, consumed  # still being written
                newline = more.find(NEWLINE)
                if newline >= 0:
                    data += more[: newline + 1]
                    lines, end = split_lines(data, offset)
                    return lines, end, 0, 0
                data += more
                consumed += len(more)
            # Longer than any line we accept: skip to its end without keeping it.
            while True:
                more = source.read(self._batch)
                if not more:
                    return [], offset, 0, consumed
                newline = more.find(NEWLINE)
                consumed += newline + 1 if newline >= 0 else len(more)
                if newline >= 0:
                    return [], offset + consumed, 1, 0
