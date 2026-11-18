"""One process at a time has a store to itself.

The service holds this lock for as long as it runs. A second copy started beside
it, by the app or by hand while the first is still bringing a large store up to
date, ends at once instead of collecting into the same store. Compacting by hand
takes the same lock, so it waits for the service to be stopped, whether or not
the service has got as far as answering.

Commands that only read, or that write a little beside a running service
(``backfill``, ``connect``, ``retention``, ``export``, ``archive``), do not take it.

The lock is an advisory one on an empty file beside the store. The system drops it
when the process ends, however it ends, so nothing is left to clean up. It is
agdash's own file: one that would lie inside a harness directory is refused.
"""

import fcntl
import os
from pathlib import Path

from agdash.platform.roots import is_inside

FALLBACK_NAME = "service.lock"


class InsideHarness(ValueError):
    """The lock would be made inside a harness directory, where agdash writes nothing."""


def lock_file(database_file: Path | None, data_dir: Path) -> Path:
    """Beside a store that is a file; in the data folder for any other store."""
    if database_file is not None:
        return database_file.with_name(f"{database_file.name}.lock")
    return data_dir / FALLBACK_NAME


class Held:
    """The lock, for as long as this is neither released nor the process ended."""

    def __init__(self, descriptor: int) -> None:
        self._descriptor: int | None = descriptor

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            os.close(descriptor)


def hold(path: Path, *, forbidden_roots: tuple[Path, ...] = ()) -> Held | None:
    """Take the lock at ``path`` without waiting; None when another process has it."""
    if is_inside(path, forbidden_roots):
        raise InsideHarness(f"refusing to make a lock inside a harness directory: {path}")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    except BaseException:
        os.close(descriptor)
        raise
    return Held(descriptor)
