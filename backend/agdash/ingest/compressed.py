"""Zstandard-compressed files: some harnesses compress the transcripts they archive.

Python 3.14 ships a zstd module; older versions use the ``zstandard`` package.
Both are driven the same way here: frame by frame, with a cap on the output so
a damaged or hostile file cannot exhaust memory.
"""

from typing import BinaryIO

try:  # Python 3.14 and later
    from compression import zstd as _zstd

    def _decompressor():
        return _zstd.ZstdDecompressor()

    def compress(data: bytes) -> bytes:
        return _zstd.compress(data)

    _ERRORS: tuple[type[Exception], ...] = (_zstd.ZstdError,)
except ImportError:  # pragma: no cover - exercised on Python 3.13 and older
    import zstandard as _zstd

    def _decompressor():
        return _zstd.ZstdDecompressor().decompressobj()

    def compress(data: bytes) -> bytes:
        return _zstd.ZstdCompressor().compress(data)

    _ERRORS = (_zstd.ZstdError,)

SUFFIXES = (".zst",)
CHUNK = 64 * 1024


class UnreadableArchive(Exception):
    """The file is not complete, valid zstd data: damaged, or still being written."""


class ArchiveTooLarge(UnreadableArchive):
    """The file decompresses to more than the allowed size."""


def is_compressed(name: str) -> bool:
    return name.endswith(SUFFIXES)


def decompress(source: BinaryIO, limit: int) -> bytes:
    """Every frame of ``source``, decompressed; at most ``limit`` bytes."""
    output = bytearray()
    decoder = None
    pending = b""
    try:
        while True:
            chunk = pending or source.read(CHUNK)
            pending = b""
            if not chunk:
                break
            if decoder is None:
                decoder = _decompressor()
            output += decoder.decompress(chunk)
            if len(output) > limit:
                raise ArchiveTooLarge(f"decompresses to more than {limit} bytes")
            if decoder.eof:
                pending = decoder.unused_data
                decoder = None
    except _ERRORS as error:
        raise UnreadableArchive(str(error)) from error
    if decoder is not None:
        raise UnreadableArchive("the data ends inside a frame")
    return bytes(output)
