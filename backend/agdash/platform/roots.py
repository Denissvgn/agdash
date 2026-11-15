"""Where agdash keeps its own data, and where each harness keeps its files.

Pure functions of a home directory and an environment, so they can be tested
for either operating system on either.

The harness locations are what was observed on macOS. They are assumed to be
the same on Linux until a survey there confirms them.
"""

from collections.abc import Mapping
from pathlib import Path

from agdash.vocab import Harness

APP_NAME = "agdash"
DATA_DIR_VARIABLE = "AGDASH_DATA_DIR"


def _absolute(value: str | None) -> Path | None:
    """An absolute path from an environment value; anything else is ignored."""
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def macos_data_dir(home: Path, env: Mapping[str, str]) -> Path:
    override = _absolute(env.get(DATA_DIR_VARIABLE))
    return override or home / "Library" / "Application Support" / APP_NAME


def linux_data_dir(home: Path, env: Mapping[str, str]) -> Path:
    """``$XDG_DATA_HOME/agdash``. A relative ``XDG_DATA_HOME`` is ignored, as the spec says."""
    override = _absolute(env.get(DATA_DIR_VARIABLE))
    if override:
        return override
    base = _absolute(env.get("XDG_DATA_HOME")) or home / ".local" / "share"
    return base / APP_NAME


def linux_config_dir(home: Path, env: Mapping[str, str]) -> Path:
    """``$XDG_CONFIG_HOME``, the parent of systemd user units."""
    return _absolute(env.get("XDG_CONFIG_HOME")) or home / ".config"


def harness_roots(home: Path, env: Mapping[str, str]) -> dict[Harness, tuple[Path, ...]]:
    """Every directory or file each harness writes that agdash may read.

    Overrides are read from agdash's own environment. An agent started with a
    different environment can keep its files elsewhere; such a session is not
    found here.
    """
    claude = _absolute(env.get("CLAUDE_CONFIG_DIR"))
    gemini = home / ".gemini"
    return {
        Harness.CLAUDE_CODE: (claude, claude / ".claude.json")
        if claude
        else (home / ".claude", home / ".claude.json"),
        Harness.CODEX: (_absolute(env.get("CODEX_HOME")) or home / ".codex",),
        Harness.ANTIGRAVITY: (
            gemini / "antigravity",
            gemini / "antigravity-cli",
            gemini / "google_accounts.json",
        ),
        Harness.PI: (home / ".pi" / "agent",),
    }


def all_harness_roots(home: Path, env: Mapping[str, str]) -> tuple[Path, ...]:
    return tuple(root for roots in harness_roots(home, env).values() for root in roots)


def is_inside(path: Path, roots: tuple[Path, ...]) -> bool:
    """Whether ``path`` is one of ``roots`` or lies below one, links resolved."""
    real = path.resolve()
    for root in roots:
        real_root = root.resolve()
        if real == real_root or real_root in real.parents:
            return True
    return False
