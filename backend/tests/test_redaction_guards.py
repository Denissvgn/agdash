"""The guarded redaction gives exactly what running every step unguarded gives.

Each step of ``redact_text`` is skipped when a cheap test says its pattern cannot
match. That is only a speed-up if it never changes a result, so the guarded and
the unguarded runs are compared on the seeded secret corpus, on every line of the
synthetic harness fixtures, and on many random mixes of secrets, keywords,
separators and plain text.
"""

import json
import random
import string
from pathlib import Path

import pytest
from test_redaction import CORPUS, Seeded, generate

from agdash import redaction
from agdash.redaction import redact_text

FIXTURES = Path(__file__).parent / "fixtures" / "harness"


def unguarded(text: str) -> str:
    if not text:
        return text
    for _guard, step in redaction._STEPS:
        text = step(text)
    return text


def corpus_texts() -> list[str]:
    seeded = Seeded("guards")
    texts = [seeded.fill(entry["template"]) for entry in CORPUS["text"]]
    texts += [json.dumps(seeded.fill(entry["template"])) for entry in CORPUS["structured"]]
    texts += [" ".join(seeded.fill(entry["template"])) for entry in CORPUS["argv"]]
    return texts


def test_every_step_has_a_guard():
    assert len(redaction._STEPS) == 2 + len(redaction._SHAPES) + 6


def test_the_corpus_is_redacted_the_same_with_and_without_guards():
    texts = corpus_texts()
    assert len(texts) >= 60
    for text in texts:
        assert redact_text(text) == unguarded(text)


def test_every_fixture_line_is_redacted_the_same():
    lines = [
        line
        for path in sorted(FIXTURES.rglob("*.jsonl"))
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
    ]
    assert len(lines) > 1000
    for line in lines:
        assert redact_text(line) == unguarded(line)


# Pieces that sit at the edges of the patterns: names, separators, scheme words,
# prefixes cut short, and real secret shapes from the corpus generator.
PIECES = [
    "password",
    "Passwd",
    "PASSPHRASE",
    "secret",
    "api_key",
    "API-KEY",
    "access key",
    "token",
    "Token",
    "refresh_token",
    "credential",
    "credentials",
    "authorization",
    "cookie",
    "Set-Cookie",
    "set-cookie:",
    "bearer",
    "Basic",
    "machine",
    "login",
    "-u",
    "--user",
    "--password",
    "-p",
    "=",
    ":",
    ": ",
    '="',
    '\\"',
    "'",
    '"',
    " ",
    "\t",
    "\n",
    "://",
    "@",
    "/",
    "sk-",
    "sk-ant-",
    "_live_",
    "_test_",
    "ghp_",
    "github_pat_",
    "glpat-",
    "AIza",
    "ya29.",
    "1//",
    "AKIA",
    "ASIA",
    "xox",
    "hf_",
    "npm_",
    "pypi-",
    "xai-",
    "pmag_",
    "eyJ",
    "-----BEGIN ",
    "PRIVATE KEY-----",
    "hooks.slack.com/services/",
    "/api/webhooks/",
    "12345678:",
    "$HOME",
    "[REDACTED:value]",
    "true",
    "none",
    "token_count",
    "max_tokens",
    # Letters that fold to ASCII only under case-insensitive matching, and other text.
    "\u017f",
    "\u0131",
    "\u212a",
    "pa\u017f\u017fword",
    "credent\u0131al",
    "\u212aey",
    "\u00e9t\u00e9",
    "\u2014",
    "\U0001f600",
]


@pytest.mark.parametrize("seed", range(40))
def test_random_mixes_are_redacted_the_same(seed):
    rng = random.Random(seed)
    shapes = list(CORPUS["shapes"])
    alphabet = string.ascii_letters + string.digits + "-_./+=:@"
    for _ in range(60):
        parts = []
        for _ in range(rng.randint(1, 14)):
            roll = rng.random()
            if roll < 0.55:
                parts.append(rng.choice(PIECES))
            elif roll < 0.7:
                parts.append(generate(rng.choice(shapes), rng))
            else:
                parts.append("".join(rng.choices(alphabet, k=rng.randint(1, 40))))
        text = "".join(parts)
        assert redact_text(text) == unguarded(text), text
