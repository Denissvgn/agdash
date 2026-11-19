"""Release checks and files: one version everywhere, checksums, and the update manifest.

    release.py check v0.2.0 [--publish]   every part of agdash carries this version
    release.py checksums DIR              write DIR/SHA256SUMS for every file in DIR
    release.py manifest DIR VERSION REPO  write DIR/latest.json, the updater's manifest

The release workflow runs these. ``--publish`` also requires the updater's public key
in the desktop app's settings, since a release the app cannot verify cannot update it.
Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUMS = "SHA256SUMS"
MANIFEST = "latest.json"

# The update archives each platform's app installs from, and the updater's names for
# them: `{os}-{arch}-{bundle}` is looked up first, then `{os}-{arch}`.
ARCH = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}
UPDATES = (
    (re.compile(r"^agdash-(?P<v>[^-]+)-(?P<arch>arm64|x86_64)\.app\.tar\.gz$"), "darwin", "app"),
    (re.compile(r"^agdash_(?P<v>[^_]+)_(?P<arch>amd64|aarch64)\.AppImage$"), "linux", "appimage"),
)
PLATFORMS = ("darwin-aarch64", "darwin-x86_64", "linux-x86_64", "linux-aarch64")


class ReleaseError(Exception):
    pass


# -- one version ------------------------------------------------------------------


def versions(root: Path = ROOT) -> dict[str, str]:
    """The version each part of agdash names, by the file that names it."""
    found: dict[str, str] = {}
    init = (root / "backend/agdash/__init__.py").read_text()
    match = re.search(r'^__version__ = "([^"]+)"', init, re.M)
    found["backend/agdash/__init__.py"] = match.group(1) if match else ""
    pyproject = tomllib.loads((root / "backend/pyproject.toml").read_text())
    found["backend/pyproject.toml"] = pyproject["project"]["version"]
    cargo = tomllib.loads((root / "desktop/Cargo.toml").read_text())
    found["desktop/Cargo.toml"] = cargo["package"]["version"]
    found["desktop/tauri.conf.json"] = _json(root / "desktop/tauri.conf.json")["version"]
    found["frontend/package.json"] = _json(root / "frontend/package.json")["version"]
    lock = _json(root / "frontend/package-lock.json")
    found["frontend/package-lock.json"] = lock["version"]
    return found


def check(tag: str, *, publish: bool = False, root: Path = ROOT) -> list[str]:
    """What stops this tag from being released; empty when nothing does."""
    problems = []
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        problems.append(f"{tag}: a release tag is v and three numbers, such as v0.2.0")
    wanted = tag.removeprefix("v")
    for path, version in versions(root).items():
        if version != wanted:
            problems.append(f"{path} says {version or 'nothing'}, the tag says {wanted}")
    if publish:
        updater = _json(root / "desktop/tauri.conf.json").get("plugins", {}).get("updater", {})
        if not str(updater.get("pubkey", "")).strip():
            problems.append(
                "desktop/tauri.conf.json has no plugins.updater.pubkey: the app could not "
                "verify this release's updates (see docs/releasing.md)"
            )
    return problems


# -- checksums ----------------------------------------------------------------------


def checksums(directory: Path) -> Path:
    """Write SHA256SUMS for every file in the folder, in `sha256sum -c` form."""
    lines = []
    for path in sorted(p for p in directory.iterdir() if p.is_file() and p.name != SUMS):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        lines.append(f"{digest.hexdigest()}  {path.name}\n")
    if not lines:
        raise ReleaseError(f"{directory} holds no files")
    target = directory / SUMS
    target.write_text("".join(lines))
    return target


# -- the update manifest ------------------------------------------------------------


def signed_version(signature: str) -> str | None:
    """The version a Tauri signature was bound to, from its trusted comment."""
    try:
        text = base64.b64decode(signature, validate=True).decode()
    except ValueError:
        return None
    for line in text.splitlines():
        if line.startswith("trusted comment:"):
            for field in line.split("\t"):
                if field.startswith("version:"):
                    return field.removeprefix("version:")
    return None


def manifest(directory: Path, version: str, repo: str, *, now: datetime | None = None) -> Path:
    """Write latest.json: for each platform, the update archive's address and signature.

    Every platform must be there, signed for this version, or nothing is written.
    """
    base = f"https://github.com/{repo}/releases/download/v{version}"
    platforms: dict[str, dict[str, str]] = {}
    for path in sorted(directory.iterdir()):
        for pattern, system, bundle in UPDATES:
            match = pattern.match(path.name)
            if not match:
                continue
            if match["v"] != version:
                raise ReleaseError(f"{path.name} is not version {version}")
            sig = path.with_name(path.name + ".sig")
            if not sig.is_file():
                raise ReleaseError(f"{path.name} has no signature ({sig.name})")
            signature = sig.read_text().strip()
            if signed_version(signature) != version:
                raise ReleaseError(f"{sig.name} is not signed for version {version}")
            platform = f"{system}-{ARCH[match['arch']]}"
            entry = {"signature": signature, "url": f"{base}/{path.name}"}
            if platform in platforms or f"{platform}-{bundle}" in platforms:
                raise ReleaseError(f"two update archives for {platform}")
            platforms[f"{platform}-{bundle}"] = entry
            if system == "darwin":
                # A macOS app is always an .app, so the plain name serves it too.
                platforms[platform] = entry
    missing = [p for p in PLATFORMS if not any(k.startswith(p) for k in platforms)]
    if missing:
        raise ReleaseError("no update archive for " + ", ".join(missing))
    when = (now or datetime.now(UTC)).replace(microsecond=0)
    document = {
        "version": version,
        "notes": f"agdash {version}: https://github.com/{repo}/releases/tag/v{version}",
        "pub_date": when.isoformat().replace("+00:00", "Z"),
        "platforms": dict(sorted(platforms.items())),
    }
    target = directory / MANIFEST
    target.write_text(json.dumps(document, indent=2) + "\n")
    return target


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser("check", help="every part of agdash carries the tag's version")
    checking.add_argument("tag")
    checking.add_argument("--publish", action="store_true", help="also require the updater key")
    summing = commands.add_parser("checksums", help="write SHA256SUMS")
    summing.add_argument("directory", type=Path)
    manifesting = commands.add_parser("manifest", help="write latest.json")
    manifesting.add_argument("directory", type=Path)
    manifesting.add_argument("version")
    manifesting.add_argument("repo", help="owner/name on GitHub")
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            problems = check(args.tag, publish=args.publish)
            for problem in problems:
                print(problem, file=sys.stderr)
            if not problems:
                print(f"Every part of agdash is {args.tag.removeprefix('v')}.")
            return 1 if problems else 0
        if args.command == "checksums":
            print(checksums(args.directory).read_text(), end="")
            return 0
        print(manifest(args.directory, args.version, args.repo).read_text(), end="")
        return 0
    except ReleaseError as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
