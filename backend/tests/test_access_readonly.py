"""Reads stay inside the allowlisted roots, and nothing but a read is possible."""

import io
import os
import socket

import pytest

from agdash.access import (
    AccessError,
    NotARegularFile,
    OutsideAllowedRoots,
    ReadOnlyAccess,
    WriteRefused,
)
from agdash.access.guard import assert_untouched

WRITE_MODES = ["w", "wb", "wt", "a", "ab", "x", "xb", "r+", "rb+", "r+b", "w+", "a+", "rw", "U", ""]


def test_a_file_inside_a_root_is_read(harness):
    access = harness.access()
    with access.open(harness.transcript) as source:
        assert source.read() == harness.transcript.read_bytes()
    with access.open(harness.transcript, "r") as source:
        assert source.readline() == '{"type": "user", "text": "hello"}\n'


def test_text_is_decoded_as_utf8_unless_told_otherwise(harness):
    access = harness.access()
    with access.open(harness.transcript, "r") as source:
        assert source.encoding.lower().replace("-", "") == "utf8"
    with access.open(harness.transcript, "rt", encoding="latin-1", errors="replace") as source:
        assert source.encoding == "latin-1"


def test_a_path_outside_the_roots_is_refused(harness):
    access = harness.access()
    for path in [
        harness.secret,
        harness.root / ".." / "outside" / "secret.txt",
        harness.root.parent,
        "/etc/hosts",
    ]:
        with pytest.raises(OutsideAllowedRoots):
            access.open(path)
        with pytest.raises(OutsideAllowedRoots):
            access.stat(path)
        with pytest.raises(OutsideAllowedRoots):
            access.exists(path)
        with pytest.raises(OutsideAllowedRoots):
            access.list_dir(path)
        with pytest.raises(OutsideAllowedRoots):
            list(access.walk(path))
        with pytest.raises(OutsideAllowedRoots), access.sqlite_snapshot(path):
            pass


def test_a_directory_whose_name_only_starts_like_a_root_is_outside(harness):
    lookalike = harness.root.with_name(harness.root.name + "-other")
    lookalike.mkdir()
    (lookalike / "notes.txt").write_text("x")
    with pytest.raises(OutsideAllowedRoots):
        harness.access().open(lookalike / "notes.txt")


def test_a_link_is_judged_by_where_it_points(harness):
    access = harness.access()
    with access.open(harness.root / "link-to-transcript") as source:
        assert source.read() == harness.transcript.read_bytes()
    with pytest.raises(OutsideAllowedRoots):
        access.open(harness.root / "link-to-secret")
    with pytest.raises(OutsideAllowedRoots):
        access.open(harness.root / "link-to-outside-directory" / "secret.txt")
    with pytest.raises(OutsideAllowedRoots):
        access.list_dir(harness.root / "link-to-outside-directory")


def test_a_root_may_itself_be_a_link(harness, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(harness.root, target_is_directory=True)
    access = ReadOnlyAccess([alias], snapshot_dir=harness.outside)
    relative = harness.transcript.relative_to(harness.root)
    with access.open(alias / relative) as source:
        assert source.read() == harness.transcript.read_bytes()
    with pytest.raises(OutsideAllowedRoots):
        access.open(harness.secret)


def test_paths_and_roots_must_be_absolute(harness, monkeypatch):
    monkeypatch.chdir(harness.root)
    with pytest.raises(ValueError, match="absolute"):
        harness.access().open("sessions")
    with pytest.raises(ValueError, match="absolute"):
        ReadOnlyAccess(["relative/root"])
    with pytest.raises(ValueError, match="at least one root"):
        ReadOnlyAccess([])


@pytest.mark.parametrize("mode", WRITE_MODES)
def test_opening_for_anything_but_reading_is_refused(harness, mode):
    access = harness.access()
    absent = harness.root / "sessions" / "never-created.jsonl"
    with assert_untouched([harness.root]):
        with pytest.raises(WriteRefused):
            access.open(harness.transcript, mode)
        with pytest.raises(WriteRefused):
            access.open(absent, mode)
    assert not absent.exists()


def test_a_handle_that_was_opened_cannot_write(harness):
    access = harness.access()
    with access.open(harness.transcript) as source, pytest.raises(io.UnsupportedOperation):
        source.write(b"x")
    with access.open(harness.transcript, "r") as source, pytest.raises(io.UnsupportedOperation):
        source.write("x")
    with access.open(harness.transcript) as source, pytest.raises(io.UnsupportedOperation):
        source.truncate(0)


def test_the_class_offers_reads_only():
    public = {name for name in vars(ReadOnlyAccess) if not name.startswith("_")}
    assert public == {
        "roots",
        "resolve",
        "exists",
        "stat",
        "list_dir",
        "walk",
        "open",
        "sqlite_snapshot",
    }


def test_only_regular_files_are_opened(harness):
    access = harness.access()
    with pytest.raises(NotARegularFile):
        access.open(harness.root / "sessions")

    pipe = harness.root / "control.pipe"
    os.mkfifo(pipe)
    # Would block forever if the pipe were actually opened for reading.
    with pytest.raises(NotARegularFile):
        access.open(pipe)

    listener = socket.socket(socket.AF_UNIX)
    try:
        # A socket path has a short length limit, so bind by a relative name.
        here = os.getcwd()
        os.chdir(harness.root)
        try:
            listener.bind("control.sock")
        finally:
            os.chdir(here)
        with pytest.raises(NotARegularFile):
            access.open(harness.root / "control.sock")
    finally:
        listener.close()


def test_a_missing_file_is_reported_as_missing(harness):
    access = harness.access()
    absent = harness.root / "sessions" / "absent.jsonl"
    assert access.exists(absent) is False
    assert access.exists(harness.transcript) is True
    with pytest.raises(FileNotFoundError):
        access.open(absent)
    with pytest.raises(FileNotFoundError):
        access.stat(absent)


def test_a_root_that_does_not_exist_yet_is_allowed(tmp_path):
    root = tmp_path / "not-installed"
    access = ReadOnlyAccess([root], snapshot_dir=tmp_path)
    assert access.exists(root / "sessions") is False
    with pytest.raises(FileNotFoundError):
        list(access.walk(root))
    assert not root.exists()


def test_stat_and_list_dir(harness):
    access = harness.access()
    assert access.stat(harness.transcript).st_size == harness.transcript.stat().st_size
    assert [path.name for path in access.list_dir(harness.root / "state")] == [
        "goals.sqlite",
        "goals.sqlite-journal",
        "summaries.db",
        "summaries.db-wal",
    ]


def test_walk_yields_files_inside_the_roots_only(harness):
    os.mkfifo(harness.root / "control.pipe")
    access = harness.access()
    found = list(access.walk(harness.root))

    assert found == list(access.walk(harness.root))
    names = [path.name for path in found]
    assert "session-a.jsonl" in names
    assert "agent-1.jsonl" in names
    assert "stale.lock" in names
    assert "secret.txt" not in names
    assert "control.pipe" not in names
    assert all(harness.root.resolve() in path.parents for path in found)
    # The link to the transcript resolves to the transcript itself.
    assert names.count("session-a.jsonl") == 2


def test_walk_keeps_a_stable_order_and_enters_no_linked_folder(tmp_path):
    root = tmp_path / "root"
    for folder in ("b/deep", "a", "c"):
        (root / folder).mkdir(parents=True)
    for name in ("z.txt", "b/2.txt", "b/1.txt", "b/deep/x.txt", "a/y.txt"):
        (root / name).write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x")
    (root / "c" / "linked-folder").symlink_to(root / "b")
    (root / "c" / "leaves").symlink_to(outside)
    (root / "c" / "leaving.txt").symlink_to(outside / "secret.txt")
    (root / "c" / "inside.txt").symlink_to(root / "z.txt")
    (root / "c" / "dangling.txt").symlink_to(root / "nothing")
    access = ReadOnlyAccess([root], snapshot_dir=tmp_path / "snapshots")
    real = root.resolve()
    found = [str(path.relative_to(real)) for path in access.walk(root)]
    # A folder's files first, then its folders in name order; the link inside the root
    # is the file it points at; nothing is reached through a linked folder.
    assert found == ["z.txt", "a/y.txt", "b/1.txt", "b/2.txt", "b/deep/x.txt", "z.txt"]
    assert [str(p.relative_to(real)) for p in access.walk(root / "b")] == [
        "b/1.txt",
        "b/2.txt",
        "b/deep/x.txt",
    ]


def test_snapshots_may_not_be_kept_inside_a_harness_root(harness, monkeypatch):
    with pytest.raises(AccessError, match="inside a harness root"):
        ReadOnlyAccess([harness.root], snapshot_dir=harness.root / "state")
    monkeypatch.setattr("tempfile.tempdir", str(harness.root))
    with pytest.raises(AccessError, match="inside a harness root"):
        ReadOnlyAccess([harness.root])
