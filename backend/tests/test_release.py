"""The release tool: one version everywhere, checksums, and the updater's manifest."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "packaging" / "release.py"
spec = importlib.util.spec_from_file_location("release", TOOL)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

VERSIONED = (
    "backend/agdash/__init__.py",
    "backend/pyproject.toml",
    "desktop/Cargo.toml",
    "desktop/tauri.conf.json",
    "frontend/package.json",
    "frontend/package-lock.json",
)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of the files that carry the version."""
    for name in VERSIONED:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(release.ROOT / name, target)
    return tmp_path


def _set(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))


def test_this_checkout_names_one_version_everywhere():
    found = release.versions()
    assert set(found) == set(VERSIONED)
    assert len(set(found.values())) == 1


def test_a_tag_must_match_every_version(tree: Path):
    current = release.versions(tree)["desktop/Cargo.toml"]
    assert release.check(f"v{current}", root=tree) == []
    assert release.check(current, root=tree)  # no v
    assert release.check("v1.0", root=tree)
    problems = release.check("v99.0.0", root=tree)
    assert len(problems) == len(VERSIONED)
    _set(tree / "desktop/Cargo.toml", f'version = "{current}"', 'version = "99.0.0"')
    assert release.check(f"v{current}", root=tree) == [
        f"desktop/Cargo.toml says 99.0.0, the tag says {current}"
    ]


def test_publishing_needs_the_updater_key(tree: Path):
    current = release.versions(tree)["desktop/Cargo.toml"]
    conf = tree / "desktop/tauri.conf.json"
    settings = json.loads(conf.read_text())
    settings["plugins"]["updater"]["pubkey"] = ""
    conf.write_text(json.dumps(settings))
    assert release.check(f"v{current}", root=tree) == []
    (problem,) = release.check(f"v{current}", publish=True, root=tree)
    assert "plugins.updater.pubkey" in problem
    settings["plugins"]["updater"]["pubkey"] = "a public key"
    conf.write_text(json.dumps(settings))
    assert release.check(f"v{current}", publish=True, root=tree) == []


def test_checksums_cover_every_file_in_sha256sum_form(tmp_path: Path):
    (tmp_path / "b.deb").write_bytes(b"deb")
    (tmp_path / "a.dmg").write_bytes(b"dmg")
    (tmp_path / "SHA256SUMS").write_text("stale")
    (tmp_path / "folder").mkdir()
    text = release.checksums(tmp_path).read_text()
    assert text == (
        f"{hashlib.sha256(b'dmg').hexdigest()}  a.dmg\n"
        f"{hashlib.sha256(b'deb').hexdigest()}  b.deb\n"
    )
    with pytest.raises(release.ReleaseError):
        release.checksums(tmp_path / "folder")


def _signature(name: str, version: str | None) -> str:
    comment = f"timestamp:1795103254\tfile:{name}" + (f"\tversion:{version}" if version else "")
    text = (
        "untrusted comment: signature from tauri secret key\nUlVTSUdOQVRVUkU=\n"
        f"trusted comment: {comment}\nR0xPQkFM\n"
    )
    return base64.b64encode(text.encode()).decode()


UPDATE_FILES = (
    "agdash-0.2.0-arm64.app.tar.gz",
    "agdash-0.2.0-x86_64.app.tar.gz",
    "agdash_0.2.0_amd64.AppImage",
    "agdash_0.2.0_aarch64.AppImage",
)


def _release_folder(folder: Path, *, signed_as: str | None = "0.2.0") -> Path:
    for name in UPDATE_FILES:
        (folder / name).write_bytes(b"x")
        (folder / f"{name}.sig").write_text(_signature(name, signed_as))
    for name in ("agdash-0.2.0-arm64.dmg", "agdash_0.2.0_amd64.deb", "SHA256SUMS"):
        (folder / name).write_bytes(b"x")
    return folder


def test_the_manifest_names_each_platforms_archive_and_signature(tmp_path: Path):
    folder = _release_folder(tmp_path)
    when = datetime(2026, 11, 19, 12, 0, 0, 5, tzinfo=UTC)
    document = json.loads(release.manifest(folder, "0.2.0", "owner/agdash", now=when).read_text())
    assert document["version"] == "0.2.0"
    assert document["pub_date"] == "2026-11-19T12:00:00Z"
    assert set(document["platforms"]) == {
        "darwin-aarch64",
        "darwin-aarch64-app",
        "darwin-x86_64",
        "darwin-x86_64-app",
        "linux-x86_64-appimage",
        "linux-aarch64-appimage",
    }
    linux = document["platforms"]["linux-aarch64-appimage"]
    assert linux["url"] == (
        "https://github.com/owner/agdash/releases/download/v0.2.0/agdash_0.2.0_aarch64.AppImage"
    )
    assert linux["signature"] == _signature("agdash_0.2.0_aarch64.AppImage", "0.2.0")
    # A .deb is updated by installing the new package, so it has no entry.
    assert not any("deb" in key for key in document["platforms"])


def test_signatures_must_be_bound_to_the_released_version(tmp_path: Path):
    assert release.signed_version(_signature("f", "0.2.0")) == "0.2.0"
    assert release.signed_version(_signature("f", None)) is None
    assert release.signed_version("not base64!") is None
    for signed_as in (None, "0.1.0"):
        folder = tmp_path / str(signed_as)
        folder.mkdir()
        with pytest.raises(release.ReleaseError, match="not signed for version 0.2.0"):
            release.manifest(_release_folder(folder, signed_as=signed_as), "0.2.0", "o/a")
        assert not (folder / "latest.json").exists()


def test_a_missing_platform_or_signature_writes_no_manifest(tmp_path: Path):
    folder = _release_folder(tmp_path)
    (folder / "agdash_0.2.0_amd64.AppImage.sig").unlink()
    with pytest.raises(release.ReleaseError, match="has no signature"):
        release.manifest(folder, "0.2.0", "o/a")
    (folder / "agdash_0.2.0_amd64.AppImage").unlink()
    with pytest.raises(release.ReleaseError, match="no update archive for linux-x86_64"):
        release.manifest(folder, "0.2.0", "o/a")
    assert not (folder / "latest.json").exists()


def test_an_archive_of_another_version_is_refused(tmp_path: Path):
    folder = _release_folder(tmp_path)
    (folder / "agdash-0.1.0-arm64.app.tar.gz").write_bytes(b"x")
    with pytest.raises(release.ReleaseError, match="is not version 0.2.0"):
        release.manifest(folder, "0.2.0", "o/a")


def test_the_command_reports_problems_and_exits_non_zero(tmp_path: Path, capsys):
    current = release.versions()["desktop/Cargo.toml"]
    assert release.main(["check", f"v{current}"]) == 0
    assert release.main(["check", "v99.0.0"]) == 1
    assert "the tag says 99.0.0" in capsys.readouterr().err
    assert release.main(["manifest", str(tmp_path), "0.2.0", "o/a"]) == 1
    assert "no update archive" in capsys.readouterr().err
