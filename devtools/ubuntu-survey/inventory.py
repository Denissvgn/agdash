"""What each harness wrote in a home folder, as structure: never a value.

    python3 inventory.py HOME > inventory.json

For every harness it lists the files it keeps, and for each kind of record the
record types, their keys, the kinds of content blocks and how much thinking they
carry. For SQLite stores it lists tables and columns. Only names, types, counts and
booleans are printed. No prompt, reply, path, identifier or account is printed.

Credential files are never opened; only their presence, size and mode are listed.
SQLite stores are copied to a private folder first, as agdash reads them. Standard
library only.
"""

from __future__ import annotations

import collections
import json
import shutil
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

# Never opened: these hold sign-in tokens or keys.
CREDENTIALS = (
    ".claude/.credentials.json",
    ".codex/auth.json",
    ".gemini/antigravity-cli/antigravity-oauth-token",
    ".gemini/oauth_creds.json",
    ".pi/agent/auth.json",
)
ROOTS = {
    "claude": (".claude", ".claude.json"),
    "codex": (".codex",),
    "antigravity": (".gemini", ".config/antigravity"),
    "pi": (".pi/agent",),
}
# Caches and downloads that hold no records.
SKIP = (
    "plugins",
    "skills",
    "packages",
    "cache",
    "marketplaces",
    "builtin",
    "tmp",
    "bin",
)
BLOCK_KINDS = (
    "thinking",
    "redacted_thinking",
    "text",
    "tool_use",
    "tool_result",
    "toolCall",
)


def credential(home: Path, path: Path) -> bool:
    return any(path == home / name for name in CREDENTIALS)


def keys_of(value: object, depth: int = 2) -> object:
    """The shape of a JSON value: key names and value types, never the values."""
    if isinstance(value, dict):
        if depth == 0:
            return "object"
        return {key: keys_of(item, depth - 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return ["list", len(value)]
    return type(value).__name__


def blocks(message: object) -> list[dict]:
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return [block for block in content if isinstance(block, dict)]
    return []


def jsonl(path: Path) -> dict:
    """Record types, the keys each carries, content block kinds and thinking coverage."""
    kinds: collections.Counter = collections.Counter()
    keys: dict[str, set] = collections.defaultdict(set)
    payloads: dict[str, set] = collections.defaultdict(set)
    content: collections.Counter = collections.Counter()
    thinking = {"with_text": 0, "empty": 0, "signed": 0}
    reasoning = {"summary": 0, "encrypted": 0, "content": 0}
    bad = 0
    for line in path.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(record, dict):
            continue
        kind = str(record.get("type", "?"))
        payload = record.get("payload")
        if isinstance(payload, dict) and "type" in payload:
            kind = f"{kind}/{payload['type']}"
            payloads[kind].update(payload)
            if payload.get("type") == "reasoning":
                reasoning["summary"] += bool(payload.get("summary"))
                reasoning["encrypted"] += bool(payload.get("encrypted_content"))
                reasoning["content"] += bool(payload.get("content"))
        kinds[kind] += 1
        keys[kind].update(record)
        if "thinking" in record:  # a step that carries its thinking at the top level
            thinking[
                "with_text" if str(record["thinking"] or "").strip() else "empty"
            ] += 1
            content["step thinking"] += 1
        message = record.get("message")
        for block in blocks(message):
            name = str(block.get("type", "?"))
            content[name] += 1
            if name == "thinking":
                text = block.get("thinking") or block.get("text") or ""
                thinking["with_text" if str(text).strip() else "empty"] += 1
                thinking["signed"] += bool(
                    block.get("signature") or block.get("thinkingSignature")
                )
    return {
        "records": dict(sorted(kinds.items())),
        "keys": {kind: sorted(names) for kind, names in sorted(keys.items())},
        "payload_keys": {
            kind: sorted(names) for kind, names in sorted(payloads.items())
        },
        "content_blocks": dict(sorted(content.items())),
        "thinking_blocks": thinking
        if content.get("thinking") or content.get("step thinking")
        else None,
        "reasoning_items": reasoning if any(reasoning.values()) else None,
        "unparseable_lines": bad,
    }


def sqlite(path: Path) -> dict:
    """Tables, their columns and row counts, from a private copy."""
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / path.name
        for suffix in ("", "-wal", "-shm"):
            source = path.with_name(path.name + suffix)
            if source.exists():
                shutil.copy2(source, copy.with_name(copy.name + suffix))
        try:
            db = sqlite3.connect(copy)
            tables = {}
            for (name,) in db.execute(
                "select name from sqlite_master where type='table' order by name"
            ):
                columns = [row[1] for row in db.execute(f'pragma table_info("{name}")')]
                (rows,) = db.execute(f'select count(*) from "{name}"').fetchone()
                tables[name] = {"columns": columns, "rows": rows}
            db.close()
            return {"tables": tables}
        except sqlite3.DatabaseError as error:
            return {"error": type(error).__name__}


def json_file(path: Path) -> dict:
    try:
        return {"shape": keys_of(json.loads(path.read_text()), depth=3)}
    except ValueError:
        return {"error": "not JSON"}


def toml_keys(path: Path) -> dict:
    import tomllib

    try:
        return {"shape": keys_of(tomllib.loads(path.read_text()), depth=2)}
    except (ValueError, tomllib.TOMLDecodeError):
        return {"error": "not TOML"}


def describe(home: Path, path: Path) -> dict:
    info = path.stat()
    entry: dict = {"bytes": info.st_size, "mode": oct(stat.S_IMODE(info.st_mode))}
    if credential(home, path):
        entry["credential"] = "not opened"
        return entry
    name = path.name
    try:
        if name.endswith(".jsonl"):
            entry.update(jsonl(path))
        elif name.endswith((".db", ".sqlite")):
            entry.update(sqlite(path))
        elif name.endswith(".json"):
            entry.update(json_file(path))
        elif name.endswith(".toml"):
            entry.update(toml_keys(path))
    except OSError as error:
        entry["error"] = type(error).__name__
    return entry


def pattern(home: Path, path: Path) -> str:
    """The file's place with every name that varies (ids, dates, slugs) replaced."""
    parts = []
    for part in path.relative_to(home).parts:
        stem = part.split(".")[0]
        if len(stem) >= 16 and any(ch.isdigit() for ch in stem):
            part = "<id>" + part[len(stem) :]
        elif stem.isdigit():
            part = "<n>" + part[len(stem) :]
        elif part.startswith(("-home-", "--home-")):
            part = "<cwd-slug>"
        parts.append(part)
    return "/".join(parts)


def merge(into: dict, entry: dict) -> None:
    """Add a second file of the same place to the first: counts summed, keys united."""
    into["count"] = into.get("count", 1) + 1
    into["bytes"] += entry["bytes"]
    for field in ("records", "content_blocks", "thinking_blocks", "reasoning_items"):
        mine, theirs = into.get(field) or {}, entry.get(field) or {}
        if mine or theirs:
            into[field] = {
                k: mine.get(k, 0) + theirs.get(k, 0) for k in sorted({*mine, *theirs})
            }
    for field in ("keys", "payload_keys"):
        mine, theirs = into.get(field) or {}, entry.get(field) or {}
        if mine or theirs:
            into[field] = {
                k: sorted({*mine.get(k, []), *theirs.get(k, [])})
                for k in sorted({*mine, *theirs})
            }
    if "unparseable_lines" in entry:
        into["unparseable_lines"] = (
            into.get("unparseable_lines", 0) + entry["unparseable_lines"]
        )


def survey(home: Path) -> dict:
    found: dict = {}
    for harness, roots in ROOTS.items():
        files: dict[str, dict] = {}
        for root in roots:
            base = home / root
            if base.is_file():
                files[pattern(home, base)] = describe(home, base)
                continue
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                relative = path.relative_to(base).parts
                if any(part in SKIP for part in relative[:-1]) or path.is_symlink():
                    continue
                if path.is_file() and not path.name.endswith(("-wal", "-shm")):
                    key = pattern(home, path)
                    entry = describe(home, path)
                    if key in files:  # several files in the same place: add them up
                        merge(files[key], entry)
                    else:
                        files[key] = entry
        found[harness] = files
    return found


if __name__ == "__main__":
    json.dump(survey(Path(sys.argv[1])), sys.stdout, indent=1, sort_keys=True)
    print()
