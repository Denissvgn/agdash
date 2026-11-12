"""Seeded secrets in text, arguments and structured data are removed; ordinary text is not."""

import base64
import json
import random
import string
import time
from pathlib import Path

import pytest

from agdash.redaction import (
    MARKER_PREFIX,
    VALUE_MARKER,
    is_sensitive_name,
    redact_argv,
    redact_text,
    redact_value,
)

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "secret_corpus.json").read_text(encoding="utf-8")
)
SEED = 20261112
LEAK_WINDOW = 8

ALPHABETS = {
    "alnum": string.ascii_letters + string.digits,
    "alnum_underscore": string.ascii_letters + string.digits + "_",
    "urlsafe": string.ascii_letters + string.digits + "-_",
    "base64": string.ascii_letters + string.digits + "+/",
    "upper_digits": string.ascii_uppercase + string.digits,
    "hex": "0123456789abcdef",
    "lower": string.ascii_lowercase,
    "digits": string.digits,
    "password": string.ascii_letters + string.digits + "!#%,;&+",
    "url_password": string.ascii_letters + string.digits + "._~-",
    "webhook": string.ascii_letters + string.digits + "/",
}


def _segment(rng: random.Random, data: dict) -> str:
    raw = json.dumps({**data, "nonce": rng.getrandbits(64)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _key_block(rng: random.Random, complete: bool) -> str:
    body = "\n".join(
        "".join(rng.choices(ALPHABETS["base64"], k=64)) for _ in range(6 if complete else 3)
    )
    block = "-----BEGIN OPENSSH PRIVATE KEY-----\n" + body
    return block + "\n-----END OPENSSH PRIVATE KEY-----" if complete else block


def generate(shape: str, rng: random.Random) -> str:
    prefix, kind, length = CORPUS["shapes"][shape]
    if kind == "jwt":
        signature = "".join(rng.choices(ALPHABETS["urlsafe"], k=43))
        header = _segment(rng, {"alg": "HS256", "typ": "JWT"})
        return f"{header}.{_segment(rng, {'sub': 'someone'})}.{signature}"
    if kind == "private_key":
        return _key_block(rng, complete=True)
    if kind == "truncated_private_key":
        return _key_block(rng, complete=False)
    if kind == "escaped_private_key":  # as it sits inside a JSON string
        return _key_block(rng, complete=True).replace("\n", "\\n") + "\\n"
    if kind == "telegram":
        return (
            "".join(rng.choices(string.digits, k=9))
            + ":"
            + "".join(rng.choices(ALPHABETS["urlsafe"], k=35))
        )
    return prefix + "".join(rng.choices(ALPHABETS[kind], k=length))


class Seeded:
    """Fills a template's ``<<shape>>`` slots and remembers what it put there."""

    def __init__(self, name: str) -> None:
        self.rng = random.Random(f"{SEED}:{name}")
        self.secrets: list[str] = []

    def fill(self, template):
        if isinstance(template, str):
            while "<<" in template:
                start = template.index("<<")
                end = template.index(">>", start)
                secret = generate(template[start + 2 : end], self.rng)
                self.secrets.append(secret)
                template = template[:start] + secret + template[end + 2 :]
            return template
        if isinstance(template, list):
            return [self.fill(item) for item in template]
        if isinstance(template, dict):
            return {key: self.fill(item) for key, item in template.items()}
        return template


def leaked(secret: str, output: str) -> str | None:
    """A piece of ``secret`` that survives in ``output``, if any."""
    if len(secret) <= LEAK_WINDOW:
        return secret if secret in output else None
    for start in range(len(secret) - LEAK_WINDOW + 1):
        window = secret[start : start + LEAK_WINDOW]
        # A key block's armour is fixed text, not the secret.
        if window in output and not set(window) <= set("-BEGINDOPSHRVATKY \n"):
            return window
    return None


def _ids(entries) -> list[str]:
    return [entry["name"] for entry in entries]


def test_the_corpus_seeds_every_shape_it_declares():
    used = json.dumps([CORPUS["text"], CORPUS["argv"], CORPUS["structured"]])
    for shape in CORPUS["shapes"]:
        assert f"<<{shape}>>" in used, f"no entry seeds {shape}"
    assert len(CORPUS["text"]) >= 45
    assert len(CORPUS["argv"]) >= 10


def test_the_corpus_file_holds_templates_not_secrets():
    raw = (Path(__file__).parent / "fixtures" / "secret_corpus.json").read_text(encoding="utf-8")
    for prefix, kind, length in CORPUS["shapes"].values():
        if prefix and length:
            follow = set(ALPHABETS[kind])
            starts = [i + len(prefix) for i in range(len(raw)) if raw.startswith(prefix, i)]
            for start in starts:
                run = 0
                while start + run < len(raw) and raw[start + run] in follow:
                    run += 1
                assert run < 12, f"something shaped like a real {prefix} value is stored"


@pytest.mark.parametrize("entry", CORPUS["text"], ids=_ids(CORPUS["text"]))
def test_seeded_secrets_are_removed_from_text(entry):
    seeded = Seeded(entry["name"])
    text = seeded.fill(entry["template"])
    assert seeded.secrets

    redacted = redact_text(text)

    for secret in seeded.secrets:
        assert leaked(secret, redacted) is None, entry["name"]
    assert MARKER_PREFIX in redacted
    for kept in entry["keeps"]:
        assert kept in redacted, f"lost surrounding text: {kept!r}"
    assert redact_text(redacted) == redacted, "redacting twice changed the text"


@pytest.mark.parametrize("entry", CORPUS["argv"], ids=_ids(CORPUS["argv"]))
def test_seeded_secrets_are_removed_from_arguments(entry):
    seeded = Seeded(entry["name"])
    argv = seeded.fill(entry["template"])
    assert seeded.secrets

    redacted = redact_argv(argv)

    assert len(redacted) == len(argv)
    joined = "\x00".join(redacted)
    for secret in seeded.secrets:
        assert leaked(secret, joined) is None, entry["name"]
    assert MARKER_PREFIX in joined
    for kept in entry["keeps"]:
        assert kept in joined, f"lost surrounding argument: {kept!r}"
    assert redact_argv(redacted) == redacted, "redacting twice changed the arguments"


@pytest.mark.parametrize("entry", CORPUS["structured"], ids=_ids(CORPUS["structured"]))
def test_seeded_secrets_are_removed_from_structured_data(entry):
    seeded = Seeded(entry["name"])
    data = seeded.fill(entry["template"])
    original = json.dumps(data)

    redacted = redact_value(data)
    dumped = json.dumps(redacted)

    assert json.dumps(data) == original, "the input was modified"
    for secret in seeded.secrets:
        assert leaked(secret, dumped) is None
    for kept in entry["keeps"]:
        assert kept in dumped
    assert redacted["password"] == VALUE_MARKER
    assert redacted["pin_secret"] == VALUE_MARKER
    assert redacted["credentials"]["client_id"] == "public-id"
    assert redacted["attempts"][0]["ok"] is False
    assert redact_value(redacted) == redacted


def test_the_whole_corpus_at_once():
    """Entries next to each other must not shield one another."""
    seeded = Seeded("everything")
    text = "\n".join(seeded.fill(entry["template"]) for entry in CORPUS["text"])
    redacted = redact_text(text)
    for secret in seeded.secrets:
        assert leaked(secret, redacted) is None


@pytest.mark.parametrize("seed", range(40))
def test_other_seeds_are_redacted_too(seed):
    for entry in [*CORPUS["text"], *CORPUS["argv"]]:
        seeded = Seeded(f"{entry['name']}:{seed}")
        filled = seeded.fill(entry["template"])
        output = (
            redact_text(filled) if isinstance(filled, str) else "\x00".join(redact_argv(filled))
        )
        for secret in seeded.secrets:
            assert leaked(secret, output) is None, entry["name"]


@pytest.mark.parametrize("text", CORPUS["benign"])
def test_ordinary_text_is_left_alone(text):
    assert redact_text(text) == text
    assert redact_argv([text]) == [text]
    assert redact_value({"note": text, "items": [text]}) == {"note": text, "items": [text]}


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "DB_PASSWORD",
        "passwd",
        "passphrase",
        "secret",
        "client_secret",
        "clientSecret",
        "token",
        "access_token",
        "refreshToken",
        "GITHUB_TOKEN",
        "api_key",
        "apiKey",
        "x-api-key",
        "OPENAI_API_KEY",
        "aws_secret_access_key",
        "private_key",
        "session-key",
        "credentials",
        "Authorization",
        "cookie",
        "--api-key",
        "--auth-token",
    ],
)
def test_names_that_mark_a_secret(name):
    assert is_sensitive_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "key",
        "primary_key",
        "sort_key",
        "token_count",
        "max_tokens",
        "input_tokens",
        "tokenizer",
        "author",
        "authority",
        "auth_mode",
        "auth_method",
        "password_hint_shown",
        "secret_santa_list",
        "keyboard",
        "model",
        "PWD",
        "aws_access_key_id",
        "",
    ],
)
def test_names_that_do_not(name):
    assert not is_sensitive_name(name)


def test_long_arguments_are_cut_after_redaction():
    seeded = Seeded("long argument")
    secret = seeded.fill("<<openai_key>>")
    prompt = "summarise " + "the report " * 100 + secret
    (cut,) = redact_argv([prompt], max_argument_length=60)
    assert cut.startswith("summarise the report")
    assert cut.endswith("]") and "[TRUNCATED:" in cut
    assert len(cut) < 90
    assert leaked(secret, cut) is None

    # The cut must not slice a marker open and leave the secret's neighbours looking clean.
    (edge,) = redact_argv(["x" * 50 + " " + secret], max_argument_length=55)
    assert leaked(secret, edge) is None


def test_argument_count_and_order_are_kept():
    argv = ["tool", "--token", "--verbose", "--password", "", "end"]
    assert redact_argv(argv) == ["tool", "--token", "--verbose", "--password", VALUE_MARKER, "end"]
    assert redact_argv([]) == []


def test_redaction_accepts_tuples_and_leaves_other_types():
    assert redact_value(("a", 1, None, 2.5, True)) == ["a", 1, None, 2.5, True]
    assert redact_value({1: "x", "token": 12345678}) == {1: "x", "token": 12345678}
    assert redact_text("") == ""


_HOSTILE_SIZE = 60_000
_HOSTILE_INPUTS = {
    "one long token-like run": "".join(
        random.Random(1).choices(ALPHABETS["urlsafe"], k=_HOSTILE_SIZE)
    ),
    "a separator line": "-" * _HOSTILE_SIZE,
    "underscores": "a_" * (_HOSTILE_SIZE // 2),
    "a telling word repeated": "token_" * (_HOSTILE_SIZE // 6),
    "flags": "--secret " * (_HOSTILE_SIZE // 9),
    "an unfinished url": "://" + "a:" * (_HOSTILE_SIZE // 2),
    "an unclosed quote": 'api_key="' + "\\\\" * (_HOSTILE_SIZE // 2),
    "key blocks with no end": ("-----BEGIN PRIVATE KEY-----\n" + "A" * 60 + "\n")
    * (_HOSTILE_SIZE // 90),
    "token prefixes": "eyJ" * (_HOSTILE_SIZE // 3),
    "scheme words": "bearer " * (_HOSTILE_SIZE // 7),
}


@pytest.mark.parametrize("name", _HOSTILE_INPUTS)
def test_awkward_input_is_redacted_in_linear_time(name):
    """A pattern that rescans a long run from every position takes minutes here, not seconds."""
    started = time.perf_counter()
    redact_text(_HOSTILE_INPUTS[name])
    assert time.perf_counter() - started < 5
