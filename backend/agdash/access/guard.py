"""Proof that a run left harness directories exactly as it found them.

Record every entry under the harness roots before the run and again after it;
any entry that appeared, vanished or changed is a failure. This deliberately
does not use :mod:`agdash.access.readonly`, so a fault there cannot hide here.

Only meaningful while no harness is writing to the directories being checked.
"""

import os
import stat
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EntryState:
    kind: str
    mode: int
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Difference:
    root: str
    path: str
    change: str
    detail: str = ""

    def __str__(self) -> str:
        where = os.path.join(self.root, self.path)
        return f"{self.change}: {where}" + (f" ({self.detail})" if self.detail else "")


class HarnessDataChanged(AssertionError):
    """Something under a harness root is not as it was."""

    def __init__(self, differences: list[Difference]) -> None:
        self.differences = differences
        lines = "\n".join(f"  {difference}" for difference in differences)
        super().__init__(f"{len(differences)} change(s) under harness roots:\n{lines}")


def _kind(mode: int) -> str:
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "link"
    return "other"


def _state(path: str) -> EntryState:
    found = os.lstat(path)
    kind = _kind(found.st_mode)
    # A directory's size is the file system's business and differs between them.
    size = found.st_size if kind != "directory" else 0
    return EntryState(kind, stat.S_IMODE(found.st_mode), size, found.st_mtime_ns)


def snapshot_tree(root: str | os.PathLike[str]) -> dict[str, EntryState]:
    """The state of ``root`` and of everything below it, keyed by relative path.

    Links are recorded, not followed. A root that does not exist is an empty
    snapshot, so creating it counts as a change.
    """
    top = os.fspath(root)
    if not os.path.lexists(top):
        return {}
    entries = {".": _state(top)}
    if not os.path.isdir(top) or os.path.islink(top):
        return entries
    for directory, subdirectories, names in os.walk(top, followlinks=False):
        for name in [*subdirectories, *names]:
            path = os.path.join(directory, name)
            entries[os.path.relpath(path, top)] = _state(path)
    return entries


def compare(
    root: str | os.PathLike[str],
    before: dict[str, EntryState],
    after: dict[str, EntryState],
) -> list[Difference]:
    top = os.fspath(root)
    differences = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old is None:
            differences.append(Difference(top, path, "created"))
        elif new is None:
            differences.append(Difference(top, path, "removed"))
        elif old != new:
            changed = [
                name
                for name in ("kind", "mode", "size", "mtime_ns")
                if getattr(old, name) != getattr(new, name)
            ]
            differences.append(Difference(top, path, "changed", ", ".join(changed)))
    return differences


@contextmanager
def assert_untouched(roots: Iterable[str | os.PathLike[str]]) -> Iterator[None]:
    """Raise :class:`HarnessDataChanged` if the block changes anything under ``roots``."""
    tops = [Path(root) for root in roots]
    before = [snapshot_tree(top) for top in tops]
    yield
    differences = [
        difference
        for top, earlier in zip(tops, before, strict=True)
        for difference in compare(top, earlier, snapshot_tree(top))
    ]
    if differences:
        raise HarnessDataChanged(differences)
