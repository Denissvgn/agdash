"""Config readers return allowlisted fields only and never materialise the rest."""

import json
import random
import string

import pytest

from agdash.access import OutsideAllowedRoots
from agdash.access import config as config_module
from agdash.access.config import (
    MAX_VALUE_LENGTH,
    ConfigFormatError,
    ConfigTooLarge,
    presence,
    read_json_fields,
    read_toml_fields,
    value,
)
from agdash.access.guard import assert_untouched

MARKER = "PRIVATE-CREDENTIAL-MARKER"

CREDENTIAL_FILE = {
    "auth_mode": "subscription",
    "PROVIDER_API_KEY": None,
    "tokens": {
        "id_token": f"{MARKER}-id",
        "access_token": f"{MARKER}-access",
        "refresh_token": f"{MARKER}-refresh",
        "account_id": "account-1",
    },
    "last_refresh": "2026-01-02T03:04:05Z",
    "seats": 3,
    "trial": False,
    "nested": {"deep": {"plan": "team", "history": [1, {"refresh_token": f"{MARKER}-old"}]}},
    "awkward": 'has "quotes", a \\ and } ] { [ inside, ' + MARKER,
    "empty_string": "",
    "empty_object": {},
    "empty_list": [],
}

WANTED = [
    value("auth_mode"),
    value("last_refresh"),
    value("seats"),
    value("trial"),
    value("tokens", "account_id"),
    value("nested", "deep", "plan"),
    value("not_there"),
    presence("PROVIDER_API_KEY"),
    presence("tokens", "refresh_token"),
    presence("tokens", "not_there"),
    presence("empty_string"),
    presence("empty_object"),
    presence("empty_list"),
    presence("nested", "deep", "history"),
]

EXPECTED = {
    ("auth_mode",): "subscription",
    ("last_refresh",): "2026-01-02T03:04:05Z",
    ("seats",): 3,
    ("trial",): False,
    ("tokens", "account_id"): "account-1",
    ("nested", "deep", "plan"): "team",
    ("PROVIDER_API_KEY",): False,
    ("tokens", "refresh_token"): True,
    ("tokens", "not_there"): False,
    ("empty_string",): False,
    ("empty_object",): False,
    ("empty_list",): False,
    ("nested", "deep", "history"): True,
}


@pytest.fixture
def credential_file(harness):
    path = harness.root / "auth.json"
    path.write_text(json.dumps(CREDENTIAL_FILE, indent=2))
    return path


def _strings_in_frames(error: BaseException) -> list[str]:
    """Every string held by a local variable in the error's traceback."""
    held = []
    traceback = error.__traceback__
    while traceback is not None:
        held.extend(v for v in traceback.tb_frame.f_locals.values() if isinstance(v, str | bytes))
        traceback = traceback.tb_next
    return [item.decode(errors="replace") if isinstance(item, bytes) else item for item in held]


def test_only_the_listed_fields_come_back(harness, credential_file):
    with assert_untouched([harness.root]):
        found = read_json_fields(harness.access(), credential_file, WANTED)
    assert found == EXPECTED
    assert MARKER not in repr(found)


@pytest.mark.parametrize("layout", [None, 0, 4, "\t"])
def test_layout_does_not_matter(harness, layout):
    path = harness.root / "auth.json"
    separators = (",", ":") if layout is None else None
    path.write_text(json.dumps(CREDENTIAL_FILE, indent=layout, separators=separators))
    assert read_json_fields(harness.access(), path, WANTED) == EXPECTED


def test_values_that_were_not_asked_for_are_never_decoded(harness, credential_file, monkeypatch):
    decoded = []
    real = config_module._DECODER

    class Spy:
        def raw_decode(self, text, index):
            result = real.raw_decode(text, index)
            decoded.append(result[0])
            return result

    monkeypatch.setattr(config_module, "_DECODER", Spy())
    read_json_fields(harness.access(), credential_file, WANTED)

    assert MARKER not in repr(decoded)
    # Only key names on the way to a wanted field, and the wanted scalars.
    assert "refresh_token" in decoded
    assert "subscription" in decoded
    assert "id_token" in decoded
    assert not any(isinstance(item, dict | list) for item in decoded)


@pytest.mark.parametrize(
    "path",
    [
        ("tokens", "access_token"),
        ("tokens", "refresh_token"),
        ("api_key",),
        ("account", "clientSecret"),
        ("password",),
        ("credentials", "kind"),
    ],
)
def test_a_field_named_like_a_secret_can_only_be_asked_for_by_presence(
    harness, credential_file, path
):
    with pytest.raises(ValueError, match="named like a secret"):
        read_json_fields(harness.access(), credential_file, [value(*path)])
    assert read_json_fields(harness.access(), credential_file, [presence(*path)]) in (
        {path: True},
        {path: False},
    )


def test_a_container_is_never_returned_as_a_value(harness, credential_file):
    found = read_json_fields(
        harness.access(), credential_file, [value("nested"), value("empty_list"), value("seats")]
    )
    assert found == {("seats",): 3}


def test_returned_strings_are_redacted_and_bounded(harness):
    path = harness.root / "settings.json"
    key = "sk-" + "a1B2" * 12
    path.write_text(json.dumps({"note": f"key {key}", "long": "x" * 5000, "model": "example-1"}))
    found = read_json_fields(harness.access(), path, [value("note"), value("long"), value("model")])
    assert key not in found[("note",)]
    assert found[("note",)].startswith("key [REDACTED")
    assert len(found[("long",)]) == MAX_VALUE_LENGTH
    assert found[("model",)] == "example-1"


def test_the_last_of_repeated_keys_wins(harness):
    path = harness.root / "settings.json"
    path.write_text('{"mode": "first", "mode": "second", "x": {"y": 1}, "x": {"y": 2}}')
    found = read_json_fields(harness.access(), path, [value("mode"), value("x", "y")])
    assert found == {("mode",): "second", ("x", "y"): 2}


def test_a_byte_order_mark_is_accepted(harness):
    path = harness.root / "settings.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"mode": "plain"}')
    assert read_json_fields(harness.access(), path, [value("mode")]) == {("mode",): "plain"}


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "[]",
        '"just a string"',
        '{"auth_mode": "x"',
        '{"auth_mode": "x",}',
        '{"auth_mode" "x"}',
        '{"auth_mode": }',
        '{"auth_mode": "x"} trailing',
        '{"tokens": {"refresh_token": "MARKER"]}',
        '{"tokens": ["MARKER"}, "auth_mode": "x"}',
        '{"tokens": "MARKER',
        '{"tokens": {"refresh_token": "MARKER\\',
        '{"auth_mode": tru}',
        "{auth_mode: 1}",
        '{"auth_mode": "x" "tokens": "MARKER"}',
    ],
)
def test_a_malformed_file_is_reported_without_its_content(harness, text):
    path = harness.root / "auth.json"
    path.write_text(text.replace("MARKER", MARKER))
    with pytest.raises(ConfigFormatError) as raised:
        read_json_fields(harness.access(), path, [value("auth_mode"), presence("tokens")])

    error = raised.value
    assert MARKER not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not any(MARKER in held for held in _strings_in_frames(error))


def test_a_file_that_is_not_text_is_reported(harness):
    path = harness.root / "auth.json"
    path.write_bytes(b'{"auth_mode": "\xff\xfe"}')
    with pytest.raises(ConfigFormatError, match="not UTF-8"):
        read_json_fields(harness.access(), path, [value("auth_mode")])


def test_an_oversized_file_is_refused(harness, credential_file):
    with pytest.raises(ConfigTooLarge):
        read_json_fields(harness.access(), credential_file, [value("auth_mode")], max_bytes=64)


def test_files_are_reached_through_the_access_layer(harness):
    harness.secret.write_text('{"auth_mode": "x"}')
    with pytest.raises(OutsideAllowedRoots):
        read_json_fields(harness.access(), harness.secret, [value("auth_mode")])
    with pytest.raises(OutsideAllowedRoots):
        read_toml_fields(harness.access(), harness.secret, [value("auth_mode")])
    with pytest.raises(FileNotFoundError):
        read_json_fields(harness.access(), harness.root / "absent.json", [value("auth_mode")])


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ([value()], "at least one"),
        ([value("a", "")], "at least one"),
        ([value("a"), value("a")], "listed twice"),
        ([value("a"), presence("a")], "listed twice"),
        ([value("a"), value("a", "b")], "both a field and a parent"),
        ([presence("a"), value("a", "b")], "both a field and a parent"),
    ],
)
def test_a_muddled_field_list_is_refused(harness, credential_file, fields, message):
    with pytest.raises(ValueError, match=message):
        read_json_fields(harness.access(), credential_file, fields)


# -- agreement with the standard decoder ----------------------------------------

_TRICKY = ['"', "\\", "{", "}", "[", "]", ",", ":", "\n", "\t", "é", "☃", "\U0001f600", " "]


def _random_string(rng: random.Random) -> str:
    pieces = rng.choices([*string.ascii_letters, *string.digits, *_TRICKY], k=rng.randint(0, 12))
    return "".join(pieces)


def _random_value(rng: random.Random, depth: int):
    kinds = ["string", "int", "float", "bool", "null"]
    if depth < 4:
        kinds += ["object", "object", "array"]
    kind = rng.choice(kinds)
    if kind == "string":
        return _random_string(rng)
    if kind == "int":
        return rng.randint(-(10**12), 10**12)
    if kind == "float":
        return rng.choice([0.0, -1.5, 3.25e10, 1e-7, 123456.789])
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "null":
        return None
    if kind == "array":
        return [_random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {
        f"k{index}{_random_string(rng)[:3]}": _random_value(rng, depth + 1)
        for index in range(rng.randint(0, 5))
    }


def _object_paths(node, prefix=()):
    """Every path that only passes through objects, with the node it leads to."""
    if isinstance(node, dict):
        for key, child in node.items():
            if key:
                yield (*prefix, key), child
                yield from _object_paths(child, (*prefix, key))


def _is_scalar(node) -> bool:
    return node is None or isinstance(node, str | int | float | bool)


@pytest.mark.parametrize("seed", range(150))
def test_the_skipping_reader_agrees_with_the_standard_decoder(harness, seed, monkeypatch):
    # The names are random here, so let any of them be asked for by value.
    monkeypatch.setattr(config_module, "is_sensitive_name", lambda name: False)
    monkeypatch.setattr(config_module, "redact_text", lambda text: text)
    rng = random.Random(seed)
    document = {f"top{index}": _random_value(rng, 1) for index in range(rng.randint(1, 6))}
    path = harness.root / "random.json"
    path.write_text(
        json.dumps(
            document,
            indent=rng.choice([None, 1, 3]),
            ensure_ascii=rng.random() < 0.5,
        ),
        encoding="utf-8",
    )

    candidates = list(_object_paths(document))
    rng.shuffle(candidates)
    fields, expected, taken = [], {}, set()
    for field_path, node in candidates:
        related = any(
            field_path[: len(other)] == other or other[: len(field_path)] == field_path
            for other in taken
        )
        if related:
            continue
        taken.add(field_path)
        if rng.random() < 0.5:
            fields.append(presence(*field_path))
            expected[field_path] = node not in (None, "", {}, [])
        else:
            fields.append(value(*field_path))
            if _is_scalar(node):
                expected[field_path] = node[:MAX_VALUE_LENGTH] if isinstance(node, str) else node
    fields.append(presence("absent"))
    expected[("absent",)] = False

    assert read_json_fields(harness.access(), path, fields) == expected


# -- TOML ------------------------------------------------------------------------

TOML_FILE = f"""
model = "example-model-1"
model_provider = "example"
approval_policy = "on-request"
retries = 3

[mcp_servers.tracker]
url = "http://127.0.0.1:8480/mcp"

[mcp_servers.tracker.env]
TRACKER_API_KEY = "{MARKER}"

[model_providers.example]
base_url = "https://api.example.test/v1"
env_key = "EXAMPLE_API_KEY"
empty = ""

[features]
fast = true
"""


def test_toml_fields_are_read_by_allowlist(harness):
    path = harness.root / "config.toml"
    path.write_text(TOML_FILE)
    with assert_untouched([harness.root]):
        found = read_toml_fields(
            harness.access(),
            path,
            [
                value("model"),
                value("retries"),
                value("model_providers", "example", "base_url"),
                value("model_providers", "example", "env_key"),
                value("features"),
                value("sandbox_mode"),
                presence("mcp_servers", "tracker", "env", "TRACKER_API_KEY"),
                presence("model_providers", "example", "empty"),
                presence("mcp_servers", "absent"),
            ],
        )
    assert found == {
        ("model",): "example-model-1",
        ("retries",): 3,
        ("model_providers", "example", "base_url"): "https://api.example.test/v1",
        ("model_providers", "example", "env_key"): "EXAMPLE_API_KEY",
        ("mcp_servers", "tracker", "env", "TRACKER_API_KEY"): True,
        ("model_providers", "example", "empty"): False,
        ("mcp_servers", "absent"): False,
    }
    assert MARKER not in repr(found)


def test_toml_secret_names_can_only_be_asked_for_by_presence(harness):
    path = harness.root / "config.toml"
    path.write_text(TOML_FILE)
    with pytest.raises(ValueError, match="named like a secret"):
        read_toml_fields(
            harness.access(), path, [value("mcp_servers", "tracker", "env", "TRACKER_API_KEY")]
        )


def test_malformed_toml_is_reported_without_its_content(harness):
    path = harness.root / "config.toml"
    path.write_text(f'api_key = "{MARKER}"\nmodel = = "broken"\n')
    with pytest.raises(ConfigFormatError) as raised:
        read_toml_fields(harness.access(), path, [value("model")])
    error = raised.value
    assert MARKER not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert not any(MARKER in held for held in _strings_in_frames(error))
