"""The archive of raw harness lines, kept inside agdash's own database.

``Archiving`` wraps an adapter's writer. After the writer has turned a batch of
lines into events, the same lines are redacted, compressed and stored as one
chunk, in the same transaction as the ingest cursor, so a batch is archived
exactly once. Reading a session's raw lines back finds them through the files
its events came from.
"""

import io
from collections.abc import Callable, Iterator, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from agdash.db.models import Event, RawChunk
from agdash.ingest.compressed import compress, decompress
from agdash.ingest.engine import Cursor, Line
from agdash.redaction import redact_text

Writer = Callable[[DbSession, Cursor, Sequence[Line]], None]
MAX_CHUNK_BYTES = 256 * 1024 * 1024


class Archiving:
    """An adapter writer that also keeps the redacted, compressed lines it was given."""

    def __init__(self, inner: Writer, harness: str, *, enabled: bool = True) -> None:
        self._inner = inner
        self._harness = harness
        self._enabled = enabled

    def __call__(self, db: DbSession, cursor: Cursor, lines: Sequence[Line]) -> None:
        self._inner(db, cursor, lines)
        if not self._enabled or not lines:
            return
        text = "".join(
            redact_text(line.data.decode("utf-8", errors="replace")) + "\n" for line in lines
        )
        data = compress(text.encode("utf-8"))
        first, end = lines[0].offset, lines[-1].end
        same = db.scalars(
            select(RawChunk.data).where(
                RawChunk.source_file == cursor.path,
                RawChunk.first_offset == first,
                RawChunk.end_offset == end,
            )
        ).all()
        if data in same:
            return  # read again after a restart or a rewrite with the same content
        db.add(
            RawChunk(
                harness=self._harness,
                source_file=cursor.path,
                first_offset=first,
                end_offset=end,
                line_count=len(lines),
                data=data,
            )
        )


def source_files(db: DbSession, session_id: int) -> list[str]:
    return sorted(
        path
        for path in db.scalars(
            select(Event.source_file).where(Event.session_id == session_id).distinct()
        )
        if path
    )


def raw_lines(db: DbSession, session_id: int) -> Iterator[str]:
    """A session's archived lines, file by file in reading order, one chunk at a time."""
    for path in source_files(db, session_id):
        ids = db.scalars(
            select(RawChunk.id)
            .where(RawChunk.source_file == path)
            .order_by(RawChunk.first_offset, RawChunk.id)
        ).all()
        covered = -1
        for chunk_id in ids:
            chunk = db.get(RawChunk, chunk_id)
            if chunk is None or chunk.end_offset <= covered:
                continue  # the same stretch of the file, archived twice
            text = decompress(io.BytesIO(chunk.data), MAX_CHUNK_BYTES).decode("utf-8")
            yield from text.splitlines()
            covered = chunk.end_offset
            db.expunge(chunk)
