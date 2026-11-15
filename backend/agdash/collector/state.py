"""Derive a session's state from what was observed, and say what it rests on.

Five states:

* ``active`` — the agent is working now;
* ``waiting`` — the agent finished its turn or asked for permission, and the
  person has not answered yet;
* ``idle`` — the session is open but nothing has happened for a while;
* ``ended`` — there is positive proof the session is over;
* ``unknown`` — there is no evidence either way.

A session is only ever ``ended`` on positive proof: a process that was seen
running and then seen exiting, the harness saying so, an end record, or the
session being archived. The absence of a process is not proof, because a
session may run where agdash cannot see it. With no evidence, the state is
``unknown``.

Pure functions only: no file, clock or process is read here. The probes and
adapters gather the observation; this module judges it.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from agdash.vocab import EventKind, SessionState


class ProcessLiveness(StrEnum):
    RUNNING = "running"  # a process of this session runs now
    EXITED = "exited"  # a process of this session was seen running and is now gone
    HIDDEN = "hidden"  # a process may exist, but this user may not inspect it


class HarnessStatus(StrEnum):
    """What the harness itself says, for example in a session registry file."""

    BUSY = "busy"
    WAITING = "waiting"
    IDLE = "idle"
    CLOSED = "closed"


class TurnPhase(StrEnum):
    """Whose move it is, from the last transcript records."""

    AGENT = "agent"  # a prompt or tool result is waiting for the agent
    PERSON = "person"  # the agent ended its turn


class Basis(StrEnum):
    """Why a state was chosen. Stored with every state."""

    PROCESS_EXITED = "process_exited"
    HARNESS_CLOSED = "harness_closed"
    END_RECORD = "end_record"
    ARCHIVED = "archived"
    HARNESS_BUSY = "harness_busy"
    HARNESS_WAITING = "harness_waiting"
    HARNESS_IDLE = "harness_idle"
    MID_TURN = "mid_turn"
    TURN_ENDED = "turn_ended"
    QUIET = "quiet"
    PROCESS_RUNNING = "process_running"
    RECENT_WRITES = "recent_writes"
    PROCESS_HIDDEN = "process_hidden"
    NO_EVIDENCE = "no_evidence"


@dataclass(frozen=True)
class Thresholds:
    """How long things take to mean something."""

    # Writes this recent prove a writer is there, with or without a process.
    recent_writes: timedelta = timedelta(seconds=60)
    # A harness status older than this is not trusted without a live process.
    fresh_status: timedelta = timedelta(seconds=60)
    # An open session quiet for this long is idle rather than working or waiting.
    idle_after: timedelta = timedelta(minutes=10)


DEFAULT_THRESHOLDS = Thresholds()


@dataclass(frozen=True)
class Observation:
    """Everything known about one session at one moment. All instants are UTC."""

    now: datetime
    process: ProcessLiveness | None = None
    harness_status: HarnessStatus | None = None
    harness_status_at: datetime | None = None
    last_activity_at: datetime | None = None
    turn: TurnPhase | None = None
    end_record_at: datetime | None = None
    archived: bool = False

    def __post_init__(self) -> None:
        for name in ("now", "harness_status_at", "last_activity_at", "end_record_at"):
            value = getattr(self, name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must carry a UTC offset")
        if self.harness_status is not None and self.harness_status_at is None:
            raise ValueError("a harness status needs the time it was observed")


@dataclass(frozen=True)
class StateRecord:
    """A derived state, since when it holds, and what it rests on."""

    state: SessionState
    basis: Basis
    since: datetime
    observed_at: datetime
    previous: SessionState | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


def turn_phase(kind: EventKind | str, *, ends_turn: bool = False) -> TurnPhase | None:
    """Whose move it is after the last record of a transcript.

    Assistant text may come in the middle of a turn, so it only hands the move
    to the person when the adapter knows the turn ended there.
    """
    kind = EventKind(kind)
    if kind in (EventKind.USER_PROMPT, EventKind.TOOL_CALL, EventKind.TOOL_RESULT):
        return TurnPhase.AGENT
    if kind in (EventKind.THINKING, EventKind.SUBAGENT, EventKind.COMPACTION):
        return TurnPhase.AGENT
    if kind is EventKind.ASSISTANT_TEXT:
        return TurnPhase.PERSON if ends_turn else None
    return None


def _within(moment: datetime | None, now: datetime, span: timedelta) -> bool:
    return moment is not None and now - moment <= span


def derive(
    observation: Observation, thresholds: Thresholds = DEFAULT_THRESHOLDS
) -> tuple[SessionState, Basis]:
    """The state an observation supports, and the basis for it."""
    o, t = observation, thresholds
    alive = o.process is ProcessLiveness.RUNNING

    if not alive:
        if o.process is ProcessLiveness.EXITED:
            return SessionState.ENDED, Basis.PROCESS_EXITED
        if o.harness_status is HarnessStatus.CLOSED:
            return SessionState.ENDED, Basis.HARNESS_CLOSED
        if o.end_record_at is not None:
            return SessionState.ENDED, Basis.END_RECORD
        if o.archived:
            return SessionState.ENDED, Basis.ARCHIVED

    status = o.harness_status
    if status is HarnessStatus.CLOSED:
        status = None  # a running process outweighs a stale "closed"
    if status is not None and not alive and not _within(o.harness_status_at, o.now, t.fresh_status):
        status = None  # an old report with no process behind it proves nothing

    if alive or status is not None:
        if status is HarnessStatus.BUSY:
            return SessionState.ACTIVE, Basis.HARNESS_BUSY
        if status is HarnessStatus.WAITING:
            return SessionState.WAITING, Basis.HARNESS_WAITING
        quiet = not _within(o.last_activity_at, o.now, t.idle_after)
        if o.turn is TurnPhase.AGENT:
            return (
                (SessionState.IDLE, Basis.QUIET) if quiet else (SessionState.ACTIVE, Basis.MID_TURN)
            )
        if o.turn is TurnPhase.PERSON or status is HarnessStatus.IDLE:
            if quiet:
                return SessionState.IDLE, Basis.QUIET
            basis = Basis.TURN_ENDED if o.turn is TurnPhase.PERSON else Basis.HARNESS_IDLE
            return SessionState.WAITING, basis
        if _within(o.last_activity_at, o.now, t.recent_writes):
            return SessionState.ACTIVE, Basis.RECENT_WRITES
        return SessionState.IDLE, Basis.PROCESS_RUNNING

    if _within(o.last_activity_at, o.now, t.recent_writes):
        return SessionState.ACTIVE, Basis.RECENT_WRITES
    if o.process is ProcessLiveness.HIDDEN:
        return SessionState.UNKNOWN, Basis.PROCESS_HIDDEN
    return SessionState.UNKNOWN, Basis.NO_EVIDENCE


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _evidence(observation: Observation) -> dict[str, Any]:
    """The inputs the state was judged on, ready to store as JSON."""
    o = observation
    found = {
        "process": o.process.value if o.process else None,
        "harness_status": o.harness_status.value if o.harness_status else None,
        "harness_status_at": _iso(o.harness_status_at),
        "last_activity_at": _iso(o.last_activity_at),
        "turn": o.turn.value if o.turn else None,
        "end_record_at": _iso(o.end_record_at),
        "archived": o.archived or None,
    }
    return {name: value for name, value in found.items() if value is not None}


def advance(
    previous: StateRecord | None,
    observation: Observation,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> StateRecord:
    """The next state record. ``since`` moves only when the state changes.

    An exit is seen once, in the pass that notices it; later passes find no
    process at all. So a session that ended on proof stays ended until there
    is new evidence of life, instead of falling back to unknown.
    """
    state, basis = derive(observation, thresholds)
    if (
        previous is not None
        and previous.state is SessionState.ENDED
        and state is SessionState.UNKNOWN
    ):
        return replace(previous, observed_at=observation.now)
    if previous is not None and previous.state is state:
        return StateRecord(
            state=state,
            basis=basis,
            since=previous.since,
            observed_at=observation.now,
            previous=previous.previous,
            evidence=_evidence(observation),
        )
    return StateRecord(
        state=state,
        basis=basis,
        since=observation.now,
        observed_at=observation.now,
        previous=previous.state if previous else None,
        evidence=_evidence(observation),
    )


def session_state_fields(record: StateRecord) -> dict[str, Any]:
    """The ``sessions`` columns a state record fills."""
    return {
        "state": record.state,
        "state_observed_at": record.observed_at,
        "state_evidence": {
            "basis": record.basis.value,
            "since": record.since.isoformat(),
            "previous": record.previous.value if record.previous else None,
            "observed": dict(record.evidence),
        },
    }
