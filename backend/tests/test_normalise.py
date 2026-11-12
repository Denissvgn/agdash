"""Oracle tests for the pure helpers.

Each helper is checked against something that does not share its code: the
standard library's own date arithmetic, the event contract, hand-worked
vectors, and properties that must hold for any input.
"""

import ast
import random
import string
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

import agdash.normalise as normalise_module
import agdash.vocab as vocab_module
from agdash.events import validate_event
from agdash.normalise import (
    EARLIEST,
    LATEST,
    UsageFormat,
    cwd_matches_slug,
    cwd_slug,
    guess_cwd_from_slug,
    normalise_usage,
    parse_timestamp,
    pick_cwd_for_slug,
)
from agdash.vocab import EventKind, Harness, UsageScope

CLAUDE = Harness.CLAUDE_CODE
PI = Harness.PI

# -- no I/O, no third-party code --------------------------------------------------


def _imports(module) -> set[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module)
    return found


def test_the_helpers_import_only_the_standard_library():
    imports = _imports(normalise_module)
    ours = {name for name in imports if name.split(".")[0] == "agdash"}
    assert ours == {"agdash.vocab"}
    assert {name.split(".")[0] for name in imports - ours} <= sys.stdlib_module_names
    assert {name.split(".")[0] for name in _imports(vocab_module)} <= sys.stdlib_module_names


def test_the_helpers_do_no_io():
    tree = ast.parse(Path(normalise_module.__file__).read_text(encoding="utf-8"))
    imported = {name.split(".")[0] for name in _imports(normalise_module)}
    assert not imported & {"os", "io", "pathlib", "sys", "time", "socket", "subprocess", "shutil"}
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"open", "print", "input", "exec", "eval", "__import__"}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    # Reading the clock or the environment would make a result depend on when and where.
    assert not attributes & {"now", "utcnow", "today", "environ", "getenv", "read_text"}


# -- working-directory slugs ------------------------------------------------------


@pytest.mark.parametrize(
    ("harness", "cwd", "slug"),
    [
        (CLAUDE, "/work/example", "-work-example"),
        (CLAUDE, "/work/my-app", "-work-my-app"),
        (CLAUDE, "/work/my_app", "-work-my-app"),
        (CLAUDE, "/work/my app", "-work-my-app"),
        (CLAUDE, "/home/dev/.config/tool", "-home-dev--config-tool"),
        (CLAUDE, "/work/app.v2/src", "-work-app-v2-src"),
        (CLAUDE, "/", "-"),
        (CLAUDE, "/work/проект", "-work-------"),
        (PI, "/work/example", "--work-example--"),
        (PI, "/work/my-app", "--work-my-app--"),
        (PI, "/work/my_app", "--work-my_app--"),
        (PI, "/home/dev/.config/tool", "--home-dev-.config-tool--"),
        (PI, "C:\\work\\example", "--C--work-example--"),
        (PI, "/", "----"),
    ],
)
def test_slugs_follow_each_harness_rule(harness, cwd, slug):
    assert cwd_slug(harness, cwd) == slug
    assert cwd_matches_slug(harness, cwd, slug)


def test_a_slug_does_not_identify_one_directory():
    lookalikes = ["/work/my-app", "/work/my_app", "/work/my app", "/work/my/app", "/work/my.app"]
    assert {cwd_slug(CLAUDE, cwd) for cwd in lookalikes} == {"-work-my-app"}
    assert cwd_slug(PI, "/work/my-app") == cwd_slug(PI, "/work/my/app")
    assert cwd_slug(PI, "/work/my-app") != cwd_slug(PI, "/work/my_app")


def test_a_known_directory_is_picked_only_when_it_is_the_only_match():
    known = ["/work/other", "/work/my_app", "/srv/site"]
    assert pick_cwd_for_slug(CLAUDE, "-work-my-app", known) == "/work/my_app"
    assert pick_cwd_for_slug(CLAUDE, "-work-my-app", [*known, "/work/my_app"]) == "/work/my_app"
    assert pick_cwd_for_slug(CLAUDE, "-work-my-app", [*known, "/work/my-app"]) is None
    assert pick_cwd_for_slug(CLAUDE, "-work-absent", known) is None
    assert pick_cwd_for_slug(CLAUDE, "-work-my-app", []) is None
    assert pick_cwd_for_slug(PI, "--srv-site--", known) == "/srv/site"
    assert pick_cwd_for_slug(PI, "-srv-site", known) is None


@pytest.mark.parametrize(
    ("harness", "slug", "guess"),
    [
        (CLAUDE, "-work-example", "/work/example"),
        (CLAUDE, "-work-my-app", "/work/my/app"),
        (PI, "--work-example--", "/work/example"),
        (PI, "--work-.config--", "/work/.config"),
        (CLAUDE, "work-example", None),
        (CLAUDE, "-", None),
        (CLAUDE, "", None),
        (PI, "-work-example-", None),
        (PI, "----", None),
        (PI, "--", None),
    ],
)
def test_guessing_reads_every_dash_as_a_separator(harness, slug, guess):
    assert guess_cwd_from_slug(harness, slug) == guess


@pytest.mark.parametrize("harness", [Harness.CODEX, Harness.ANTIGRAVITY, "other"])
def test_harnesses_without_slugs_are_refused(harness):
    with pytest.raises(ValueError, match="does not name session directories"):
        cwd_slug(harness, "/work/example")
    with pytest.raises(ValueError, match="does not name session directories"):
        guess_cwd_from_slug(harness, "-work-example")


_PATH_CHARACTERS = string.ascii_letters + string.digits + "-_. +@"


@pytest.mark.parametrize("seed", range(200))
def test_slug_properties_hold_for_any_path(seed):
    rng = random.Random(seed)
    parts = [
        "".join(rng.choices(_PATH_CHARACTERS, k=rng.randint(1, 10)))
        for _ in range(rng.randint(1, 6))
    ]
    cwd = "/" + "/".join(parts)

    for harness in (CLAUDE, PI):
        slug = cwd_slug(harness, cwd)
        assert "/" not in slug, "a slug must be usable as one directory name"
        assert cwd_matches_slug(harness, cwd, slug)
        assert pick_cwd_for_slug(harness, slug, ["/elsewhere", cwd]) == cwd
        # The guess is a real candidate: it is filed under the very same slug.
        assert cwd_matches_slug(harness, guess_cwd_from_slug(harness, slug), slug)

    simple = all(part.isalnum() for part in parts)
    assert (guess_cwd_from_slug(CLAUDE, cwd_slug(CLAUDE, cwd)) == cwd) == simple
    assert len(cwd_slug(CLAUDE, cwd)) == len(cwd)


# -- timestamps -------------------------------------------------------------------

INSTANT = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)
SECOND = INSTANT.replace(microsecond=0)
MILLI = INSTANT.replace(microsecond=678000)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("2026-01-02T03:04:05.678901Z", INSTANT),
        ("2026-01-02T03:04:05.678Z", MILLI),
        ("2026-01-02T03:04:05Z", SECOND),
        ("2026-01-02T03:04:05z", SECOND),
        ("2026-01-02t03:04:05Z", SECOND),
        ("2026-01-02 03:04:05Z", SECOND),
        ("2026-01-02T03:04:05 Z", SECOND),
        ("  2026-01-02T03:04:05Z\n", SECOND),
        ("2026-01-02T03:04Z", SECOND.replace(second=0)),
        ("2026-01-02T03:04:05+00:00", SECOND),
        ("2026-01-02T06:04:05+03:00", SECOND),
        ("2026-01-02T06:04:05+0300", SECOND),
        ("2026-01-02T06:04:05+03", SECOND),
        ("2026-01-01T19:34:05-07:30", SECOND),
        ("2026-01-02T08:49:05+05:45", SECOND),
        ("2026-01-02T03:04:05,678Z", MILLI),
        ("2026-01-02T03:04:05.6789019Z", INSTANT),
        ("2026-01-02T03:04:05.678901999Z", INSTANT),
        ("2026-01-02T03:04:05.6Z", SECOND.replace(microsecond=600000)),
        ("2026-01-02T03-04-05-678Z", MILLI),
        ("2026-01-02T03-04-05Z", SECOND),
        ("2026-12-31T23:59:60Z", datetime(2027, 1, 1, tzinfo=UTC)),
        (1767323045, SECOND),
        (1767323045.678901, INSTANT),
        (1767323045678, MILLI),
        (1767323045678901, INSTANT),
        (1767323045678901999, INSTANT),
        ("1767323045", SECOND),
        ("1767323045678", MILLI),
        ("1767323045.5", SECOND.replace(microsecond=500000)),
        (INSTANT, INSTANT),
        (INSTANT.astimezone(timezone(timedelta(hours=-8))), INSTANT),
    ],
)
def test_timestamp_vectors(given, expected):
    parsed = parse_timestamp(given)
    assert parsed == expected
    assert parsed.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "given",
    [
        None,
        True,
        False,
        "",
        "   ",
        "yesterday",
        "2026-01-02",
        "2026-01-02T03:04:05",
        "2026-01-02T03-04-05",
        "2026-13-02T03:04:05Z",
        "2026-02-30T03:04:05Z",
        "2026-01-02T25:04:05Z",
        "2026-01-02T03:61:05Z",
        "2026-01-02T03:04:05+25:00",
        "2026-01-02T03:04:05+03:99",
        "2026-01-02T03:04:05Z trailing",
        "x2026-01-02T03:04:05Z",
        "1999-12-31T23:59:59Z",
        "2100-01-01T00:00:00Z",
        "0001-01-01T00:00:00Z",
        "9999-12-31T23:59:59+00:00",
        0,
        -1,
        12,
        946684799,
        4102444800,
        20260102,
        float("nan"),
        float("inf"),
        float("-inf"),
        10**30,
        "1e12",
        "0x5f",
        datetime(2026, 1, 2, 3, 4, 5),
        [2026, 1, 2],
        {"seconds": 1767323045},
        b"2026-01-02T03:04:05Z",
    ],
)
def test_what_is_not_a_timestamp_gives_none(given):
    assert parse_timestamp(given) is None


def test_text_without_an_offset_needs_to_be_told_its_zone():
    assert parse_timestamp("2026-01-02T03:04:05") is None
    assert parse_timestamp("2026-01-02T03:04:05", naive_as=UTC) == SECOND
    assert parse_timestamp("2026-01-02T03-04-05", naive_as=UTC) == SECOND
    assert parse_timestamp(datetime(2026, 1, 2, 3, 4, 5), naive_as=UTC) == SECOND
    plus_three = timezone(timedelta(hours=3))
    assert parse_timestamp("2026-01-02T06:04:05", naive_as=plus_three) == SECOND
    # A stated offset always wins over the fallback.
    assert parse_timestamp("2026-01-02T03:04:05Z", naive_as=plus_three) == SECOND


def test_a_named_zone_applies_its_own_summer_time():
    try:
        stockholm = ZoneInfo("Europe/Stockholm")
    except ZoneInfoNotFoundError:
        pytest.skip("this machine has no time-zone database")
    assert parse_timestamp("2026-01-02T04:04:05", naive_as=stockholm) == SECOND
    assert parse_timestamp("2026-07-02T05:04:05", naive_as=stockholm) == SECOND.replace(month=7)


def test_the_accepted_range_is_exactly_two_thousand_to_twenty_one_hundred():
    assert parse_timestamp("2000-01-01T00:00:00Z") == EARLIEST
    assert parse_timestamp(946684800) == EARLIEST
    assert parse_timestamp("2099-12-31T23:59:59.999999Z") == LATEST - timedelta(microseconds=1)
    assert parse_timestamp(4102444799) == LATEST - timedelta(seconds=1)
    assert parse_timestamp("1999-12-31T23:59:59.999999Z") is None
    assert parse_timestamp("2100-01-01T00:00:00Z") is None
    # An offset can carry a wall-clock date in range to an instant out of range.
    assert parse_timestamp("2000-01-01T00:00:00+01:00") is None
    assert parse_timestamp("2099-12-31T23:30:00-01:00") is None


_OFFSETS = [0, 60, -300, 330, 345, -570, 840, -720]


def _spellings(instant: datetime, rng: random.Random) -> dict[str, tuple[object, datetime]]:
    """Ways a harness could write ``instant``, each with the instant it then means."""
    local = instant.astimezone(timezone(timedelta(minutes=rng.choice(_OFFSETS))))
    to_milli = instant.replace(microsecond=instant.microsecond // 1000 * 1000)
    to_second = instant.replace(microsecond=0)
    epoch = instant - datetime(1970, 1, 1, tzinfo=UTC)
    micros = epoch // timedelta(microseconds=1)
    return {
        "iso with offset": (local.isoformat(), instant),
        "iso with space": (local.isoformat(sep=" "), instant),
        "iso utc with Z": (instant.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z", instant),
        "iso milliseconds": (instant.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", to_milli),
        "iso seconds": (instant.strftime("%Y-%m-%dT%H:%M:%SZ"), to_second),
        "iso compact offset": (local.strftime("%Y-%m-%dT%H:%M:%S%z"), to_second),
        "file name": (instant.strftime("%Y-%m-%dT%H-%M-%S-%f")[:-3] + "Z", to_milli),
        "epoch seconds": (micros // 1_000_000, to_second),
        "epoch milliseconds": (micros // 1000, to_milli),
        "epoch microseconds": (micros, instant),
        "epoch nanoseconds": (micros * 1000 + rng.randrange(1000), instant),
        "epoch text": (str(micros // 1000), to_milli),
        "aware datetime": (local, instant),
    }


@pytest.mark.parametrize("seed", range(300))
def test_any_instant_survives_any_spelling(seed):
    rng = random.Random(seed)
    span = int((LATEST - EARLIEST).total_seconds()) - 86400 * 2
    instant = (
        EARLIEST
        + timedelta(days=1)
        + timedelta(seconds=rng.randrange(span), microseconds=rng.randrange(1_000_000))
    )
    for name, (written, meant) in _spellings(instant, rng).items():
        assert parse_timestamp(written) == meant, f"{name}: {written!r}"


@pytest.mark.parametrize("seed", range(100))
def test_float_seconds_are_right_to_the_microsecond(seed):
    rng = random.Random(seed)
    instant = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(
        seconds=rng.randrange(10**8), microseconds=rng.randrange(1_000_000)
    )
    parsed = parse_timestamp(instant.timestamp())
    assert abs(parsed - instant) <= timedelta(microseconds=1)


# -- token usage ------------------------------------------------------------------

USAGE_VECTORS = [
    (
        "anthropic, cache counts are separate from input",
        UsageFormat.ANTHROPIC,
        {
            "input_tokens": 12,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 4000,
            "output_tokens": 250,
            "service_tier": "standard",
        },
        {
            "input_tokens": 12,
            "output_tokens": 250,
            "cache_read_tokens": 4000,
            "cache_write_tokens": 300,
        },
    ),
    (
        "anthropic, cache writes only by lifetime",
        UsageFormat.ANTHROPIC,
        {
            "input_tokens": 5,
            "output_tokens": 7,
            "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 20},
        },
        {"input_tokens": 5, "output_tokens": 7, "cache_write_tokens": 120},
    ),
    (
        "anthropic, the stated total wins over the breakdown",
        UsageFormat.ANTHROPIC,
        {
            "output_tokens": 1,
            "cache_creation_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 9},
        },
        {"output_tokens": 1, "cache_write_tokens": 0},
    ),
    (
        "openai, cached input and reasoning are parts of the totals",
        UsageFormat.OPENAI,
        {
            "input_tokens": 5000,
            "cached_input_tokens": 4200,
            "output_tokens": 900,
            "reasoning_output_tokens": 640,
            "total_tokens": 5900,
        },
        {
            "input_tokens": 800,
            "output_tokens": 900,
            "cache_read_tokens": 4200,
            "reasoning_tokens": 640,
        },
    ),
    (
        "openai, nested detail fields",
        UsageFormat.OPENAI,
        {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 30},
            "output_tokens": 50,
            "output_tokens_details": {"reasoning_tokens": 20},
        },
        {"input_tokens": 70, "output_tokens": 50, "cache_read_tokens": 30, "reasoning_tokens": 20},
    ),
    (
        "openai, nothing cached",
        UsageFormat.OPENAI,
        {"input_tokens": 100, "output_tokens": 50},
        {"input_tokens": 100, "output_tokens": 50},
    ),
    (
        "openai, a cached count above the input is not allowed to go negative",
        UsageFormat.OPENAI,
        {"input_tokens": 10, "cached_input_tokens": 25, "output_tokens": 1},
        {"input_tokens": 0, "output_tokens": 1, "cache_read_tokens": 25},
    ),
    (
        "gemini, thoughts are added to the output",
        UsageFormat.GEMINI,
        {
            "promptTokenCount": 1000,
            "cachedContentTokenCount": 400,
            "candidatesTokenCount": 200,
            "thoughtsTokenCount": 150,
            "totalTokenCount": 1350,
        },
        {
            "input_tokens": 600,
            "output_tokens": 350,
            "cache_read_tokens": 400,
            "reasoning_tokens": 150,
        },
    ),
    (
        "gemini, no thoughts",
        UsageFormat.GEMINI,
        {"promptTokenCount": 10, "candidatesTokenCount": 4},
        {"input_tokens": 10, "output_tokens": 4},
    ),
    (
        "pi, with its cost",
        UsageFormat.PI,
        {
            "input": 20,
            "output": 30,
            "cacheRead": 500,
            "cacheWrite": 40,
            "totalTokens": 590,
            "cost": {"input": 0.0001, "output": 0.0006, "total": 0.00125},
        },
        {
            "input_tokens": 20,
            "output_tokens": 30,
            "cache_read_tokens": 500,
            "cache_write_tokens": 40,
            "reported_cost": 0.00125,
            "cost_currency": "USD",
        },
    ),
    (
        "pi, a free local model still reports a cost of zero",
        UsageFormat.PI,
        {"input": 20, "output": 30, "cost": {"total": 0}},
        {"input_tokens": 20, "output_tokens": 30, "reported_cost": 0.0, "cost_currency": "USD"},
    ),
    (
        "whole floats are counts, other junk is dropped",
        UsageFormat.ANTHROPIC,
        {
            "input_tokens": 12.0,
            "output_tokens": "250",
            "cache_read_input_tokens": -4,
            "cache_creation_input_tokens": True,
        },
        {"input_tokens": 12},
    ),
    (
        "zero is a reported number and is kept",
        UsageFormat.ANTHROPIC,
        {"input_tokens": 0, "output_tokens": 0},
        {"input_tokens": 0, "output_tokens": 0},
    ),
]


@pytest.mark.parametrize(
    ("usage_format", "raw", "expected"),
    [vector[1:] for vector in USAGE_VECTORS],
    ids=[vector[0] for vector in USAGE_VECTORS],
)
def test_usage_vectors(usage_format, raw, expected):
    before = repr(raw)
    payload = normalise_usage(usage_format, raw)
    assert payload == {"scope": "message", **expected}
    assert repr(raw) == before, "the input was modified"
    validate_event(EventKind.USAGE, payload)
    # The format may be given as plain text, as it is stored.
    assert normalise_usage(str(usage_format), raw) == payload


@pytest.mark.parametrize("usage_format", list(UsageFormat))
@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"unrelated": 5},
        {"input_tokens": None, "output_tokens": "many", "input": [], "promptTokenCount": 1.5},
        {"cost": {"total": -1}},
        {"cost": {"total": float("nan")}},
        {"cost": 0.5},
        None,
        [],
        "input_tokens=5",
        17,
    ],
)
def test_a_record_with_nothing_usable_gives_none(usage_format, raw):
    assert normalise_usage(usage_format, raw) is None


def test_the_scope_is_stated_by_the_caller():
    raw = {"input_tokens": 1}
    assert normalise_usage(UsageFormat.OPENAI, raw)["scope"] == "message"
    total = normalise_usage(UsageFormat.OPENAI, raw, scope=UsageScope.SESSION_TOTAL)
    assert total["scope"] == "session_total"
    assert normalise_usage(UsageFormat.OPENAI, raw, scope="session_total") == total
    with pytest.raises(ValueError):
        normalise_usage(UsageFormat.OPENAI, raw, scope="turn")


def test_an_unknown_format_is_refused():
    with pytest.raises(ValueError):
        normalise_usage("other", {"input_tokens": 1})


_JUNK = [None, True, False, -3, 2.5, "7", [], {}, float("nan"), float("inf"), 4.0, 10**15]
_FIELDS = {
    UsageFormat.ANTHROPIC: [
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ],
    UsageFormat.OPENAI: [
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    ],
    UsageFormat.GEMINI: [
        "promptTokenCount",
        "cachedContentTokenCount",
        "candidatesTokenCount",
        "thoughtsTokenCount",
    ],
    UsageFormat.PI: ["input", "output", "cacheRead", "cacheWrite", "cost"],
}


@pytest.mark.parametrize("seed", range(300))
def test_whatever_comes_in_the_result_follows_the_event_contract(seed):
    rng = random.Random(seed)
    usage_format = rng.choice(list(UsageFormat))
    raw = {}
    for field in _FIELDS[usage_format]:
        roll = rng.random()
        if roll < 0.6:
            raw[field] = rng.randrange(0, 10**7)
        elif roll < 0.8:
            raw[field] = rng.choice(_JUNK)
    if "cost" in raw:
        raw["cost"] = {"total": rng.choice([0.25, 0, -1, None, "0.1", float("inf")])}

    payload = normalise_usage(usage_format, raw, scope=rng.choice(list(UsageScope)))
    if payload is None:
        return
    validate_event(EventKind.USAGE, payload)
    counts = {name: number for name, number in payload.items() if name.endswith("_tokens")}
    assert all(type(number) is int and number >= 0 for number in counts.values())
    if "reasoning_tokens" in payload and "output_tokens" in payload:
        if usage_format is UsageFormat.GEMINI:
            assert payload["reasoning_tokens"] <= payload["output_tokens"]


@pytest.mark.parametrize("seed", range(100))
def test_no_token_is_counted_twice_or_lost(seed):
    """Fresh input plus cached input is the input the source reported, in every format."""
    rng = random.Random(seed)
    fresh, cached, written = rng.randrange(10**6), rng.randrange(10**6), rng.randrange(10**5)
    answer, thoughts = rng.randrange(10**5), rng.randrange(10**5)

    sources = {
        UsageFormat.ANTHROPIC: {
            "input_tokens": fresh,
            "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": written,
            "output_tokens": answer + thoughts,
        },
        UsageFormat.OPENAI: {
            "input_tokens": fresh + cached,
            "cached_input_tokens": cached,
            "output_tokens": answer + thoughts,
            "reasoning_output_tokens": thoughts,
        },
        UsageFormat.GEMINI: {
            "promptTokenCount": fresh + cached,
            "cachedContentTokenCount": cached,
            "candidatesTokenCount": answer,
            "thoughtsTokenCount": thoughts,
        },
        UsageFormat.PI: {
            "input": fresh,
            "cacheRead": cached,
            "cacheWrite": written,
            "output": answer + thoughts,
        },
    }
    for usage_format, raw in sources.items():
        payload = normalise_usage(usage_format, raw)
        assert payload["input_tokens"] == fresh, usage_format
        assert payload["cache_read_tokens"] == cached, usage_format
        assert payload["output_tokens"] == answer + thoughts, usage_format
        if "reasoning_tokens" in payload:
            assert payload["reasoning_tokens"] == thoughts, usage_format
        if "cache_write_tokens" in payload:
            assert payload["cache_write_tokens"] == written, usage_format
