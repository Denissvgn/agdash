"""Small pure helpers that turn what harnesses write into agdash's own terms.

Nothing here reads a file, a clock or the environment, and nothing outside
the standard library is imported.
"""

import math
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from enum import StrEnum
from typing import Any

from agdash.vocab import Harness, UsageScope

# -- working-directory slugs ------------------------------------------------------
#
# Two harnesses file a session under a directory named after the working
# directory it ran in. Both encodings lose information: a "-" in the slug may
# have been a path separator or some other character. A slug therefore cannot be
# decoded with certainty; it can only be checked against a candidate directory.

_CLAUDE_REPLACED = re.compile(r"[^A-Za-z0-9]")
_PI_REPLACED = re.compile(r"[/\\:]")
_PI_WRAP = "--"


def cwd_slug(harness: str, cwd: str) -> str:
    """The directory name a harness files sessions under for ``cwd``.

    Claude Code replaces every character that is not a letter or digit with
    ``-``. Pi drops the leading separator, replaces ``/``, ``\\`` and ``:``
    with ``-`` and wraps the result in ``--``.
    """
    if harness == Harness.CLAUDE_CODE:
        return _CLAUDE_REPLACED.sub("-", cwd)
    if harness == Harness.PI:
        relative = cwd[1:] if cwd[:1] in ("/", "\\") else cwd
        return _PI_WRAP + _PI_REPLACED.sub("-", relative) + _PI_WRAP
    raise ValueError(f"{harness} does not name session directories after the working directory")


def cwd_matches_slug(harness: str, cwd: str, slug: str) -> bool:
    """Whether sessions run in ``cwd`` would be filed under ``slug``."""
    return cwd_slug(harness, cwd) == slug


def pick_cwd_for_slug(harness: str, slug: str, known_cwds: Iterable[str]) -> str | None:
    """The one known directory that ``slug`` stands for.

    ``None`` if none of them matches, and also if several different ones do:
    then the slug alone cannot say which.
    """
    matches = {cwd for cwd in known_cwds if cwd_matches_slug(harness, cwd, slug)}
    return matches.pop() if len(matches) == 1 else None


def guess_cwd_from_slug(harness: str, slug: str) -> str | None:
    """A best guess at the directory behind ``slug``, reading every ``-`` as ``/``.

    Wrong whenever the real path holds a ``-`` or another replaced character,
    so this is for display when nothing better is known, never for matching.
    ``None`` if the slug does not have the harness's form.
    """
    if harness == Harness.CLAUDE_CODE:
        if not slug.startswith("-") or len(slug) < 2:
            return None
        return slug.replace("-", "/")
    if harness == Harness.PI:
        inner = slug[len(_PI_WRAP) : -len(_PI_WRAP)]
        if not (slug.startswith(_PI_WRAP) and slug.endswith(_PI_WRAP)) or not inner:
            return None
        return "/" + inner.replace("-", "/")
    raise ValueError(f"{harness} does not name session directories after the working directory")


# -- timestamps -------------------------------------------------------------------

# Outside these years a value is a counter or a mistake, not a session timestamp.
EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)
LATEST = datetime(2100, 1, 1, tzinfo=UTC)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_EARLIEST_SECONDS = int((EARLIEST - _EPOCH).total_seconds())
_LATEST_SECONDS = int((LATEST - _EPOCH).total_seconds())
# Seconds, milliseconds, microseconds, nanoseconds. Between the two bounds the
# four ranges do not overlap, so the size of a number tells its unit.
_EPOCH_UNITS = (1, 1_000, 1_000_000, 1_000_000_000)

_ISO = re.compile(
    r"""
    (?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})
    [T\ ]
    (?P<hour>\d{2}):(?P<minute>\d{2})
    (?: :(?P<second>\d{2}) (?:[.,](?P<fraction>\d{1,9}))? )?
    \s*
    (?P<zone> Z | [+-]\d{2}(?::?\d{2})? )?
    """,
    re.VERBOSE | re.IGNORECASE,
)
# The same instant as it appears in a file name, where ":" and "." are not allowed.
_ISO_IN_FILE_NAME = re.compile(
    r"""
    (?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})
    T
    (?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})
    (?:-(?P<fraction>\d{1,9}))?
    (?P<zone>Z)?
    """,
    re.VERBOSE | re.IGNORECASE,
)
_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")


def _in_range(instant: datetime) -> datetime | None:
    return instant if EARLIEST <= instant < LATEST else None


def _from_epoch(number: int | float) -> datetime | None:
    if isinstance(number, float) and not math.isfinite(number):
        return None
    for unit in _EPOCH_UNITS:
        if _EARLIEST_SECONDS * unit <= number < _LATEST_SECONDS * unit:
            whole, part = divmod(number, unit)
            if isinstance(number, int):
                micros = part * 1_000_000 // unit
            else:  # a float is already inexact, so take the nearest microsecond
                micros = round(part * 1_000_000 / unit)
            return _in_range(_EPOCH + timedelta(seconds=int(whole), microseconds=micros))
    return None


def _zone(text: str | None, naive_as: tzinfo | None) -> tzinfo | None:
    if not text:
        return naive_as
    if text in ("Z", "z"):
        return UTC
    digits = text[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:] or 0)
    if hours > 23 or minutes > 59:
        return None
    offset = timedelta(hours=hours, minutes=minutes)
    return timezone(-offset if text[0] == "-" else offset)


def _from_text(text: str, naive_as: tzinfo | None) -> datetime | None:
    text = text.strip()
    if _NUMBER.fullmatch(text):
        return _from_epoch(float(text) if "." in text else int(text))
    match = _ISO.fullmatch(text) or _ISO_IN_FILE_NAME.fullmatch(text)
    if match is None:
        return None
    zone = _zone(match["zone"], naive_as)
    if zone is None:
        return None
    second = int(match["second"] or 0)
    leap = second == 60
    micros = int((match["fraction"] or "").ljust(6, "0")[:6] or 0)
    try:
        instant = datetime(
            int(match["year"]),
            int(match["month"]),
            int(match["day"]),
            int(match["hour"]),
            int(match["minute"]),
            59 if leap else second,
            micros,
            tzinfo=zone,
        )
        return _in_range((instant + timedelta(seconds=1 if leap else 0)).astimezone(UTC))
    except (ValueError, OverflowError):
        return None


def parse_timestamp(value: Any, *, naive_as: tzinfo | None = None) -> datetime | None:
    """Turn a harness timestamp into a UTC instant, or ``None`` if it is not one.

    Accepts ISO 8601 text with ``Z`` or an offset, the same text as it appears
    in a file name (``2026-01-02T03-04-05-678Z``), and epoch numbers or numeric
    text in seconds, milliseconds, microseconds or nanoseconds. Fractions finer
    than a microsecond are cut, not rounded.

    Text with no offset is refused unless ``naive_as`` names the zone it was
    written in. Values outside the years 2000 to 2099 are refused.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            if naive_as is None:
                return None
            value = value.replace(tzinfo=naive_as)
        return _in_range(value.astimezone(UTC))
    if isinstance(value, int | float):
        return _from_epoch(value)
    if isinstance(value, str):
        return _from_text(value, naive_as)
    return None


# -- token usage ------------------------------------------------------------------


class UsageFormat(StrEnum):
    """Whose usage record this is. They count the same things differently."""

    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    PI = "pi"


def _count(raw: Mapping[str, Any], *path: str) -> int | None:
    """A token count at ``path``, or ``None`` if it is missing or not a count."""
    node: Any = raw
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    if isinstance(node, bool):
        return None
    if isinstance(node, float) and node.is_integer():
        node = int(node)
    return node if isinstance(node, int) and node >= 0 else None


def _first(*counts: int | None) -> int | None:
    return next((count for count in counts if count is not None), None)


def _less(total: int | None, part: int | None) -> int | None:
    """``total`` without ``part``, for formats whose total includes the part."""
    if total is None:
        return None
    return max(total - (part or 0), 0)


def _plus(first: int | None, second: int | None) -> int | None:
    if first is None and second is None:
        return None
    return (first or 0) + (second or 0)


def _cost(raw: Mapping[str, Any]) -> float | None:
    cost = raw.get("cost")
    total = cost.get("total") if isinstance(cost, Mapping) else None
    if isinstance(total, bool) or not isinstance(total, int | float):
        return None
    return float(total) if math.isfinite(total) and total >= 0 else None


def normalise_usage(
    usage_format: str,
    raw: Any,
    *,
    scope: UsageScope = UsageScope.MESSAGE,
) -> dict[str, Any] | None:
    """Turn a harness usage record into the payload of a ``usage`` event.

    The result always means the same thing, whatever the source counted:

    * ``input_tokens`` — input that was neither read from nor written to a cache;
    * ``cache_read_tokens`` and ``cache_write_tokens`` — input that was;
    * ``output_tokens`` — everything generated, reasoning included;
    * ``reasoning_tokens`` — the part of ``output_tokens`` spent on reasoning.

    A number the source did not report is left out rather than set to zero.
    ``None`` if the record holds no usable number at all.
    """
    if not isinstance(raw, Mapping):
        return None
    fmt = UsageFormat(usage_format)
    cost = None

    if fmt is UsageFormat.ANTHROPIC:
        # Input already excludes both cache counts; output includes thinking.
        fresh = _count(raw, "input_tokens")
        cache_read = _count(raw, "cache_read_input_tokens")
        cache_write = _first(
            _count(raw, "cache_creation_input_tokens"),
            _plus(
                _count(raw, "cache_creation", "ephemeral_5m_input_tokens"),
                _count(raw, "cache_creation", "ephemeral_1h_input_tokens"),
            ),
        )
        output = _count(raw, "output_tokens")
        reasoning = None
    elif fmt is UsageFormat.OPENAI:
        # Input includes cached input; output includes reasoning.
        cache_read = _first(
            _count(raw, "cached_input_tokens"),
            _count(raw, "input_tokens_details", "cached_tokens"),
        )
        fresh = _less(_count(raw, "input_tokens"), cache_read)
        cache_write = None
        output = _count(raw, "output_tokens")
        reasoning = _first(
            _count(raw, "reasoning_output_tokens"),
            _count(raw, "output_tokens_details", "reasoning_tokens"),
        )
    elif fmt is UsageFormat.GEMINI:
        # Prompt includes cached content; candidates do not include thoughts.
        cache_read = _count(raw, "cachedContentTokenCount")
        fresh = _less(_count(raw, "promptTokenCount"), cache_read)
        cache_write = None
        reasoning = _count(raw, "thoughtsTokenCount")
        output = _plus(_count(raw, "candidatesTokenCount"), reasoning)
    else:
        # Pi has already separated fresh input from cached input.
        fresh = _count(raw, "input")
        cache_read = _count(raw, "cacheRead")
        cache_write = _count(raw, "cacheWrite")
        output = _count(raw, "output")
        reasoning = None
        cost = _cost(raw)

    payload: dict[str, Any] = {
        name: number
        for name, number in (
            ("input_tokens", fresh),
            ("output_tokens", output),
            ("cache_read_tokens", cache_read),
            ("cache_write_tokens", cache_write),
            ("reasoning_tokens", reasoning),
        )
        if number is not None
    }
    if cost is not None:
        payload["reported_cost"] = cost
        payload["cost_currency"] = "USD"
    if not payload:
        return None
    return {"scope": UsageScope(scope).value, **payload}
