"""The ingest ledger on agdash's own database.

Cursors live in ``ingest_cursors``. Lines are handed to a writer inside the same
transaction that moves the cursor, so either both are stored or neither is.
"""

from collections.abc import Callable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from agdash.db.models import IngestCursor
from agdash.ingest.engine import Cursor, Line

# Called with the open session, the cursor the lines belong to, and the lines.
Writer = Callable[[Session, Cursor, Sequence[Line]], None]


def _to_cursor(row: IngestCursor) -> Cursor:
    return Cursor(row.harness, row.path, row.device or "", row.inode, row.byte_offset, row.size)


class SqlLedger:
    def __init__(self, session_factory: sessionmaker[Session], writer: Writer) -> None:
        self._sessions = session_factory
        self._writer = writer

    def cursor_at(self, harness: str, path: str) -> Cursor | None:
        with self._sessions() as session:
            row = session.scalars(
                select(IngestCursor).where(
                    IngestCursor.harness == harness, IngestCursor.path == path
                )
            ).one_or_none()
            return _to_cursor(row) if row else None

    def cursor_of(self, harness: str, device: str, inode: str) -> Cursor | None:
        with self._sessions() as session:
            row = session.scalars(
                select(IngestCursor).where(
                    IngestCursor.harness == harness,
                    IngestCursor.device == device,
                    IngestCursor.inode == inode,
                )
            ).first()
            return _to_cursor(row) if row else None

    def commit(self, previous: Cursor | None, cursor: Cursor, lines: Sequence[Line]) -> None:
        with self._sessions.begin() as session:
            if lines:
                self._writer(session, cursor, lines)
            if previous is not None and previous.path != cursor.path:
                session.execute(
                    delete(IngestCursor).where(
                        IngestCursor.harness == previous.harness,
                        IngestCursor.path == previous.path,
                    )
                )
            row = session.scalars(
                select(IngestCursor).where(
                    IngestCursor.harness == cursor.harness, IngestCursor.path == cursor.path
                )
            ).one_or_none()
            if row is None:
                row = IngestCursor(harness=cursor.harness, path=cursor.path)
                session.add(row)
            row.device = cursor.device
            row.inode = cursor.inode
            row.byte_offset = cursor.offset
            row.size = cursor.size
