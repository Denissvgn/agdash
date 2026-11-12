"""The event contract: what an adapter may hand to the store.

The JSON Schema in ``contracts/event.schema.json`` is the source of truth.
"""

import json
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

SCHEMA_RESOURCE = "contracts/event.schema.json"


class EventContractError(ValueError):
    """An event does not follow the contract.

    The message names where the event is wrong and which rule it broke. It
    never quotes the event, because events hold private transcript content and
    this message may be logged.
    """

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("event violates the contract: " + "; ".join(violations))


@lru_cache
def event_schema() -> dict[str, Any]:
    text = resources.files("agdash").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")
    return json.loads(text)


@lru_cache
def _validator() -> Draft202012Validator:
    schema = event_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _location(error: ValidationError) -> str:
    return "/".join(str(part) for part in error.absolute_path) or "(event)"


def _describe(error: ValidationError) -> list[str]:
    # A failed "if/then" or "anyOf" only says that a branch failed; the rule
    # that was actually broken is in its context.
    if error.context:
        return [line for inner in error.context for line in _describe(inner)]
    return [f"{_location(error)}: fails '{error.validator}'"]


def validate_event(
    kind: str,
    payload: dict[str, Any],
    thoughts_coverage: str | None = None,
) -> None:
    """Raise :class:`EventContractError` unless the event follows the contract."""
    event: dict[str, Any] = {"kind": str(kind), "payload": payload}
    if thoughts_coverage is not None:
        event["thoughts_coverage"] = str(thoughts_coverage)
    violations = sorted(
        {line for error in _validator().iter_errors(event) for line in _describe(error)}
    )
    if violations:
        raise EventContractError(violations)
