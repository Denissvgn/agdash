"""When to scan: on file events, with polling where events are not available.

File events only say which files to look at; the scan itself decides what is
new. So a missed or duplicated event costs at most a late or an empty scan, and
a full poll at a slower interval catches anything events missed.

Watching only reads: the operating system reports changes in the watched
directories, and nothing is written there.
"""

import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import watchfiles

DEFAULT_POLL_SECONDS = 30.0
DEFAULT_DEBOUNCE_MS = 500


def run(
    *,
    directories: Iterable[Path],
    discover: Callable[[], list[Path]],
    scan: Callable[[list[Path]], object],
    stop: threading.Event,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    force_polling: bool = False,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
) -> None:
    """Scan everything once, then scan what changes until ``stop`` is set.

    Only directories that exist are watched; a harness installed later is
    found by the next full poll. When the system refuses a watch, for example
    because the inotify watch limit is reached, the loop keeps polling.
    """
    scan(discover())
    last_full = time.monotonic()
    watched = [str(d) for d in directories if d.is_dir()]
    while not stop.is_set():
        changed: set[Path] = set()
        if watched:
            try:
                for batch in watchfiles.watch(
                    *watched,
                    stop_event=stop,
                    rust_timeout=int(poll_seconds * 1000),
                    yield_on_timeout=True,
                    force_polling=force_polling,
                    debounce=debounce_ms,
                    raise_interrupt=False,
                ):
                    changed = {Path(path) for _, path in batch}
                    break
            except OSError:
                # Watching is not possible here; poll instead.
                watched = []
        else:
            stop.wait(poll_seconds)
        if stop.is_set():
            return
        if time.monotonic() - last_full >= poll_seconds or not watched:
            scan(discover())
            last_full = time.monotonic()
        elif changed:
            candidates = set(discover())
            scan(
                sorted(
                    path for path in changed if path in candidates or path.resolve() in candidates
                )
            )
