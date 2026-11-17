"""When to scan: on file events, with polling where events are not available.

File events only say which files to look at; the scan itself decides what is
new. So a missed or duplicated event costs at most a late or an empty scan, and
a full poll at a slower interval catches anything events missed.

The first read of everything there is can take many minutes. It runs in short
slices, most urgent file first, and between slices the loop reads what the
watcher named. So a session being written now shows up, and keeps up, while
older files are still waiting their turn.

Watching only reads: the operating system reports changes in the watched
directories, and nothing is written there.
"""

import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from pathlib import Path

import watchfiles

DEFAULT_POLL_SECONDS = 30.0
DEFAULT_DEBOUNCE_MS = 500
# How long the first read runs before the loop looks at what changed meanwhile.
DEFAULT_SLICE_SECONDS = 1.0
# The loop's longest sleep, so it notices ``stop`` soon.
_WAKE_SECONDS = 0.25
# Every so many full polls, files are read whether or not they look changed.
_TRUST_POLLS = 20


class _Watcher:
    """File events from some directories, gathered on a thread of their own.

    The loop collects them between scans, so a change made during a scan waits
    its turn instead of being missed.
    """

    def __init__(self, directories: frozenset[Path], *, force_polling: bool, debounce_ms: int):
        self.directories = directories
        self.failed = False
        self._changes: queue.SimpleQueue[set[Path]] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._watch, args=(force_polling, debounce_ms), name="agdash-watch", daemon=True
        )
        self._thread.start()

    def _watch(self, force_polling: bool, debounce_ms: int) -> None:
        try:
            for batch in watchfiles.watch(
                *sorted(str(directory) for directory in self.directories),
                stop_event=self._stop,
                force_polling=force_polling,
                debounce=debounce_ms,
                raise_interrupt=False,
            ):
                self._changes.put({Path(path) for _, path in batch})
        except OSError:
            # Watching is not possible here, for example at the inotify watch limit.
            self.failed = True

    def take(self, seconds: float, stop: threading.Event) -> set[Path]:
        """Everything gathered so far, waiting up to ``seconds`` for the first of it."""
        changed: set[Path] = set()
        deadline = time.monotonic() + seconds
        while not changed and not stop.is_set() and not self.failed:
            try:
                while True:
                    changed |= self._changes.get_nowait()
            except queue.Empty:
                pass
            left = deadline - time.monotonic()
            if changed or left <= 0:
                break
            try:
                changed |= self._changes.get(timeout=min(left, _WAKE_SECONDS))
            except queue.Empty:
                pass
        return changed

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)


def run(
    *,
    directories: Iterable[Path] | Callable[[], Iterable[Path]],
    discover: Callable[[], list[Path]],
    scan: Callable[[list[Path]], object],
    stop: threading.Event,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    full_seconds: float | None = None,
    force_polling: bool = False,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    match: Callable[[Iterable[Path]], list[Path]] | None = None,
    order: Callable[[list[Path]], list[Path]] | None = None,
    progress: Callable[[int, int], object] | None = None,
    signature: Callable[[Path], object] | None = None,
    slice_seconds: float = DEFAULT_SLICE_SECONDS,
) -> None:
    """Read everything once, then read what changes, until ``stop`` is set.

    ``order`` puts the files of the first read most urgent first, and
    ``progress`` hears how many of them are done, and when all are. ``match``
    picks the session files among changed paths; without it they are looked up
    in a fresh ``discover()``. A full ``discover()`` and scan runs every
    ``full_seconds`` (``poll_seconds`` unless given) while events arrive, and
    every ``poll_seconds`` without them.

    ``signature`` tells a file's state without reading it (its size and when it
    was written, say). With it a full poll scans only the files whose signature
    differs from the one they had when last scanned, so its cost follows what
    changed and not how much there is. A scan that returns False counts as not
    done, and its files are scanned again at the next full poll.

    Only what exists is watched; ``directories`` (folders, and single files too)
    may be a function, asked again at each full poll, so a folder made later is
    watched from then on. When the system refuses a watch, for example because
    the inotify watch limit is reached, the loop keeps polling.
    """
    if callable(directories):
        listed = directories
    else:
        fixed = tuple(directories)
        listed = lambda: fixed  # noqa: E731
    full_seconds = poll_seconds if full_seconds is None else full_seconds
    watcher: _Watcher | None = None
    refused = False

    def rewatch() -> None:
        nonlocal watcher, refused
        if watcher is not None and watcher.failed:
            refused = True
        wanted = frozenset(Path(d) for d in listed() if Path(d).exists())
        if watcher is not None and (refused or watcher.directories != wanted):
            watcher.close()
            watcher = None
        if watcher is None and wanted and not refused:
            watcher = _Watcher(wanted, force_polling=force_polling, debounce_ms=debounce_ms)

    def among(changed: set[Path]) -> list[Path]:
        if match is not None:
            return match(changed)
        candidates = set(discover())
        return sorted(
            path for path in changed if path in candidates or path.resolve() in candidates
        )

    scanned: dict[Path, object] = {}
    polls = 0

    def mark(path: Path) -> object:
        try:
            return signature(path) if signature is not None else None
        except OSError:
            return None  # gone: the scan says so itself

    def read(paths: list[Path]) -> None:
        # Taken before the scan, so a write during it shows as a change next time.
        marks = {path: mark(path) for path in paths} if signature is not None else {}
        if scan(paths) is not False:
            scanned.update(marks)

    def full_poll() -> None:
        nonlocal scanned, polls
        found = discover()
        polls += 1
        if signature is None or polls % _TRUST_POLLS == 0:
            scanned = {}
            return read(found)
        scanned = {path: scanned[path] for path in found if path in scanned}
        read([path for path in found if path not in scanned or mark(path) != scanned[path]])

    # Watching starts first, so nothing written during the first read is missed.
    rewatch()
    try:
        found = discover()
        pending = deque(order(found) if order is not None else found)
        total = len(pending)
        if progress is not None:
            progress(0, total)
        last_full = time.monotonic()
        while not stop.is_set():
            watching = watcher is not None and not watcher.failed
            due = last_full + (full_seconds if watching else poll_seconds) - time.monotonic()
            if watching:
                wait = 0.0 if pending else min(poll_seconds, max(due, 0.0))
                changed = watcher.take(wait, stop)
            else:
                changed = set()
                if not pending:
                    stop.wait(max(due, 0.0))
            if stop.is_set():
                return
            if changed:
                read(among(changed))
            if pending:
                until = time.monotonic() + slice_seconds
                while pending and not stop.is_set():
                    read([pending.popleft()])
                    if time.monotonic() >= until:
                        break
                if progress is not None:
                    progress(total - len(pending), total)
                if not pending:
                    last_full = time.monotonic()
                continue
            if time.monotonic() - last_full >= (full_seconds if watching else poll_seconds):
                full_poll()
                last_full = time.monotonic()
                rewatch()
            elif watcher is not None and watcher.failed:
                rewatch()
    finally:
        if watcher is not None:
            watcher.close()
