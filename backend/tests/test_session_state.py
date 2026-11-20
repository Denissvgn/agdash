"""The session state machine, table by table.

Every basis has a row that produces it, every pair of states has a transition
test, and a randomised check confirms that without positive proof nothing is
ever ``ended``.
"""

import itertools
import random
from datetime import UTC, datetime, timedelta

import pytest

from agdash.collector.state import (
    Basis,
    HarnessStatus,
    Observation,
    ProcessLiveness,
    StateRecord,
    Thresholds,
    TurnPhase,
    advance,
    derive,
    session_state_fields,
    turn_phase,
)
from agdash.db.models import Session
from agdash.vocab import EventKind, SessionState

NOW = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)
SECONDS = timedelta(seconds=1)
MINUTES = timedelta(minutes=1)

RUNNING = ProcessLiveness.RUNNING
EXITED = ProcessLiveness.EXITED
HIDDEN = ProcessLiveness.HIDDEN


def obs(**fields) -> Observation:
    fields.setdefault("now", NOW)
    if "harness_status" in fields and "harness_status_at" not in fields:
        fields["harness_status_at"] = fields["now"]
    return Observation(**fields)


# One row per basis: (name, observation, expected state, expected basis).
BASIS_TABLE = [
    (
        "exit seen",
        obs(process=EXITED, last_activity_at=NOW - 2 * SECONDS),
        SessionState.ENDED,
        Basis.PROCESS_EXITED,
    ),
    (
        "harness closed",
        obs(harness_status=HarnessStatus.CLOSED),
        SessionState.ENDED,
        Basis.HARNESS_CLOSED,
    ),
    ("end record", obs(end_record_at=NOW - MINUTES), SessionState.ENDED, Basis.END_RECORD),
    ("archived", obs(archived=True), SessionState.ENDED, Basis.ARCHIVED),
    (
        "harness busy",
        obs(process=RUNNING, harness_status=HarnessStatus.BUSY),
        SessionState.ACTIVE,
        Basis.HARNESS_BUSY,
    ),
    (
        "harness asks for permission",
        obs(process=RUNNING, harness_status=HarnessStatus.WAITING),
        SessionState.WAITING,
        Basis.HARNESS_WAITING,
    ),
    (
        "harness idle, recently",
        obs(process=RUNNING, harness_status=HarnessStatus.IDLE, last_activity_at=NOW - 2 * MINUTES),
        SessionState.WAITING,
        Basis.HARNESS_IDLE,
    ),
    (
        "mid-turn",
        obs(process=RUNNING, turn=TurnPhase.AGENT, last_activity_at=NOW - 3 * MINUTES),
        SessionState.ACTIVE,
        Basis.MID_TURN,
    ),
    (
        "turn ended",
        obs(process=RUNNING, turn=TurnPhase.PERSON, last_activity_at=NOW - 3 * MINUTES),
        SessionState.WAITING,
        Basis.TURN_ENDED,
    ),
    (
        "quiet for long",
        obs(process=RUNNING, turn=TurnPhase.PERSON, last_activity_at=NOW - 30 * MINUTES),
        SessionState.IDLE,
        Basis.QUIET,
    ),
    (
        "process with nothing else known",
        obs(process=RUNNING),
        SessionState.IDLE,
        Basis.PROCESS_RUNNING,
    ),
    (
        "fresh writes, no process seen",
        obs(last_activity_at=NOW - 5 * SECONDS),
        SessionState.ACTIVE,
        Basis.RECENT_WRITES,
    ),
    (
        "process hidden",
        obs(process=HIDDEN, last_activity_at=NOW - 2 * MINUTES),
        SessionState.UNKNOWN,
        Basis.PROCESS_HIDDEN,
    ),
    (
        "old files, nothing else",
        obs(last_activity_at=NOW - timedelta(days=90)),
        SessionState.UNKNOWN,
        Basis.NO_EVIDENCE,
    ),
]


@pytest.mark.parametrize(
    ("observation", "state", "basis"),
    [row[1:] for row in BASIS_TABLE],
    ids=[row[0] for row in BASIS_TABLE],
)
def test_each_basis_leads_to_its_state(observation, state, basis):
    assert derive(observation) == (state, basis)


def test_every_basis_has_a_row():
    assert {row[3] for row in BASIS_TABLE} == set(Basis)
    assert {row[2] for row in BASIS_TABLE} == set(SessionState)


# -- precedence ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("observation", "state", "basis"),
    [
        # A running process outweighs every kind of end evidence: the session was resumed.
        (
            obs(process=RUNNING, archived=True, turn=TurnPhase.AGENT, last_activity_at=NOW),
            SessionState.ACTIVE,
            Basis.MID_TURN,
        ),
        (
            obs(process=RUNNING, end_record_at=NOW - MINUTES),
            SessionState.IDLE,
            Basis.PROCESS_RUNNING,
        ),
        (
            obs(process=RUNNING, harness_status=HarnessStatus.CLOSED),
            SessionState.IDLE,
            Basis.PROCESS_RUNNING,
        ),
        # The harness's own status outweighs what the transcript suggests.
        (
            obs(process=RUNNING, harness_status=HarnessStatus.BUSY, turn=TurnPhase.PERSON),
            SessionState.ACTIVE,
            Basis.HARNESS_BUSY,
        ),
        (
            obs(process=RUNNING, harness_status=HarnessStatus.WAITING, turn=TurnPhase.AGENT),
            SessionState.WAITING,
            Basis.HARNESS_WAITING,
        ),
        # An exit outweighs writes that happened just before it.
        (obs(process=EXITED, last_activity_at=NOW), SessionState.ENDED, Basis.PROCESS_EXITED),
        # A fresh status counts without a process, as for a desktop app's database.
        (
            obs(harness_status=HarnessStatus.BUSY, harness_status_at=NOW - 10 * SECONDS),
            SessionState.ACTIVE,
            Basis.HARNESS_BUSY,
        ),
        # An old one does not, and leaves the state unknown rather than ended.
        (
            obs(harness_status=HarnessStatus.BUSY, harness_status_at=NOW - 10 * MINUTES),
            SessionState.UNKNOWN,
            Basis.NO_EVIDENCE,
        ),
        (
            obs(harness_status=HarnessStatus.IDLE, harness_status_at=NOW - 10 * MINUTES),
            SessionState.UNKNOWN,
            Basis.NO_EVIDENCE,
        ),
        # Mid-turn but silent beyond the idle threshold.
        (
            obs(process=RUNNING, turn=TurnPhase.AGENT, last_activity_at=NOW - 11 * MINUTES),
            SessionState.IDLE,
            Basis.QUIET,
        ),
        # A running process with fresh writes and no turn information.
        (
            obs(process=RUNNING, last_activity_at=NOW - 30 * SECONDS),
            SessionState.ACTIVE,
            Basis.RECENT_WRITES,
        ),
        # Hidden with fresh writes: the writes prove a writer.
        (
            obs(process=HIDDEN, last_activity_at=NOW - 5 * SECONDS),
            SessionState.ACTIVE,
            Basis.RECENT_WRITES,
        ),
    ],
)
def test_precedence(observation, state, basis):
    assert derive(observation) == (state, basis)


def test_thresholds_can_be_tuned():
    slow = Thresholds(idle_after=timedelta(hours=1))
    quiet_half_hour = obs(
        process=RUNNING, turn=TurnPhase.PERSON, last_activity_at=NOW - 30 * MINUTES
    )
    assert derive(quiet_half_hour) == (SessionState.IDLE, Basis.QUIET)
    assert derive(quiet_half_hour, slow) == (SessionState.WAITING, Basis.TURN_ENDED)


# -- no evidence never means ended ----------------------------------------------------


def test_no_liveness_evidence_is_unknown_never_ended():
    rng = random.Random(9)
    for _ in range(3000):
        observation = obs(
            process=rng.choice([None, HIDDEN]),
            harness_status=rng.choice(
                [None, HarnessStatus.BUSY, HarnessStatus.IDLE, HarnessStatus.WAITING]
            ),
            harness_status_at=NOW - timedelta(seconds=rng.randint(61, 10**6)),
            last_activity_at=rng.choice([None, NOW - timedelta(seconds=rng.randint(61, 10**8))]),
            turn=rng.choice([None, *TurnPhase]),
        )
        assert derive(observation)[0] is SessionState.UNKNOWN


def test_only_positive_proof_ends_a_session():
    rng = random.Random(11)
    for _ in range(3000):
        observation = obs(
            process=rng.choice([None, RUNNING, EXITED, HIDDEN]),
            harness_status=rng.choice([None, *HarnessStatus]),
            harness_status_at=NOW - timedelta(seconds=rng.randint(0, 10**5)),
            last_activity_at=rng.choice([None, NOW - timedelta(seconds=rng.randint(0, 10**6))]),
            turn=rng.choice([None, *TurnPhase]),
            end_record_at=rng.choice([None, NOW - MINUTES]),
            archived=rng.random() < 0.2,
        )
        state, basis = derive(observation)
        if state is SessionState.ENDED:
            assert observation.process is not RUNNING
            assert basis in {
                Basis.PROCESS_EXITED,
                Basis.HARNESS_CLOSED,
                Basis.END_RECORD,
                Basis.ARCHIVED,
            }
            proof = {
                Basis.PROCESS_EXITED: observation.process is EXITED,
                Basis.HARNESS_CLOSED: observation.harness_status is HarnessStatus.CLOSED,
                Basis.END_RECORD: observation.end_record_at is not None,
                Basis.ARCHIVED: observation.archived,
            }
            assert proof[basis]


# -- transitions ----------------------------------------------------------------------

# An observation that produces each state.
LEADS_TO = {
    SessionState.ACTIVE: lambda now: obs(
        now=now, process=RUNNING, harness_status=HarnessStatus.BUSY
    ),
    SessionState.WAITING: lambda now: obs(
        now=now, process=RUNNING, turn=TurnPhase.PERSON, last_activity_at=now - MINUTES
    ),
    SessionState.IDLE: lambda now: obs(
        now=now, process=RUNNING, turn=TurnPhase.PERSON, last_activity_at=now - timedelta(hours=1)
    ),
    SessionState.ENDED: lambda now: obs(now=now, process=EXITED),
    SessionState.UNKNOWN: lambda now: obs(now=now),
}
EXPECTED_BASIS = {
    SessionState.ACTIVE: Basis.HARNESS_BUSY,
    SessionState.WAITING: Basis.TURN_ENDED,
    SessionState.IDLE: Basis.QUIET,
    SessionState.ENDED: Basis.PROCESS_EXITED,
    SessionState.UNKNOWN: Basis.NO_EVIDENCE,
}


@pytest.mark.parametrize(
    ("before", "after"),
    list(itertools.product(SessionState, SessionState)),
    ids=[f"{a.value}->{b.value}" for a, b in itertools.product(SessionState, SessionState)],
)
def test_every_transition_records_its_basis(before, after):
    first = advance(None, LEADS_TO[before](NOW))
    assert (first.state, first.basis, first.since, first.previous) == (
        before,
        EXPECTED_BASIS[before],
        NOW,
        None,
    )

    later = NOW + 5 * SECONDS
    second = advance(first, LEADS_TO[after](later))
    assert second.observed_at == later
    if (before, after) == (SessionState.ENDED, SessionState.UNKNOWN):
        # Proof of an end outlasts later passes that see nothing.
        assert (second.state, second.basis, second.since) == (before, Basis.PROCESS_EXITED, NOW)
        return
    assert second.state is after
    assert second.basis is EXPECTED_BASIS[after]
    if before is after:
        assert second.since == NOW, "a state that holds keeps the moment it began"
        assert second.previous is None
    else:
        assert second.since == later
        assert second.previous is before
    if after is not SessionState.UNKNOWN:
        assert second.evidence, "the inputs are stored with the state"


def test_records_read_after_an_exit_do_not_bring_the_session_back():
    exited = Observation(
        now=NOW, process=ProcessLiveness.EXITED, last_activity_at=NOW - 2 * SECONDS
    )
    ended = advance(None, exited)
    assert (ended.state, ended.basis) == (SessionState.ENDED, Basis.PROCESS_EXITED)
    # Its last lines are read once the exit has been seen; they were written before it.
    later = NOW + 2 * SECONDS
    still = advance(ended, Observation(now=later, last_activity_at=NOW - SECONDS))
    assert (still.state, still.basis, still.since) == (
        SessionState.ENDED,
        Basis.PROCESS_EXITED,
        NOW,
    )
    assert still.observed_at == later
    # Something written after the end is new evidence of life.
    again = advance(ended, Observation(now=later, last_activity_at=NOW + SECONDS))
    assert (again.state, again.basis) == (SessionState.ACTIVE, Basis.RECENT_WRITES)
    assert again.previous is SessionState.ENDED


def test_a_state_that_holds_keeps_its_history():
    first = advance(None, LEADS_TO[SessionState.WAITING](NOW))
    active = advance(first, LEADS_TO[SessionState.ACTIVE](NOW + MINUTES))
    still = advance(active, LEADS_TO[SessionState.ACTIVE](NOW + 2 * MINUTES))
    assert (still.since, still.previous) == (NOW + MINUTES, SessionState.WAITING)


def test_the_evidence_is_the_observation_in_json_form():
    record = advance(
        None,
        obs(
            process=RUNNING,
            harness_status=HarnessStatus.IDLE,
            harness_status_at=NOW - 3 * SECONDS,
            last_activity_at=NOW - MINUTES,
            turn=TurnPhase.PERSON,
        ),
    )
    assert record.evidence == {
        "process": "running",
        "harness_status": "idle",
        "harness_status_at": (NOW - 3 * SECONDS).isoformat(),
        "last_activity_at": (NOW - MINUTES).isoformat(),
        "turn": "person",
    }


def test_the_session_columns_are_filled():
    record = advance(
        advance(None, LEADS_TO[SessionState.ACTIVE](NOW)),
        LEADS_TO[SessionState.ENDED](NOW + MINUTES),
    )
    fields = session_state_fields(record)
    assert set(fields) <= {column.name for column in Session.__table__.columns}
    assert fields["state"] is SessionState.ENDED
    assert fields["state_observed_at"] == NOW + MINUTES
    assert fields["state_evidence"] == {
        "basis": "process_exited",
        "since": (NOW + MINUTES).isoformat(),
        "previous": "active",
        "observed": {"process": "exited"},
    }


# -- inputs ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "ends_turn", "phase"),
    [
        (EventKind.USER_PROMPT, False, TurnPhase.AGENT),
        (EventKind.TOOL_CALL, False, TurnPhase.AGENT),
        (EventKind.TOOL_RESULT, False, TurnPhase.AGENT),
        (EventKind.THINKING, False, TurnPhase.AGENT),
        (EventKind.SUBAGENT, False, TurnPhase.AGENT),
        (EventKind.COMPACTION, False, TurnPhase.AGENT),
        (EventKind.ASSISTANT_TEXT, True, TurnPhase.PERSON),
        (EventKind.ASSISTANT_TEXT, False, None),
        (EventKind.MODEL_CHANGE, False, None),
        (EventKind.STATUS, False, None),
        (EventKind.USAGE, False, None),
        ("user_prompt", False, TurnPhase.AGENT),
    ],
)
def test_turn_phase_from_the_last_record(kind, ends_turn, phase):
    assert turn_phase(kind, ends_turn=ends_turn) is phase


def test_unknown_record_kinds_are_refused():
    with pytest.raises(ValueError):
        turn_phase("note")


@pytest.mark.parametrize(
    "fields",
    [
        {"now": datetime(2026, 1, 2, 12, 0)},
        {"now": NOW, "last_activity_at": datetime(2026, 1, 2, 11, 0)},
        {"now": NOW, "harness_status": HarnessStatus.BUSY, "harness_status_at": None},
    ],
)
def test_malformed_observations_are_refused(fields):
    with pytest.raises(ValueError):
        Observation(**fields)


def test_records_are_immutable():
    record = advance(None, LEADS_TO[SessionState.ACTIVE](NOW))
    with pytest.raises(AttributeError):
        record.state = SessionState.ENDED  # type: ignore[misc]
    assert isinstance(record, StateRecord)


def test_an_ended_session_stays_ended_until_there_is_new_evidence_of_life():
    ended = advance(None, obs(process=EXITED))
    quiet = advance(ended, obs(now=NOW + MINUTES))
    assert (quiet.state, quiet.basis, quiet.since, quiet.observed_at) == (
        SessionState.ENDED,
        Basis.PROCESS_EXITED,
        NOW,
        NOW + MINUTES,
    )
    hidden = advance(quiet, obs(now=NOW + 2 * MINUTES, process=HIDDEN))
    assert hidden.state is SessionState.ENDED
    resumed = advance(
        hidden, obs(now=NOW + 3 * MINUTES, process=RUNNING, harness_status=HarnessStatus.BUSY)
    )
    assert (resumed.state, resumed.previous, resumed.since) == (
        SessionState.ACTIVE,
        SessionState.ENDED,
        NOW + 3 * MINUTES,
    )
