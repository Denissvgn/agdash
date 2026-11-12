"""Read named fields out of a harness's config or credential file, and nothing else.

A caller lists the fields it wants. Two kinds can be asked for:

* :func:`value` — the field's value, which must be a scalar;
* :func:`presence` — only whether the field is there and not empty. This is
  the only thing that may be asked about a field that holds a secret.

For JSON, values that were not asked for are stepped over character by
character and never turned into Python objects. The file's text is in memory
only while it is scanned, and is not reachable from the result or from any
error raised.

TOML has no skipping parser in the standard library, so a TOML file is parsed
whole, the asked-for fields are picked out, and the parse is dropped before
returning. Do not use the TOML reader on a file whose purpose is to hold
credentials.
"""

import json
import os
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agdash.access.readonly import AccessError, ReadOnlyAccess
from agdash.redaction import is_sensitive_name, redact_text

DEFAULT_MAX_CONFIG_BYTES = 16 * 1024 * 1024
MAX_VALUE_LENGTH = 1024

FieldPath = tuple[str, ...]
Scalar = str | int | float | bool | None


@dataclass(frozen=True)
class Field:
    path: FieldPath
    presence_only: bool = False


def value(*path: str) -> Field:
    """Ask for a scalar field's value. Refused for a field named like a secret."""
    return Field(tuple(path))


def presence(*path: str) -> Field:
    """Ask only whether a field exists and is not empty."""
    return Field(tuple(path), presence_only=True)


class ConfigFormatError(AccessError):
    """The file is not well-formed. Carries a position, never any content."""


class ConfigTooLarge(AccessError):
    """The file is larger than a config file has any reason to be."""


def _checked(fields: Iterable[Field]) -> dict[FieldPath, Field]:
    wanted: dict[FieldPath, Field] = {}
    for field in fields:
        if not field.path or not all(isinstance(part, str) and part for part in field.path):
            raise ValueError("a field path needs at least one non-empty key")
        if not field.presence_only and any(is_sensitive_name(part) for part in field.path):
            raise ValueError(
                f"{'.'.join(field.path)} is named like a secret: only its presence may be read"
            )
        if field.path in wanted:
            raise ValueError(f"{'.'.join(field.path)} is listed twice")
        wanted[field.path] = field
    for path in wanted:
        for length in range(1, len(path)):
            if path[:length] in wanted:
                raise ValueError(f"{'.'.join(path[:length])} is both a field and a parent")
    return wanted


def _read_text(access: ReadOnlyAccess, path: str | os.PathLike[str], limit: int) -> str | None:
    with access.open(path) as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise ConfigTooLarge(f"larger than {limit} bytes: {os.fspath(path)}")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _is_scalar(found: Any) -> bool:
    return found is None or isinstance(found, str | bool | int | float)


def _clean(found: Scalar) -> Scalar:
    """An allowlisted value, made safe to store."""
    if isinstance(found, str):
        return redact_text(found[:MAX_VALUE_LENGTH])
    return found


# -- JSON ----------------------------------------------------------------------


class _Malformed(Exception):
    def __init__(self, position: int) -> None:
        self.position = position


_WHITESPACE = re.compile(r"[ \t\n\r]*")
_STRING_STOP = re.compile(r'["\\]')
_STRUCTURE = re.compile(r'["{}\[\]]')
_LITERAL = re.compile(r"[^\s,\]}]+")
_CLOSER = {"{": "}", "[": "]"}
_DECODER = json.JSONDecoder()


def _skip_whitespace(text: str, index: int) -> int:
    return _WHITESPACE.match(text, index).end()


def _skip_string(text: str, index: int) -> int:
    """``index`` is at the opening quote. Return the index after the closing one."""
    index += 1
    while True:
        stop = _STRING_STOP.search(text, index)
        if stop is None:
            raise _Malformed(len(text))
        if stop.group() == '"':
            return stop.end()
        index = stop.end() + 1  # a backslash: the character after it is not a quote


def _skip_value(text: str, index: int) -> int:
    """Return the index after the value at ``index`` without decoding any of it."""
    if index >= len(text):
        raise _Malformed(index)
    first = text[index]
    if first == '"':
        return _skip_string(text, index)
    if first not in _CLOSER:
        literal = _LITERAL.match(text, index)
        if literal is None:
            raise _Malformed(index)
        return literal.end()
    expected = [_CLOSER[first]]
    index += 1
    while expected:
        mark = _STRUCTURE.search(text, index)
        if mark is None:
            raise _Malformed(len(text))
        char = mark.group()
        if char == '"':
            index = _skip_string(text, mark.start())
            continue
        if char in _CLOSER:
            expected.append(_CLOSER[char])
        elif char != expected.pop():
            raise _Malformed(mark.start())
        index = mark.end()
    return index


def _is_empty(text: str, index: int) -> bool:
    """Whether the value at ``index`` is null, an empty string or an empty container."""
    if text.startswith("null", index) or text.startswith('""', index):
        return True
    if text[index] in _CLOSER:
        inner = _skip_whitespace(text, index + 1)
        return text[inner : inner + 1] == _CLOSER[text[index]]
    return False


def _decode(text: str, index: int) -> tuple[Any, int]:
    try:
        return _DECODER.raw_decode(text, index)
    except json.JSONDecodeError as error:
        position = error.pos
    # Raised outside the handler: the decoder's error holds the whole document.
    raise _Malformed(position)


def _leads_to_a_field(path: FieldPath, wanted: dict[FieldPath, Field]) -> bool:
    return any(other[: len(path)] == path for other in wanted)


def _scan_object(
    text: str,
    index: int,
    prefix: FieldPath,
    wanted: dict[FieldPath, Field],
    found: dict[FieldPath, Any],
) -> int:
    """``index`` is at ``{``. Collect wanted members; return the index after ``}``."""
    index = _skip_whitespace(text, index + 1)
    if text[index : index + 1] == "}":
        return index + 1
    while True:
        if text[index : index + 1] != '"':
            raise _Malformed(index)
        key, index = _decode(text, index)
        index = _skip_whitespace(text, index)
        if text[index : index + 1] != ":":
            raise _Malformed(index)
        index = _skip_whitespace(text, index + 1)
        if index >= len(text):
            raise _Malformed(index)

        path = (*prefix, key)
        field = wanted.get(path)
        if field is not None and field.presence_only:
            found[path] = not _is_empty(text, index)
            index = _skip_value(text, index)
        elif field is not None and text[index] not in _CLOSER:
            found[path], index = _decode(text, index)
        elif field is None and text[index] == "{" and _leads_to_a_field(path, wanted):
            index = _scan_object(text, index, path, wanted, found)
        else:
            index = _skip_value(text, index)

        index = _skip_whitespace(text, index)
        if text[index : index + 1] == "}":
            return index + 1
        if text[index : index + 1] != ",":
            raise _Malformed(index)
        index = _skip_whitespace(text, index + 1)


def _project_json(
    text: str, wanted: dict[FieldPath, Field]
) -> tuple[dict[FieldPath, Any], int | None]:
    """Never raises: returns what was found, and where the text went wrong if it did."""
    found: dict[FieldPath, Any] = {}
    try:
        index = _skip_whitespace(text, 0)
        if text[index : index + 1] != "{":
            raise _Malformed(index)
        index = _skip_whitespace(text, _scan_object(text, index, (), wanted, found))
        if index != len(text):
            raise _Malformed(index)
    except _Malformed as error:
        return {}, error.position
    return found, None


def _finish(wanted: dict[FieldPath, Field], found: dict[FieldPath, Any]) -> dict[FieldPath, Scalar]:
    result: dict[FieldPath, Scalar] = {}
    for path, field in wanted.items():
        if field.presence_only:
            result[path] = bool(found.get(path, False))
        elif path in found and _is_scalar(found[path]):
            result[path] = _clean(found[path])
    return result


def read_json_fields(
    access: ReadOnlyAccess,
    path: str | os.PathLike[str],
    fields: Iterable[Field],
    *,
    max_bytes: int = DEFAULT_MAX_CONFIG_BYTES,
) -> dict[FieldPath, Scalar]:
    """Read the listed fields of a JSON object file.

    The result maps each field's path to its value. A :func:`value` field that
    is missing, or is not a scalar, is left out. A :func:`presence` field is
    always there, as ``True`` or ``False``. String values are redacted and cut
    to a fixed length.
    """
    wanted = _checked(fields)
    text = _read_text(access, path, max_bytes)
    if text is None:
        raise ConfigFormatError(f"not UTF-8 text: {os.fspath(path)}")
    try:
        found, problem = _project_json(text, wanted)
    finally:
        # Dropped before anything is raised, so no traceback frame holds the file.
        del text
    if problem is not None:
        raise ConfigFormatError(f"not well-formed JSON at character {problem}: {os.fspath(path)}")
    return _finish(wanted, found)


# -- TOML ----------------------------------------------------------------------


def _project_toml(text: str, wanted: dict[FieldPath, Field]) -> tuple[dict[FieldPath, Any], bool]:
    """Never raises: returns what was found, and whether the text parsed."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}, False
    found: dict[FieldPath, Any] = {}
    for path, field in wanted.items():
        node: Any = document
        for part in path:
            if not isinstance(node, dict) or part not in node:
                break
            node = node[part]
        else:
            if field.presence_only:
                found[path] = node not in (None, "", {}, [])
            elif _is_scalar(node):
                found[path] = node
    return found, True


def read_toml_fields(
    access: ReadOnlyAccess,
    path: str | os.PathLike[str],
    fields: Iterable[Field],
    *,
    max_bytes: int = DEFAULT_MAX_CONFIG_BYTES,
) -> dict[FieldPath, Scalar]:
    """Read the listed fields of a TOML file. Same result as :func:`read_json_fields`.

    The whole file is parsed and the parse dropped before returning; see the
    module notes. Not for files whose purpose is to hold credentials.
    """
    wanted = _checked(fields)
    text = _read_text(access, path, max_bytes)
    if text is None:
        raise ConfigFormatError(f"not UTF-8 text: {os.fspath(path)}")
    try:
        found, parsed = _project_toml(text, wanted)
    finally:
        del text
    if not parsed:
        raise ConfigFormatError(f"not well-formed TOML: {os.fspath(path)}")
    return _finish(wanted, found)
