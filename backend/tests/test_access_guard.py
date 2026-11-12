"""The guard: a full read of a harness tree changes nothing, and any change is caught."""

import os
import sqlite3

import pytest
from conftest import needs_file_permissions

from agdash.access.guard import HarnessDataChanged, assert_untouched, snapshot_tree

DATABASE_SUFFIXES = (".db", ".sqlite")


def read_everything(access, root) -> dict[str, int]:
    """Stand in for a collector run: use every kind of read the access layer has."""
    seen = {"files": 0, "bytes": 0, "databases": 0, "rows": 0, "directories": 0}
    pending = [root]
    while pending:
        directory = pending.pop()
        seen["directories"] += 1
        for entry in access.list_dir(directory):
            if entry.is_dir() and not entry.is_symlink():
                pending.append(entry)
    for path in access.walk(root):
        assert access.exists(path)
        seen["files"] += 1
        with access.open(path) as source:
            seen["bytes"] += len(source.read())
        assert access.stat(path).st_size >= 0
        if path.name.endswith(DATABASE_SUFFIXES):
            with access.sqlite_snapshot(path) as snapshot:
                tables = snapshot.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                for (table,) in tables.fetchall():
                    seen["rows"] += snapshot.execute(f'SELECT count(*) FROM "{table}"').fetchone()[
                        0
                    ]
            seen["databases"] += 1
    return seen


def test_a_full_read_leaves_the_harness_tree_untouched(harness):
    with assert_untouched([harness.root]):
        seen = read_everything(harness.access(), harness.root)
    # The guard is only worth something if the run really read the tree.
    assert seen["databases"] == 2
    assert seen["rows"] == 402
    assert seen["files"] >= 8
    assert seen["bytes"] > 100_000
    assert seen["directories"] >= 8


@needs_file_permissions
def test_a_full_read_needs_no_write_permission_anywhere(harness):
    for directory, _subdirectories, names in os.walk(harness.root):
        for name in names:
            path = os.path.join(directory, name)
            if not os.path.islink(path):
                os.chmod(path, 0o400)
        os.chmod(directory, 0o500)
    try:
        with assert_untouched([harness.root]):
            seen = read_everything(harness.access(), harness.root)
        assert seen["rows"] == 402
    finally:
        for directory, _subdirectories, _names in os.walk(harness.root):
            os.chmod(directory, 0o700)


def test_opening_a_database_where_it_lies_is_caught(harness):
    """The mistake the snapshot reader exists to prevent, seen by the guard."""
    with pytest.raises(HarnessDataChanged) as raised, assert_untouched([harness.root]):
        in_place = sqlite3.connect(f"file:{harness.wal_database}?mode=ro", uri=True)
        try:
            in_place.execute("SELECT body FROM notes").fetchall()
        finally:
            in_place.close()
    created = [d.path for d in raised.value.differences if d.change == "created"]
    assert os.path.join("state", "summaries.db-shm") in created


def _change(harness, how: str) -> None:
    transcript = harness.transcript
    if how == "create a file":
        (harness.root / "sessions" / "new.jsonl").write_text("{}\n")
    elif how == "create an empty directory":
        (harness.root / "sessions" / "2026" / "02").mkdir()
    elif how == "remove a file":
        (harness.root / "presence" / "stale.lock").unlink()
    elif how == "append to a file":
        with open(transcript, "a") as target:
            target.write("{}\n")
    elif how == "rewrite a file at the same size":
        found = transcript.stat()
        transcript.write_bytes(b"x" * found.st_size)
        os.utime(transcript, ns=(found.st_atime_ns, found.st_mtime_ns + 1))
    elif how == "touch a file":
        found = transcript.stat()
        os.utime(transcript, ns=(found.st_atime_ns, found.st_mtime_ns + 1))
    elif how == "change permissions":
        os.chmod(transcript, 0o600 if transcript.stat().st_mode & 0o777 != 0o600 else 0o640)
    elif how == "replace a file with a link":
        found = transcript.parent.stat()
        transcript.unlink()
        transcript.symlink_to(harness.secret)
        os.utime(transcript.parent, ns=(found.st_atime_ns, found.st_mtime_ns))
    elif how == "retarget a link":
        link = harness.root / "link-to-transcript"
        found = os.lstat(link)
        parent = harness.root.stat()
        link.unlink()
        link.symlink_to(harness.wal_database)
        os.utime(link, ns=(found.st_atime_ns, found.st_mtime_ns), follow_symlinks=False)
        os.utime(harness.root, ns=(parent.st_atime_ns, parent.st_mtime_ns))
    else:
        raise AssertionError(how)


@pytest.mark.parametrize(
    ("how", "path", "change"),
    [
        ("create a file", "sessions/new.jsonl", "created"),
        ("create an empty directory", "sessions/2026/02", "created"),
        ("remove a file", "presence/stale.lock", "removed"),
        ("append to a file", "sessions/2026/01/02/session-a.jsonl", "changed"),
        ("rewrite a file at the same size", "sessions/2026/01/02/session-a.jsonl", "changed"),
        ("touch a file", "sessions/2026/01/02/session-a.jsonl", "changed"),
        ("change permissions", "sessions/2026/01/02/session-a.jsonl", "changed"),
        ("replace a file with a link", "sessions/2026/01/02/session-a.jsonl", "changed"),
        ("retarget a link", "link-to-transcript", "changed"),
    ],
)
def test_every_kind_of_change_is_caught(harness, how, path, change):
    with pytest.raises(HarnessDataChanged) as raised, assert_untouched([harness.root]):
        _change(harness, how)
    found = {(difference.path, difference.change) for difference in raised.value.differences}
    assert (os.path.normpath(path), change) in found


def test_creating_a_missing_root_is_caught(tmp_path):
    root = tmp_path / "not-installed"
    with pytest.raises(HarnessDataChanged), assert_untouched([root]):
        root.mkdir()


def test_every_root_is_checked(harness, tmp_path):
    second = tmp_path / "second-harness"
    second.mkdir()
    with pytest.raises(HarnessDataChanged) as raised, assert_untouched([harness.root, second]):
        (second / "created.txt").write_text("x")
    assert [str(d) for d in raised.value.differences if d.path == "created.txt"] == [
        f"created: {second / 'created.txt'}"
    ]


def test_the_report_names_paths_and_never_content(harness):
    marker = "PRIVATE-TRANSCRIPT-MARKER"
    with pytest.raises(HarnessDataChanged) as raised, assert_untouched([harness.root]):
        with open(harness.transcript, "a") as target:
            target.write(marker)
    report = str(raised.value)
    assert "session-a.jsonl (size" in report
    assert marker not in report


def test_links_are_recorded_not_followed(harness):
    entries = snapshot_tree(harness.root)
    assert entries["link-to-outside-directory"].kind == "link"
    assert not any(path.startswith("link-to-outside-directory" + os.sep) for path in entries)
    assert entries["."].kind == "directory"
    assert entries[os.path.join("state", "summaries.db-wal")].kind == "file"
