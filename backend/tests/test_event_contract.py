import pytest
from jsonschema import Draft202012Validator

from agdash import events
from agdash.events import EventContractError, event_schema, validate_event
from agdash.vocab import EventKind, ThoughtsCoverage, UsageScope

# One well-formed event per kind: (payload, thoughts_coverage).
EXAMPLES: dict[EventKind, tuple[dict, str | None]] = {
    EventKind.USER_PROMPT: ({"text": "Rename the helper."}, None),
    EventKind.ASSISTANT_TEXT: ({"text": "Renamed it.", "model": "example-model-1"}, None),
    EventKind.THINKING: ({"text": "The helper has two callers."}, "full"),
    EventKind.TOOL_CALL: (
        {"name": "read_file", "call_id": "call-1", "input": {"path": "src/example.py"}},
        None,
    ),
    EventKind.TOOL_RESULT: ({"call_id": "call-1", "output": "ok", "is_error": False}, None),
    EventKind.SUBAGENT: ({"phase": "started", "native_id": "sub-1", "prompt": "Survey."}, None),
    EventKind.MODEL_CHANGE: (
        {"model": "example-model-2", "previous_model": "example-model-1"},
        None,
    ),
    EventKind.COMPACTION: ({"trigger": "auto", "tokens_before": 9000, "tokens_after": 1200}, None),
    EventKind.STATUS: ({"status": "turn_started"}, None),
    EventKind.USAGE: (
        {"scope": "message", "input_tokens": 120, "output_tokens": 40, "reported_cost": 0.0021},
        None,
    ),
}


def test_schema_is_a_valid_json_schema():
    Draft202012Validator.check_schema(event_schema())


def test_schema_and_code_agree_on_the_vocabularies():
    schema = event_schema()
    assert schema["properties"]["kind"]["enum"] == [kind.value for kind in EventKind]
    assert set(schema["$defs"]) >= {kind.value for kind in EventKind}
    coverage = schema["properties"]["thoughts_coverage"]["enum"]
    assert [level for level in coverage if level is not None] == [c.value for c in ThoughtsCoverage]
    scopes = schema["$defs"]["usage"]["properties"]["scope"]["enum"]
    assert scopes == [scope.value for scope in UsageScope]


def test_every_kind_has_an_example():
    assert set(EXAMPLES) == set(EventKind)


@pytest.mark.parametrize("kind", list(EventKind))
def test_every_kind_validates(kind):
    payload, coverage = EXAMPLES[kind]
    validate_event(kind, payload, coverage)
    validate_event(kind.value, payload, coverage)


@pytest.mark.parametrize("kind", ["note", "", "USER_PROMPT", "user-prompt", "None"])
def test_unknown_kinds_are_rejected(kind):
    with pytest.raises(EventContractError) as raised:
        validate_event(kind, {"text": "x"})
    assert "kind: fails 'enum'" in raised.value.violations


@pytest.mark.parametrize("kind", list(EventKind))
def test_unknown_payload_fields_are_rejected(kind):
    payload, coverage = EXAMPLES[kind]
    with pytest.raises(EventContractError) as raised:
        validate_event(kind, {**payload, "unexpected": 1}, coverage)
    assert raised.value.violations == ["payload: fails 'additionalProperties'"]


@pytest.mark.parametrize("kind", list(EventKind))
def test_harness_specific_fields_go_under_extra(kind):
    payload, coverage = EXAMPLES[kind]
    validate_event(kind, {**payload, "extra": {"anything": [1, "two", None]}}, coverage)


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        (EventKind.USER_PROMPT, {}),
        (EventKind.ASSISTANT_TEXT, {"model": "example-model-1"}),
        (EventKind.TOOL_CALL, {"input": {}}),
        (EventKind.TOOL_CALL, {"name": ""}),
        (EventKind.TOOL_RESULT, {}),
        (EventKind.SUBAGENT, {"native_id": "sub-1"}),
        (EventKind.SUBAGENT, {"phase": "paused"}),
        (EventKind.MODEL_CHANGE, {"previous_model": "example-model-1"}),
        (EventKind.COMPACTION, {"tokens_before": -1}),
        (EventKind.COMPACTION, {"trigger": "scheduled"}),
        (EventKind.STATUS, {"detail": "no status"}),
        (EventKind.USAGE, {"scope": "message"}),
        (EventKind.USAGE, {"input_tokens": 1}),
        (EventKind.USAGE, {"scope": "turn", "input_tokens": 1}),
        (EventKind.USAGE, {"scope": "message", "input_tokens": 1.5}),
        (EventKind.USAGE, {"scope": "message", "input_tokens": -1}),
        (EventKind.USAGE, {"scope": "message", "input_tokens": 1, "cost_currency": "USD"}),
        (EventKind.USAGE, {"scope": "message", "reported_cost": 1, "cost_currency": "usd"}),
    ],
)
def test_malformed_payloads_are_rejected(kind, payload):
    with pytest.raises(EventContractError):
        validate_event(kind, payload)


def test_a_payload_must_be_an_object():
    with pytest.raises(EventContractError):
        validate_event(EventKind.USER_PROMPT, ["text"])  # type: ignore[arg-type]


@pytest.mark.parametrize("coverage", ["full", "summary"])
def test_readable_thinking_carries_text(coverage):
    validate_event(EventKind.THINKING, {"text": "Weighing two options."}, coverage)
    with pytest.raises(EventContractError):
        validate_event(EventKind.THINKING, {}, coverage)


@pytest.mark.parametrize("coverage", ["encrypted", "absent"])
def test_unreadable_thinking_carries_no_text(coverage):
    validate_event(EventKind.THINKING, {}, coverage)
    validate_event(EventKind.THINKING, {"extra": {"reasoning_tokens": 512}}, coverage)
    with pytest.raises(EventContractError):
        validate_event(EventKind.THINKING, {"text": "opaque"}, coverage)


def test_thinking_must_state_its_coverage():
    with pytest.raises(EventContractError):
        validate_event(EventKind.THINKING, {"text": "Weighing two options."})
    with pytest.raises(EventContractError):
        validate_event(EventKind.THINKING, {"text": "Weighing two options."}, "partial")


@pytest.mark.parametrize("kind", [kind for kind in EventKind if kind is not EventKind.THINKING])
def test_only_thinking_states_a_coverage(kind):
    payload, _ = EXAMPLES[kind]
    with pytest.raises(EventContractError):
        validate_event(kind, payload, "full")


def test_errors_never_quote_the_event():
    marker = "PRIVATE-TRANSCRIPT-MARKER"
    cases = [
        (EventKind.USER_PROMPT, {"text": 7, "note": marker}, None),
        (EventKind.TOOL_CALL, {"name": 7, "input": marker, marker: marker}, None),
        (EventKind.USAGE, {"scope": marker}, None),
        (EventKind.THINKING, {"text": marker}, "encrypted"),
        (marker, {"text": marker}, marker),
    ]
    for kind, payload, coverage in cases:
        with pytest.raises(EventContractError) as raised:
            validate_event(kind, payload, coverage)
        assert marker not in str(raised.value)
        assert marker not in repr(raised.value.violations)


def _violations(validator, kind, payload, coverage):
    event = {"kind": kind, "payload": payload}
    if coverage is not None:
        event["thoughts_coverage"] = coverage
    return sorted(
        {line for error in validator.iter_errors(event) for line in events._describe(error)}
    )


@pytest.mark.parametrize("kind", list(EXAMPLES))
def test_the_per_kind_rules_judge_exactly_as_the_whole_schema(kind):
    payload, coverage = EXAMPLES[kind]
    broken = [
        (payload, coverage),
        (payload, "full"),
        (payload, "encrypted"),
        ({**payload, "unexpected": 1}, coverage),
        ({key: 7 for key in payload}, coverage),
        ({}, coverage),
        ("not an object", coverage),
    ]
    for case_payload, case_coverage in broken:
        assert _violations(
            events._validator_for(kind), kind, case_payload, case_coverage
        ) == _violations(events._validator(), kind, case_payload, case_coverage)
