"""The archive of raw harness lines, kept inside agdash's own database.

``Archiving`` wraps an adapter's writer. After the writer has turned a batch of
lines into events, the same lines are redacted, compressed and stored as one
chunk, in the same transaction as the ingest cursor, so a batch is archived
exactly once. Reading a session's raw lines back finds them through the files
its events came from.

Long runs of encoded data are left out of the lines unless a person chose to
keep them: images, signatures and encrypted reasoning, which nothing agdash shows
can use, which do not compress, and which were most of what the archive held.
A marker with the run's length stands in for each. ``Trimmer`` does the same to
chunks archived before, when a person asks, a few at a time.

    python -m agdash.archive           what is archived, and what a trim would look at
    python -m agdash.archive --trim    take encoded data out of what is archived
"""

import argparse
import io
import logging
import re
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash import saved
from agdash.compact import release
from agdash.config import database_url
from agdash.db.migrate import sync_database_url
from agdash.db.models import RawChunk, SessionFile
from agdash.ingest.compressed import Compressor, decompress
from agdash.ingest.engine import Cursor, Line
from agdash.redaction import redact_text

Writer = Callable[[DbSession, Cursor, Sequence[Line]], None]
MAX_CHUNK_BYTES = 256 * 1024 * 1024

log = logging.getLogger("agdash.archive")

# A run this long of nothing but the characters base64 is written in is encoded data,
# not text: no word, path or identifier is 400 characters without a space or a dot.
ENCODED_RUN = 400
_ENCODED = re.compile(rf"[A-Za-z0-9+/_=-]{{{ENCODED_RUN},}}")


def leave_out_encoded(text: str) -> str:
    """``text`` with each long run of encoded data replaced by a marker of its length.

    The marker holds characters a run cannot, so doing this twice changes nothing more.
    """
    return _ENCODED.sub(_marker, text)


def _marker(run: re.Match[str]) -> str:
    found = run.group()
    # Base64 holds a slash once in sixty-four characters; a long path holds many more.
    if found.count("/") * 16 > len(found):
        return found
    return f"[OMITTED:{len(found)}]"


class Archiving:
    """An adapter writer that also keeps the redacted, compressed lines it was given."""

    def __init__(
        self,
        inner: Writer,
        harness: str,
        *,
        enabled: bool | Callable[[DbSession], bool] = True,
        keep_encoded: bool | Callable[[DbSession], bool] = False,
    ) -> None:
        """Either choice may be asked of the store each time, so a saved change holds at once."""
        self._inner = inner
        self._harness = harness
        self._enabled = enabled
        self._keep_encoded = keep_encoded
        self._compressor = Compressor()

    def __call__(self, db: DbSession, cursor: Cursor, lines: Sequence[Line]) -> None:
        self._inner(db, cursor, lines)
        if not lines:
            return
        if not (self._enabled(db) if callable(self._enabled) else self._enabled):
            return
        whole = self._keep_encoded(db) if callable(self._keep_encoded) else self._keep_encoded
        kept = (redact_text(line.data.decode("utf-8", errors="replace")) for line in lines)
        data = _frame(self._compressor, kept if whole else map(leave_out_encoded, kept))
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


def _frame(compressor: Compressor, lines) -> bytes:
    """Lines as one compressed chunk, fed line by line: the same lines give the same bytes."""
    return compressor.frame((line + "\n").encode("utf-8") for line in lines)


def source_files(db: DbSession, session_id: int) -> list[str]:
    """The files a session's events were read from."""
    return sorted(db.scalars(select(SessionFile.path).where(SessionFile.session_id == session_id)))


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


# -- trimming what is archived already -----------------------------------------------

TRIM_BYTES = 32 * 1024 * 1024  # stored bytes looked at in one transaction
TRIM_PAUSE_SECONDS = 0.2
LOOK_SECONDS = 1.0


@dataclass(frozen=True)
class Trimmed:
    chunks: int
    saved_bytes: int
    finished: bool


def untrimmed(db: DbSession) -> int:
    """How many archived chunks have not been looked at for encoded data yet."""
    through = (saved.last_trim(db) or {}).get("through", 0)
    return db.scalar(select(func.count()).select_from(RawChunk).where(RawChunk.id > through)) or 0


def trim_step(db: DbSession, compressor: Compressor, *, now: datetime) -> Trimmed:
    """Take encoded data out of the next few chunks, and note how far that got.

    A chunk is written again only when that makes it smaller. Its lines are fed to the
    compressor as the archive feeds them, so the same lines read again still match it.
    """
    through = (saved.last_trim(db) or {}).get("through", 0)
    ids = db.execute(
        select(RawChunk.id, func.length(RawChunk.data))
        .where(RawChunk.id > through)
        .order_by(RawChunk.id)
        .limit(64)
    ).all()
    looked = spared = seen = 0
    for chunk_id, size in ids:
        if looked and seen + size > TRIM_BYTES:
            break
        chunk = db.get(RawChunk, chunk_id)
        text = decompress(io.BytesIO(chunk.data), MAX_CHUNK_BYTES).decode("utf-8")
        lean = leave_out_encoded(text)
        if lean != text:
            # Each line ended in a newline when it was archived; none holds one inside.
            data = _frame(compressor, lean.split("\n")[:-1])
            if len(data) < len(chunk.data):
                spared += len(chunk.data) - len(data)
                chunk.data = data
        db.flush()
        db.expunge(chunk)
        looked, seen, through = looked + 1, seen + size, chunk_id
    if looked:
        saved.note_trim(db, at=now, through=through, chunks=looked, saved_bytes=spared)
    return Trimmed(looked, spared, finished=looked == len(ids) and len(ids) < 64)


def trim_in_steps(
    sessions: sessionmaker[DbSession],
    *,
    stop: threading.Event | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    pause: float | None = None,
) -> Trimmed:
    """Trim every chunk not yet looked at, a few in each transaction so ingest goes on."""
    compressor = Compressor()
    chunks = spared = 0
    while not (stop is not None and stop.is_set()):
        with sessions.begin() as db:
            step = trim_step(db, compressor, now=clock())
        chunks, spared = chunks + step.chunks, spared + step.saved_bytes
        if step.finished:
            return Trimmed(chunks, spared, True)
        if stop is not None:
            stop.wait(TRIM_PAUSE_SECONDS if pause is None else pause)
    return Trimmed(chunks, spared, False)


class Trimmer:
    """Trims the archive inside the service when a person asks; never by itself."""

    def __init__(self, sessions: sessionmaker[DbSession]) -> None:
        self._sessions = sessions
        self._asked = threading.Event()
        self._lock = threading.Lock()
        self._running = False

    def running(self) -> bool:
        with self._lock:
            return self._running or self._asked.is_set()

    def ask(self) -> None:
        self._asked.set()

    def run(self, stop: threading.Event) -> list[threading.Thread]:
        def loop() -> None:
            while not stop.is_set():
                if not self._asked.wait(LOOK_SECONDS) or stop.is_set():
                    continue
                with self._lock:
                    self._running = True
                try:
                    done = trim_in_steps(self._sessions, stop=stop)
                    log.info(
                        "archive: looked at %d chunks, %d bytes less", done.chunks, done.saved_bytes
                    )
                    engine = self._sessions.kw.get("bind")
                    if engine is not None:
                        release(engine, stop=stop)
                except Exception:
                    log.exception("trimming the archive failed; ask again to go on")
                finally:
                    self._asked.clear()
                    with self._lock:
                        self._running = False

        thread = threading.Thread(target=loop, name="agdash-archive", daemon=True)
        thread.start()
        return [thread]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agdash-archive", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--trim", action="store_true", help="take encoded data out; it cannot be undone"
    )
    args = parser.parse_args(argv)
    engine = create_engine(sync_database_url(database_url()))
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions() as db:
            chunks = db.scalar(select(func.count()).select_from(RawChunk)) or 0
            waiting, keeps = untrimmed(db), saved.keep_encoded(db)[0]
        print(f"Archived: {chunks} chunks, {waiting} not yet looked at for encoded data.")
        if keeps:
            print("Encoded data is kept, so there is nothing to take out.")
            return 0
        if not args.trim:
            print("Pass --trim to take encoded data out of them. It cannot be undone.")
            return 0
        done = trim_in_steps(sessions)
        print(f"Looked at {done.chunks} chunks; the archive is {done.saved_bytes} bytes smaller.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
