"""Zstandard-compressed files: some harnesses compress the transcripts they archive.

Python 3.14 ships a zstd module; older versions use the ``zstandard`` package.
Both are driven the same way here: frame by frame, with a cap on the output so
a damaged or hostile file cannot exhaust memory.
"""

from collections.abc import Iterable, Iterator
from typing import BinaryIO

try:  # Python 3.14 and later
    from compression import zstd as _zstd

    def _decompressor():
        return _zstd.ZstdDecompressor()

    def compress(data: bytes) -> bytes:
        return _zstd.compress(data)

    class Compressor:
        """One zstd context, reused for frame after frame instead of built for each."""

        def __init__(self) -> None:
            self._context = _zstd.ZstdCompressor()

        def compress(self, data: bytes) -> bytes:
            return self._context.compress(data, mode=_zstd.ZstdCompressor.FLUSH_FRAME)

        def frame(self, pieces: Iterable[bytes]) -> bytes:
            """One frame of ``pieces``, compressed as they come, never joined first."""
            out = [self._context.compress(piece) for piece in pieces]
            out.append(self._context.flush(_zstd.ZstdCompressor.FLUSH_FRAME))
            return b"".join(out)

    _ERRORS: tuple[type[Exception], ...] = (_zstd.ZstdError,)
except ImportError:  # pragma: no cover - exercised on Python 3.13 and older
    import zstandard as _zstd

    def _decompressor():
        return _zstd.ZstdDecompressor().decompressobj()

    def compress(data: bytes) -> bytes:
        return _zstd.ZstdCompressor().compress(data)

    class Compressor:  # type: ignore[no-redef]
        """One zstd context, reused for frame after frame instead of built for each."""

        def __init__(self) -> None:
            self._context = _zstd.ZstdCompressor()

        def compress(self, data: bytes) -> bytes:
            return self._context.compress(data)

        def frame(self, pieces: Iterable[bytes]) -> bytes:
            """One frame of ``pieces``, compressed as they come, never joined first."""
            stream = self._context.compressobj()
            out = [stream.compress(piece) for piece in pieces]
            out.append(stream.flush(_zstd.COMPRESSOBJ_FLUSH_FINISH))
            return b"".join(out)

    _ERRORS = (_zstd.ZstdError,)

SUFFIXES = (".zst",)
CHUNK = 64 * 1024
FEED = 1024


class UnreadableArchive(Exception):
    """The file is not complete, valid zstd data: damaged, or still being written."""


class ArchiveTooLarge(UnreadableArchive):
    """The file decompresses to more than the allowed size."""


def is_compressed(name: str) -> bool:
    return name.endswith(SUFFIXES)


def iter_decompressed(source: BinaryIO, limit: int) -> Iterator[bytes]:
    """Every frame of ``source``, decompressed, a piece at a time; at most ``limit`` bytes.

    Memory stays at a chunk or so whatever the size of the archive. A damaged or
    unfinished archive raises :class:`UnreadableArchive` once the generator reaches
    the damage, so a caller that must not act on half an archive reads it through
    once first.
    """
    decoder = None
    produced = 0
    data, position = b"", 0
    try:
        while True:
            if position >= len(data):
                data, position = source.read(CHUNK), 0
                if not data:
                    break
            # A little input at a time: how much one call can produce is bounded by the
            # input it is given, so a highly compressed file cannot balloon memory.
            feed = memoryview(data)[position : position + FEED]
            position += len(feed)
            if decoder is None:
                decoder = _decompressor()
            piece = decoder.decompress(feed)
            produced += len(piece)
            if produced > limit:
                raise ArchiveTooLarge(f"decompresses to more than {limit} bytes")
            if piece:
                yield piece
            if decoder.eof:
                data, position = decoder.unused_data + data[position:], 0
                decoder = None
    except _ERRORS as error:
        raise UnreadableArchive(str(error)) from error
    if decoder is not None:
        raise UnreadableArchive("the data ends inside a frame")


def decompress(source: BinaryIO, limit: int) -> bytes:
    """Every frame of ``source``, decompressed, in memory; at most ``limit`` bytes."""
    return b"".join(iter_decompressed(source, limit))
