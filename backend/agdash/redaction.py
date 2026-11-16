"""Remove secrets from text, process arguments and structured data before storage.

Two kinds of evidence are used. A *shape* is a token format that is a secret
wherever it appears, such as a provider key prefix or a private-key block. A
*name* is a field, variable or flag called something like ``api_key`` or
``password``; its value is removed whatever it looks like.

This is a filter for known shapes and telling names, not a guarantee: a secret
with neither passes through. Redaction is applied before anything is stored,
and is safe to apply twice.
"""

import re
from collections.abc import Sequence
from typing import Any

MARKER_PREFIX = "[REDACTED"
DEFAULT_MAX_ARGUMENT_LENGTH = 4096


def marker(kind: str) -> str:
    return f"{MARKER_PREFIX}:{kind}]"


VALUE_MARKER = marker("value")

# -- names ---------------------------------------------------------------------

# A name is telling when it *ends* in one of these, so ``refresh_token`` and
# ``OPENAI_API_KEY`` are caught while ``token_count`` and ``max_tokens`` are not.
_ALWAYS_SECRET = r"pass(?:word|wd|phrase)|secret"
_USUALLY_SECRET = (
    r"token|api[_ -]?key|access[_ -]?key|private[_ -]?key|secret[_ -]?key|auth[_ -]?key"
    r"|session[_ -]?key|signing[_ -]?key|encryption[_ -]?key|account[_ -]?key"
    r"|credentials?|authorization|cookie"
)
_NAME = rf"[A-Za-z0-9_.-]*(?:{_ALWAYS_SECRET}|{_USUALLY_SECRET})"
_NAME_PATTERN = re.compile(_NAME, re.IGNORECASE)
_ALWAYS_PATTERN = re.compile(rf"[A-Za-z0-9_.-]*(?:{_ALWAYS_SECRET})", re.IGNORECASE)

# Not secrets: empty and placeholder values, and the type names that follow a
# name in code (``password: str``).
_NOT_A_VALUE = frozenset(
    {"", "true", "false", "null", "none", "nil", "yes", "no", "undefined"}
    | {"str", "string", "bool", "boolean", "int", "integer", "bytes"}
)
_MIN_SECRET_LENGTH = 8
_LONG_ENOUGH_WITHOUT_DIGITS = 20


def is_sensitive_name(name: str) -> bool:
    """Whether a field, variable or flag name says its value is a secret."""
    return _NAME_PATTERN.fullmatch(name.strip().lstrip("-")) is not None


def _always_secret(name: str) -> bool:
    return _ALWAYS_PATTERN.fullmatch(name.strip().lstrip("-")) is not None


def _looks_like_a_secret(value: str) -> bool:
    """For names that only usually hold a secret: is this value plausibly one?"""
    if len(value) < _MIN_SECRET_LENGTH or value.isdigit():
        return False
    return len(value) >= _LONG_ENOUGH_WITHOUT_DIGITS or any(char.isdigit() for char in value)


# -- shapes --------------------------------------------------------------------

_START = r"(?<![A-Za-z0-9])"
# A name is only looked for at the true start of a run of name characters. If it
# could start after any "-" or "_", a long run of them would be rescanned from
# every position and one separator line would take seconds.
_NAME_START = r"(?<![A-Za-z0-9_.-])"


def _shape(kind: str, pattern: str, flags: int = 0) -> tuple[re.Pattern[str], str]:
    return re.compile(pattern, flags), marker(kind)


_SHAPES: tuple[tuple[re.Pattern[str], str], ...] = (
    _shape(
        "private-key",
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
        re.DOTALL,
    ),
    _shape("jwt", _START + r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    _shape("anthropic-key", _START + r"sk-ant-[A-Za-z0-9_-]{16,}"),
    _shape("api-key", _START + r"sk-[A-Za-z0-9_-]{20,}"),
    _shape("stripe-key", _START + r"(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}"),
    _shape("github-token", _START + r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    _shape("gitlab-token", _START + r"glpat-[A-Za-z0-9_-]{20,}"),
    _shape("google-api-key", _START + r"AIza[A-Za-z0-9_-]{35}"),
    _shape("google-oauth-token", _START + r"(?:ya29\.[A-Za-z0-9_-]{20,}|1//[A-Za-z0-9_-]{30,})"),
    _shape("aws-access-key", _START + r"(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
    _shape("slack-token", _START + r"xox[abeprs]-[A-Za-z0-9-]{10,}"),
    _shape("huggingface-token", _START + r"hf_[A-Za-z0-9]{30,}"),
    _shape("npm-token", _START + r"npm_[A-Za-z0-9]{36}"),
    _shape("pypi-token", _START + r"pypi-[A-Za-z0-9_-]{40,}"),
    _shape("xai-key", _START + r"xai-[A-Za-z0-9]{40,}"),
    _shape("workchord-key", _START + r"pmag_[A-Za-z0-9_-]{20,}"),
    _shape("telegram-token", _START + r"\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"),
    _shape("webhook-url", r"hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}"),
    _shape("webhook-url", r"discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]{20,}"),
)

# ``Authorization: Bearer <token>``. The scheme word stays.
_AUTH_SCHEME = re.compile(
    _START + r"(?P<scheme>bearer|basic|token)(?P<gap>\s+)(?P<value>[A-Za-z0-9._~+/=-]{16,})",
    re.IGNORECASE,
)
# ``scheme://user:password@host``. The user stays.
_URL_PASSWORD = re.compile(r"(?P<head>://[^\s/:@]*:)(?P<value>[^\s/@]+)(?=@)")
# ``curl -u user:password``. The user stays.
_USER_FLAG = re.compile(r"(?P<head>(?<!\S)(?:-u|--user)[ =]+[^\s:]+:)(?P<value>[^\s\"']+)")
# ``machine host login user password VALUE`` in a netrc file.
_NETRC = re.compile(
    r"(?P<head>\bmachine\s+\S+\s+login\s+\S+\s+password\s+)(?P<value>\S+)", re.IGNORECASE
)
# ``aws_session_token VALUE``: a telling name, a space, then a long opaque value.
_NAME_THEN_BLOB = re.compile(
    rf"(?P<head>{_NAME_START}{_NAME}[ \t]+)(?P<value>[^\s\"'\\]{{20,}})", re.IGNORECASE
)
# ``Cookie: a=1; b=2``: everything to the end of the line.
_COOKIE_HEADER = re.compile(r"(?P<head>(?:set-)?cookie\s*:[ \t]*)(?P<value>[^\r\n\"'\\]+)", re.I)

# ``name = value`` in its common spellings: env, JSON, JSON inside a JSON string,
# query strings and headers. An unquoted value runs to the next space or quote:
# cutting it at "&" or ";" would leak the rest of a password that contains one.
_ASSIGNMENT = re.compile(
    rf"""
    (?P<name>{_NAME_START}{_NAME})
    (?P<closing>\\?["']?) \s* (?P<separator>[:=]) \s*
    (?:
        (?P<q1>\\") (?P<v1>.*?) \\"
      | (?P<q2>")   (?P<v2>(?:[^"\\\n]|\\.)*) "
      | (?P<q3>')   (?P<v3>(?:[^'\\\n]|\\.)*) '
      |             (?P<v4>[^\s"'\\]+)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _redact_assignment(match: re.Match[str]) -> str:
    group = next(n for n in ("v1", "v2", "v3", "v4") if match.group(n) is not None)
    value = match.group(group)
    if value.startswith(MARKER_PREFIX) or value.startswith("$"):
        return match.group(0)
    if value.rstrip(",;)}]").lower() in _NOT_A_VALUE:
        return match.group(0)
    if not _always_secret(match.group("name")) and not _looks_like_a_secret(value):
        return match.group(0)
    start, end = match.span(group)
    whole_start = match.start()
    return (
        match.group(0)[: start - whole_start] + VALUE_MARKER + match.group(0)[end - whole_start :]
    )


# ``--password VALUE`` inside a command written out as text.
_FLAG_THEN_VALUE = re.compile(
    rf"""
    (?P<head>(?<![^\s"'=(]) --?{_NAME} \s+)
    (?: "(?P<f1>[^"\n]*)" | '(?P<f2>[^'\n]*)' | (?P<f3>[^\s"'\\]+) )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _redact_flag_value(match: re.Match[str]) -> str:
    group = next(n for n in ("f1", "f2", "f3") if match.group(n) is not None)
    value = match.group(group)
    name = match.group("head").split()[0]
    if value.startswith(MARKER_PREFIX) or value.startswith("$"):
        return match.group(0)
    # Another flag, not a value. A random token can begin with a dash, so only
    # something that reads as a plain flag counts.
    if _PLAIN_FLAG.fullmatch(value) or _SHORT_FLAG.fullmatch(value):
        return match.group(0)
    if not _always_secret(name) and not _looks_like_a_secret(value):
        return match.group(0)
    start, end = match.span(group)
    whole_start = match.start()
    return (
        match.group(0)[: start - whole_start] + VALUE_MARKER + match.group(0)[end - whole_start :]
    )


def _redact_scheme(match: re.Match[str]) -> str:
    if not _looks_like_a_secret(match.group("value")):
        return match.group(0)
    return match.group("scheme") + match.group("gap") + marker("credential")


def _redact_blob(match: re.Match[str]) -> str:
    if not any(char.isdigit() for char in match.group("value")):
        return match.group(0)
    return match.group("head") + VALUE_MARKER


def _redact_user_flag(match: re.Match[str]) -> str:
    if match.group("value").isdigit():  # ``-u 1000:1000`` is a user and group id
        return match.group(0)
    return match.group("head") + marker("password")


# Every step of :func:`redact_text`, in order, with a guard: a cheap test that any match
# of the step's pattern needs, run on the text as it stands at that step. A step whose
# guard fails cannot change the text, so skipping it gives exactly the same result.
# Most text holds no secret at all, and the guards let it pass without the costlier
# patterns being tried at every position.
_KEYWORD = r"(?:pass(?:word|wd|phrase)|secret|token|key|credentials?|authorization|cookie)"


# A guard is called with the text and, where it is safe, its lower-case copy.
# These patterns spell their letters in ASCII, and under case-insensitive matching
# an ASCII letter also matches exactly four other characters: İ, ı, ſ and the
# Kelvin sign (Python's re documentation). In text without them, matching a
# pattern case-insensitively equals matching its plain form against the lower-case
# text, which is much faster; text with one of them keeps the case-insensitive
# pattern.
_FOLDS_TO_ASCII = re.compile("[\u0130\u0131\u017f\u212a]").search


def _lowered(text: str) -> str | None:
    if text.isascii() or not _FOLDS_TO_ASCII(text):
        return text.lower()
    return None


def _literal(*needles: str):
    if len(needles) == 1:
        (needle,) = needles
        return lambda text, _lower: needle in text
    first, second = needles
    return lambda text, _lower: first in text or second in text


def _found(pattern: str):
    search = re.compile(pattern).search
    return lambda text, _lower: search(text)


def _found_any_case(pattern: str):
    plain = re.compile(pattern).search
    folded = re.compile(pattern, re.IGNORECASE).search
    return lambda text, lower: plain(lower) if lower is not None else folded(text)


_SHAPE_GUARDS = {
    "private-key": _literal("-----BEGIN "),
    "jwt": _literal("eyJ"),
    "anthropic-key": _literal("sk-ant-"),
    "api-key": _literal("sk-"),
    "stripe-key": _literal("_live_", "_test_"),
    "github-token": _found(r"gh[pousr]_|github_pat_"),
    "gitlab-token": _literal("glpat-"),
    "google-api-key": _literal("AIza"),
    "google-oauth-token": _literal("ya29.", "1//"),
    "aws-access-key": _literal("AKIA", "ASIA"),
    "slack-token": _literal("xox"),
    "huggingface-token": _literal("hf_"),
    "npm-token": _literal("npm_"),
    "pypi-token": _literal("pypi-"),
    "xai-key": _literal("xai-"),
    "workchord-key": _literal("pmag_"),
    "telegram-token": _found(r"\d{8}:"),
}
_WEBHOOK_GUARDS = (_literal("hooks.slack.com/services/"), _literal("/api/webhooks/"))


def _shape_steps():
    webhooks = iter(_WEBHOOK_GUARDS)
    for pattern, replacement in _SHAPES:
        kind = replacement[len(MARKER_PREFIX) + 1 : -1]
        guard = next(webhooks) if kind == "webhook-url" else _SHAPE_GUARDS[kind]
        yield guard, lambda text, p=pattern, r=replacement: p.sub(r, text)


_STEPS = (
    (
        _found_any_case(_KEYWORD + r"""\\?["']?\s*[:=]"""),
        lambda text: _ASSIGNMENT.sub(_redact_assignment, text),
    ),
    (
        _found_any_case(_KEYWORD + r"\s"),
        lambda text: _FLAG_THEN_VALUE.sub(_redact_flag_value, text),
    ),
    *_shape_steps(),
    (
        _found_any_case(r"(?:bearer|basic|token)\s"),
        lambda text: _AUTH_SCHEME.sub(_redact_scheme, text),
    ),
    (
        lambda text, _lower: "://" in text and "@" in text,
        lambda text: _URL_PASSWORD.sub(lambda m: m.group("head") + marker("password"), text),
    ),
    (_literal("-u"), lambda text: _USER_FLAG.sub(_redact_user_flag, text)),
    (
        _found_any_case(r"machine\s"),
        lambda text: _NETRC.sub(lambda m: m.group("head") + marker("password"), text),
    ),
    (
        _found_any_case(_KEYWORD + r"""[ \t]+[^\s"'\\]{20}"""),
        lambda text: _NAME_THEN_BLOB.sub(_redact_blob, text),
    ),
    (
        _found_any_case(r"cookie\s*:"),
        lambda text: _COOKIE_HEADER.sub(lambda m: m.group("head") + marker("cookie"), text),
    ),
)


def redact_text(text: str) -> str:
    """Return ``text`` with every recognised secret replaced by a marker."""
    if not text:
        return text
    lower = _lowered(text)
    for guard, step in _STEPS:
        if guard(text, lower):
            changed = step(text)
            if changed != text:
                text = changed
                lower = _lowered(text)
    return text


# -- process arguments ---------------------------------------------------------

_FLAG = re.compile(r"(?P<flag>--?[A-Za-z0-9][A-Za-z0-9_.-]*)(?:=(?P<value>.*))?", re.DOTALL)
# What follows a secret flag is its value unless it is plainly another flag. A
# random token can begin with dashes, so "starts with --" is not enough.
_PLAIN_FLAG = re.compile(r"--[a-z][a-z-]{0,30}(?:=.*)?", re.DOTALL)
_SHORT_FLAG = re.compile(r"-[A-Za-z]")
_USER_FLAGS = frozenset({"-u", "--user"})


def _truncate(argument: str, limit: int) -> str:
    if len(argument) <= limit:
        return argument
    return f"{argument[:limit]}[TRUNCATED:{len(argument) - limit}]"


def redact_argv(
    argv: Sequence[str], *, max_argument_length: int = DEFAULT_MAX_ARGUMENT_LENGTH
) -> list[str]:
    """Return a process's arguments with secrets removed and long ones cut short.

    Besides everything :func:`redact_text` finds inside each argument, the
    argument after a flag with a telling name (``--api-key VALUE``) is removed
    whole. The count and order of arguments are kept.
    """
    redacted: list[str] = []
    pending: str | None = None
    for argument in argv:
        if pending is not None:
            kind, pending = pending, None
            if kind == "user" and not _PLAIN_FLAG.fullmatch(argument):
                user, colon, password = argument.partition(":")
                keep = argument if not colon or password.isdigit() else user + ":" + VALUE_MARKER
                redacted.append(_truncate(keep, max_argument_length))
                continue
            if kind == "secret" and not _PLAIN_FLAG.fullmatch(argument):
                redacted.append(VALUE_MARKER)
                continue
        flag = _FLAG.fullmatch(argument)
        if flag and flag.group("value") is None:
            if flag.group("flag") in _USER_FLAGS:
                pending = "user"
            elif is_sensitive_name(flag.group("flag")):
                pending = "secret"
            redacted.append(argument)
        elif flag and is_sensitive_name(flag.group("flag")):
            redacted.append(f"{flag.group('flag')}={VALUE_MARKER}")
        else:
            redacted.append(_truncate(redact_text(argument), max_argument_length))
    return redacted


# -- structured data -----------------------------------------------------------


def redact_value(value: Any) -> Any:
    """Return a copy of JSON-like data with secrets removed.

    Strings are passed through :func:`redact_text`. Under a key with a telling
    name the value itself is replaced, since there the pairing is unambiguous.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_member(key, member) for key, member in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(member) for member in value]
    return value


def _redact_member(key: Any, member: Any) -> Any:
    if not isinstance(key, str) or not is_sensitive_name(key):
        return redact_value(member)
    if member is None or isinstance(member, bool):
        return member
    always = _always_secret(key)
    if isinstance(member, str):
        if member.lower() in _NOT_A_VALUE or member.startswith(MARKER_PREFIX):
            return member
        if always or len(member) >= _MIN_SECRET_LENGTH:
            return VALUE_MARKER
        return redact_text(member)
    if isinstance(member, int | float):
        return VALUE_MARKER if always else member
    return VALUE_MARKER if always else redact_value(member)
