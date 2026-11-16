"""Keep a local copy of what agdash reads from WorkChord, refreshed by polling.

Each pass reads, per connection, six kinds of object: projects, tasks (with
their pipeline stage), the pipeline, assignments, actors and runs. Every kind
is synced on its own, inside its own savepoint, and leaves a row in
``wc_sync_objects`` saying how it went:

* **ok** — the copy now matches the server.
* **unavailable** — the stored credentials may not read it (HTTP 401 or 403,
  or it needs the agent key agdash does not hold). The other kinds still sync.
* **unparseable** — the server answered in a shape agdash does not know. The
  copy is left exactly as it was, so a changed contract never fills it with a
  guess, and the row names where the shape broke (never the values).
* **error** — the server could not be reached or failed; the next pass retries.

With the agent key, the mirror refreshes the runs agdash already knows of: those
a session links to, and those not yet ended. Without it, a server that lets
people read runs (it lists ``person-run-reads-v1`` among its features) gives the
newest runs from its run list instead. Either way each run keeps the harness
session its actor declared, which links that session exactly. Actors come from
the roster when the agent key may read it, and are otherwise learned from task
claims and assignments, which name them.

Only GET requests leave this module (the client refuses anything else). A
server that cannot be reached is retried with exponential backoff. After each
pass, sessions are linked to runs and tasks (``agdash.connectors.links``),
then mapped to actors and projects (``agdash.connectors.resolver``). Where the
person turned publish mode on for a server, ``agdash.connectors.publish`` then
adds the one kind of link agdash ever writes.
"""

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.connectors.links import Linker
from agdash.connectors.publish import Publisher
from agdash.connectors.resolver import Resolver
from agdash.connectors.workchord import KIND, Session, WorkChordClient, open_client
from agdash.db.models import (
    Connection,
    RunLink,
    WcActor,
    WcAssignment,
    WcProject,
    WcRun,
    WcSyncObject,
    WcTask,
)
from agdash.platform.secrets import SecretStore
from agdash.redaction import redact_text, redact_value
from agdash.vocab import MirrorObject, MirrorState, SyncState

log = logging.getLogger("agdash.mirror")

INTERVAL_SECONDS = 60.0
MAX_BACKOFF_SECONDS = 900.0
TICK_SECONDS = 5.0
RUNS_PER_PASS = 50
RUN_LIST_LIMIT = 200
PERSON_RUN_READS = "person-run-reads-v1"
TOPOLOGY_READ = "team-topology-read-v1"
ASSIGNMENT_LIMIT = 500
USAGE_FIELDS = (
    "source",
    "provenance",
    "reporting_mode",
    "coverage",
    "interval_start",
    "interval_end",
    "quantities",
    "reported_cost",
    "currency",
    "pricing_basis",
)


# -- the shapes agdash relies on ------------------------------------------------------
#
# Only the fields agdash stores are declared; anything else the server adds is
# ignored. A declared field that is missing or of another type makes the whole
# kind unparseable.


class _In(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Initiative(_In):
    name: str


class ProjectIn(_In):
    id: int
    name: str
    status: str | None = None
    health: str | None = None
    initiative: _Initiative | None = None


class _Named(_In):
    id: int
    name: str
    display_name: str | None = None


class TaskIn(_In):
    id: int
    title: str
    status: str
    project_id: int | None = None
    iteration_id: int | None = None
    external_key: str | None = None
    claimed_by: _Named | None = None
    claim_expires_at: datetime | None = None
    children: list["TaskIn"] = []


class IterationIn(_In):
    id: int
    name: str


class _ActorSummary(_Named):
    role: str | None = None


class _ModelSummary(_In):
    configured_model_alias: str


class AssignmentIn(_In):
    id: int
    task_id: int
    actor_id: int
    purpose: str
    state: str
    actor: _ActorSummary | None = None
    model: _ModelSummary | None = None
    model_binding_id: int | None = None


class ActorIn(_Named):
    role: str | None = None
    lifecycle_state: str | None = None


class RunIn(_In):
    id: int
    task_id: int | None = None
    actor_id: int
    assignment_id: int | None = None
    status: str
    trace_id: str | None = None
    configured_model_alias: str | None = None
    resolved_model_id: str | None = None
    metadata: dict[str, Any] = {}
    started_at: datetime
    heartbeat_at: datetime | None = None
    ended_at: datetime | None = None


class _Harness(_In):
    kind: str
    session_id: str


class _MemberIn(_In):
    actor_id: int | None = None
    actor_name: str
    display_name: str | None = None
    role: str | None = None
    runtime_ref: str


class TopologyIn(_In):
    members: list[_MemberIn] = []


class RunRowIn(_In):
    """A row of the run list people may read: no metadata, but the declared harness."""

    id: int
    task_id: int | None = None
    actor_id: int
    assignment_id: int | None = None
    status: str
    trace_id: str | None = None
    harness: _Harness | None = None
    configured_model_alias: str | None = None
    resolved_model_id: str | None = None
    started_at: datetime
    heartbeat_at: datetime | None = None
    ended_at: datetime | None = None
    usage: dict[str, Any] | None = None


def _declared_harness(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    """The harness a run's metadata declares, if it has the shape WorkChord checks for."""
    harness = metadata.get("harness")
    if not isinstance(harness, dict):
        return None, None
    kind, session_id = harness.get("kind"), harness.get("session_id")
    if not isinstance(kind, str) or not isinstance(session_id, str):
        return None, None
    return kind[:40], session_id[:255]


class Unparseable(Exception):
    """The server's answer is not in a shape agdash knows."""


class Unavailable(Exception):
    """The stored credentials may not read this."""


def _parse(adapter: TypeAdapter, data: Any, what: str) -> Any:
    try:
        return adapter.validate_python(data)
    except ValidationError as error:
        places = sorted(
            {".".join(str(part) for part in e["loc"]) or "(top)" for e in error.errors()}
        )
        shown = ", ".join(places[:5]) + (f" and {len(places) - 5} more" if len(places) > 5 else "")
        raise Unparseable(f"{what}: unexpected shape at {shown}") from None


PROJECTS = TypeAdapter(list[ProjectIn])
TASKS = TypeAdapter(list[TaskIn])
ITERATIONS = TypeAdapter(list[IterationIn])
PIPELINE = TypeAdapter(dict[str, list[TaskIn]])
ASSIGNMENTS = TypeAdapter(list[AssignmentIn])
ACTORS = TypeAdapter(list[ActorIn])
RUN = TypeAdapter(RunIn)
RUN_ROWS = TypeAdapter(list[RunRowIn])
TOPOLOGY = TypeAdapter(TopologyIn)


def _flatten(tasks: Iterable[TaskIn]) -> Iterable[TaskIn]:
    for task in tasks:
        yield task
        yield from _flatten(task.children)


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# -- one pass over one connection ------------------------------------------------------


@dataclass
class Outcome:
    state: MirrorState
    detail: str | None = None
    rows: int | None = None


@dataclass
class Pass:
    """What one pass learned that later kinds use."""

    now: datetime
    projects: list[ProjectIn] | None = None
    actors_seen: dict[int, _Named] = field(default_factory=dict)
    roles: dict[int, str] = field(default_factory=dict)
    features: frozenset[str] = frozenset()


@dataclass
class Report:
    connection_id: int
    reachable: bool
    problem: str | None
    objects: dict[MirrorObject, Outcome]


def _upsert(db: DbSession, model, connection_id: int, remote_id: int, **values) -> Any:
    row = db.scalars(
        select(model).where(model.connection_id == connection_id, model.remote_id == remote_id)
    ).one_or_none()
    if row is None:
        row = model(connection_id=connection_id, remote_id=remote_id)
        db.add(row)
    for name, value in values.items():
        setattr(row, name, value)
    return row


def _prune(db: DbSession, model, connection_id: int, keep: set[int]) -> None:
    db.execute(
        delete(model).where(
            model.connection_id == connection_id, model.remote_id.not_in(keep or {-1})
        )
    )


class _Syncer:
    def __init__(self, db: DbSession, row: Connection, client: WorkChordClient, now: datetime):
        self.db = db
        self.row = row
        self.client = client
        self.seen = Pass(now)

    @property
    def cid(self) -> int:
        return self.row.id

    def _store_task(self, task: TaskIn, iteration_names: dict[int, str]) -> None:
        if task.claimed_by is not None:
            self.seen.actors_seen.setdefault(task.claimed_by.id, task.claimed_by)
        _upsert(
            self.db,
            WcTask,
            self.cid,
            task.id,
            project_remote_id=task.project_id,
            iteration_remote_id=task.iteration_id,
            iteration_name=iteration_names.get(task.iteration_id) if task.iteration_id else None,
            external_key=task.external_key,
            title=task.title,
            status=task.status,
            claim_holder_remote_id=task.claimed_by.id if task.claimed_by else None,
            claim_expires_at=_utc(task.claim_expires_at),
            fetched_at=self.seen.now,
        )

    def projects(self) -> Outcome:
        projects = _parse(PROJECTS, self.client.projects(), "projects")
        for project in projects:
            _upsert(
                self.db,
                WcProject,
                self.cid,
                project.id,
                name=project.name,
                status=project.status,
                health=project.health,
                initiative_name=project.initiative.name if project.initiative else None,
                fetched_at=self.seen.now,
            )
        _prune(self.db, WcProject, self.cid, {p.id for p in projects})
        self.seen.projects = projects
        return Outcome(MirrorState.OK, rows=len(projects))

    def tasks(self) -> Outcome:
        project_ids = (
            [p.id for p in self.seen.projects]
            if self.seen.projects is not None
            else list(
                self.db.scalars(
                    select(WcProject.remote_id).where(WcProject.connection_id == self.cid)
                )
            )
        )
        if self.seen.projects is None and not project_ids:
            raise Unavailable("needs the project list, which this pass could not read")
        iterations = {
            i.id: i.name for i in _parse(ITERATIONS, self.client.iterations(), "iterations")
        }
        kept: set[int] = set()
        for project_id in project_ids:
            for task in _flatten(_parse(TASKS, self.client.project_tasks(project_id), "tasks")):
                self._store_task(task, iterations)
                kept.add(task.id)
        _prune(self.db, WcTask, self.cid, kept)
        return Outcome(MirrorState.OK, rows=len(kept))

    def pipeline(self) -> Outcome:
        stages = _parse(PIPELINE, self.client.pipeline(), "pipeline")
        iterations = dict(
            self.db.execute(
                select(WcTask.iteration_remote_id, WcTask.iteration_name).where(
                    WcTask.connection_id == self.cid, WcTask.iteration_remote_id.is_not(None)
                )
            ).all()
        )
        for task in self.db.scalars(select(WcTask).where(WcTask.connection_id == self.cid)):
            task.pipeline_stage = None
        self.db.flush()
        count = 0
        for stage, tasks in stages.items():
            for task in tasks:
                self._store_task(task, iterations)
                self.db.flush()
                row = self.db.scalars(
                    select(WcTask).where(
                        WcTask.connection_id == self.cid, WcTask.remote_id == task.id
                    )
                ).one()
                row.pipeline_stage = stage[:50]
                count += 1
        return Outcome(MirrorState.OK, rows=count)

    def assignments(self) -> Outcome:
        rows = _parse(ASSIGNMENTS, self.client.assignments(limit=ASSIGNMENT_LIMIT), "assignments")
        projects = dict(
            self.db.execute(
                select(WcTask.remote_id, WcTask.project_remote_id).where(
                    WcTask.connection_id == self.cid
                )
            ).all()
        )
        for item in rows:
            if item.actor is not None:
                self.seen.actors_seen.setdefault(item.actor.id, item.actor)
                if item.actor.role:
                    self.seen.roles[item.actor.id] = item.actor.role
            binding = (
                item.model.configured_model_alias
                if item.model
                else (f"binding {item.model_binding_id}" if item.model_binding_id else None)
            )
            _upsert(
                self.db,
                WcAssignment,
                self.cid,
                item.id,
                actor_remote_id=item.actor_id,
                task_remote_id=item.task_id,
                project_remote_id=projects.get(item.task_id),
                mode=item.purpose[:30],
                status=item.state[:50],
                model_binding=binding[:255] if binding else None,
                fetched_at=self.seen.now,
            )
        complete = len(rows) < ASSIGNMENT_LIMIT
        if complete:
            _prune(self.db, WcAssignment, self.cid, {item.id for item in rows})
        detail = None if complete else f"the first {ASSIGNMENT_LIMIT}; older rows are kept"
        return Outcome(MirrorState.OK, detail, rows=len(rows))

    def actors(self) -> Outcome:
        outcome = self._actors()
        if TOPOLOGY_READ not in self.seen.features:
            return outcome
        try:
            note = self._runtime_refs()
        except Unparseable as error:
            note = f"no runtime references: {error}"
        except httpx.HTTPError:
            note = "no runtime references: the team could not be read"
        outcome.detail = "; ".join(part for part in (outcome.detail, note) if part)
        return outcome

    def _runtime_refs(self) -> str:
        """Each team member's runtime reference, from the team topology."""
        topology = _parse(TOPOLOGY, self.client.team_topology(), "team topology")
        found = 0
        for member in topology.members:
            if member.actor_id is None:
                continue
            row = _upsert(
                self.db,
                WcActor,
                self.cid,
                member.actor_id,
                runtime_ref=redact_text(member.runtime_ref)[:1024],
            )
            if row.name is None:
                row.name = member.actor_name[:100]
                row.display_name = member.display_name
                row.fetched_at = self.seen.now
            if member.role and not row.role:
                row.role = member.role[:30]
            found += 1
        return f"{found} runtime references"

    def _actors(self) -> Outcome:
        roster_problem = None
        if self.client.can_read_runs:
            try:
                roster = _parse(ACTORS, self.client.actors(), "actors")
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in (401, 403):
                    raise
                roster_problem = "the agent key may not read the actor roster"
            else:
                for actor in roster:
                    _upsert(
                        self.db,
                        WcActor,
                        self.cid,
                        actor.id,
                        name=actor.name[:100],
                        display_name=actor.display_name,
                        role=(actor.role or "")[:30] or None,
                        lifecycle_state=actor.lifecycle_state,
                        fetched_at=self.seen.now,
                    )
                return Outcome(MirrorState.OK, rows=len(roster))
        else:
            roster_problem = "the actor roster needs an agent key with planning:read"
        for actor_id, actor in self.seen.actors_seen.items():
            row = _upsert(
                self.db,
                WcActor,
                self.cid,
                actor_id,
                name=actor.name[:100],
                display_name=actor.display_name,
                fetched_at=self.seen.now,
            )
            if actor_id in self.seen.roles:
                row.role = self.seen.roles[actor_id][:30]
        learned = len(self.seen.actors_seen)
        return Outcome(
            MirrorState.UNAVAILABLE,
            f"{roster_problem}; {learned} learned from task claims and assignments",
            rows=learned,
        )

    def _listed_runs(self) -> Outcome:
        rows = _parse(RUN_ROWS, self.client.run_list(limit=RUN_LIST_LIMIT), "runs")
        for run in rows:
            usage = (
                redact_value({k: run.usage.get(k) for k in USAGE_FIELDS if k in run.usage})
                if run.usage
                else None
            )
            _upsert(
                self.db,
                WcRun,
                self.cid,
                run.id,
                task_remote_id=run.task_id,
                actor_remote_id=run.actor_id,
                assignment_remote_id=run.assignment_id,
                status=run.status[:50],
                trace_id=redact_text(run.trace_id)[:255] if run.trace_id else None,
                configured_model_alias=run.configured_model_alias,
                resolved_model_id=run.resolved_model_id,
                declared_usage=usage,
                started_at=_utc(run.started_at),
                heartbeat_at=_utc(run.heartbeat_at),
                ended_at=_utc(run.ended_at),
                harness_kind=run.harness.kind[:40] if run.harness else None,
                harness_session_id=run.harness.session_id[:255] if run.harness else None,
                fetched_at=self.seen.now,
            )
        detail = f"the newest {RUN_LIST_LIMIT}" if len(rows) >= RUN_LIST_LIMIT else None
        return Outcome(MirrorState.OK, detail, rows=len(rows))

    def runs(self) -> Outcome:
        if not self.client.can_read_runs:
            if PERSON_RUN_READS in self.seen.features:
                return self._listed_runs()
            raise Unavailable("runs need an agent key with planning:read")
        linked = select(RunLink.run_remote_id).where(
            RunLink.connection_id == self.cid, RunLink.run_remote_id.is_not(None)
        )
        open_runs = select(WcRun.remote_id).where(
            WcRun.connection_id == self.cid, WcRun.ended_at.is_(None)
        )
        known = set(self.db.scalars(linked)) | set(self.db.scalars(open_runs))
        stored = {
            r.remote_id: r
            for r in self.db.scalars(select(WcRun).where(WcRun.connection_id == self.cid))
        }

        # Not yet fetched first, then the most recently started.
        def priority(run_id: int) -> tuple:
            row = stored.get(run_id)
            started = row.started_at.timestamp() if row is not None and row.started_at else 0.0
            return (row is not None, -started, -run_id)

        order = sorted(known, key=priority)
        missing = 0
        for run_id in order[:RUNS_PER_PASS]:
            try:
                run = _parse(RUN, self.client.run(run_id), f"run {run_id}")
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404:
                    missing += 1
                    continue
                raise
            usage = None
            try:
                declared = self.client.run_usage(run_id)
                if isinstance(declared, dict):
                    usage = redact_value(
                        {k: declared.get(k) for k in USAGE_FIELDS if k in declared}
                    )
            except httpx.HTTPStatusError as error:
                if error.response.status_code not in (403, 404):
                    raise
            kind, session_id = _declared_harness(run.metadata)
            _upsert(
                self.db,
                WcRun,
                self.cid,
                run.id,
                task_remote_id=run.task_id,
                actor_remote_id=run.actor_id,
                assignment_remote_id=run.assignment_id,
                status=run.status[:50],
                trace_id=redact_text(run.trace_id)[:255] if run.trace_id else None,
                configured_model_alias=run.configured_model_alias,
                resolved_model_id=run.resolved_model_id,
                run_metadata=redact_value(run.metadata) or None,
                declared_usage=usage,
                started_at=_utc(run.started_at),
                heartbeat_at=_utc(run.heartbeat_at),
                ended_at=_utc(run.ended_at),
                harness_kind=kind,
                harness_session_id=session_id,
                fetched_at=self.seen.now,
            )
        left = max(0, len(order) - RUNS_PER_PASS)
        notes = [f"{missing} no longer on the server"] if missing else []
        if left:
            notes.append(f"{left} more next pass")
        return Outcome(
            MirrorState.OK, "; ".join(notes) or None, rows=min(len(order), RUNS_PER_PASS)
        )


ORDER = (
    MirrorObject.PROJECTS,
    MirrorObject.TASKS,
    MirrorObject.PIPELINE,
    MirrorObject.ASSIGNMENTS,
    MirrorObject.ACTORS,
    MirrorObject.RUNS,
)


def _status_error(error: httpx.HTTPStatusError) -> Outcome:
    code = error.response.status_code
    if code in (401, 403):
        return Outcome(MirrorState.UNAVAILABLE, f"the credentials may not read it (HTTP {code})")
    if code in (404, 405):
        return Outcome(MirrorState.UNAVAILABLE, f"this server does not offer it (HTTP {code})")
    return Outcome(MirrorState.ERROR, f"the server answered HTTP {code}")


def _record(db: DbSession, connection_id: int, kind: MirrorObject, outcome: Outcome, now) -> None:
    row = db.scalars(
        select(WcSyncObject).where(
            WcSyncObject.connection_id == connection_id, WcSyncObject.kind == kind
        )
    ).one_or_none()
    if row is None:
        row = WcSyncObject(connection_id=connection_id, kind=kind)
        db.add(row)
    row.state = outcome.state
    row.detail = outcome.detail
    row.checked_at = now
    if outcome.state in (MirrorState.OK, MirrorState.UNAVAILABLE):
        row.rows = outcome.rows
    if outcome.state is MirrorState.OK:
        row.synced_at = now


def sync_connection(
    db: DbSession,
    secrets: SecretStore,
    row: Connection,
    *,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> Report:
    """Refresh the local copy of one connection's objects. Commits nothing; the caller does."""
    now = now or datetime.now(UTC)
    if row.credential_ref:
        session = Session.load(secrets.get(row.credential_ref))
        problem = (
            "the stored session is missing; pair again"
            if session is None
            else ("the session expired; pair again" if session.expired(now) else None)
        )
        if problem:
            row.sync_state, row.last_error = SyncState.ERROR, problem
            return Report(row.id, False, problem, {})
    with open_client(secrets, row, transport) as client:
        try:
            me = client.me()
        except (httpx.HTTPError, ValueError) as error:
            problem = f"could not reach the server: {type(error).__name__}"
            row.sync_state, row.last_error = SyncState.ERROR, problem
            return Report(row.id, False, problem, {})
        if row.credential_ref and not me.get("authenticated"):
            problem = "the server no longer accepts the session; pair again"
            row.sync_state, row.last_error = SyncState.ERROR, problem
            return Report(row.id, False, problem, {})
        syncer = _Syncer(db, row, client, now)
        features = me.get("features")
        if isinstance(features, list):
            syncer.seen.features = frozenset(f for f in features if isinstance(f, str))
            seen = dict(row.contract_seen or {})
            if seen.get("features") != sorted(syncer.seen.features):
                seen["features"] = sorted(syncer.seen.features)
                row.contract_seen = seen
        outcomes: dict[MirrorObject, Outcome] = {}
        for kind in ORDER:
            savepoint = db.begin_nested()
            try:
                outcome = getattr(syncer, kind.value)()
                db.flush()
                savepoint.commit()
            except Exception as error:  # every failure leaves the copy as it was
                savepoint.rollback()
                if isinstance(error, Unparseable):
                    outcome = Outcome(MirrorState.UNPARSEABLE, str(error))
                    log.warning("WorkChord %s on connection %d: %s", kind.value, row.id, error)
                elif isinstance(error, Unavailable | PermissionError):
                    outcome = Outcome(MirrorState.UNAVAILABLE, str(error))
                elif isinstance(error, httpx.HTTPStatusError):
                    outcome = _status_error(error)
                elif isinstance(error, httpx.HTTPError | ValueError):
                    outcome = Outcome(
                        MirrorState.ERROR, f"the request failed: {type(error).__name__}"
                    )
                else:
                    raise
                if kind is MirrorObject.PROJECTS:
                    syncer.seen.projects = None
            outcomes[kind] = outcome
            _record(db, row.id, kind, outcome, now)
    failed = [k.value for k, o in outcomes.items() if o.state is MirrorState.ERROR]
    reachable = len(failed) < len(outcomes)
    row.sync_state = SyncState.OK if reachable else SyncState.ERROR
    row.last_error = None if not failed else f"could not sync {', '.join(failed)}"
    if reachable:
        row.last_synced_at = now
    db.flush()
    return Report(row.id, reachable, row.last_error, outcomes)


# -- polling ------------------------------------------------------------------------


class MirrorSync:
    """Polls every WorkChord connection, backing off from a server that cannot be reached."""

    def __init__(
        self,
        sessions: sessionmaker[DbSession],
        secrets: SecretStore,
        *,
        interval: float = INTERVAL_SECONDS,
        max_backoff: float = MAX_BACKOFF_SECONDS,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = sessions
        self._secrets = secrets
        self._interval = interval
        self._max_backoff = max_backoff
        self._transport = transport
        self._clock = clock
        self._next: dict[int, float] = {}
        self._failures: dict[int, int] = {}
        self._linker = Linker()
        self._resolver = Resolver()
        self._publisher = Publisher(transport)

    def delay(self, connection_id: int) -> float:
        failures = self._failures.get(connection_id, 0)
        return (
            min(self._interval * (2**failures), self._max_backoff) if failures else self._interval
        )

    def wake(self, connection_id: int) -> None:
        """Sync this connection at the next tick instead of waiting for its turn."""
        self._next[connection_id] = 0.0

    def due(self, connection_id: int) -> bool:
        return self._clock() >= self._next.get(connection_id, 0.0)

    def tick(self) -> list[Report]:
        """Sync every connection whose time has come."""
        reports = []
        with self._sessions() as db:
            ids = list(db.scalars(select(Connection.id).where(Connection.kind == KIND)))
            for connection_id in ids:
                if not self.due(connection_id):
                    continue
                row = db.get(Connection, connection_id)
                try:
                    report = sync_connection(db, self._secrets, row, transport=self._transport)
                    db.commit()
                except Exception:
                    db.rollback()
                    log.exception("a WorkChord sync failed; it is retried later")
                    report = Report(connection_id, False, "sync failed", {})
                if report.reachable:
                    self._failures.pop(connection_id, None)
                else:
                    self._failures[connection_id] = self._failures.get(connection_id, 0) + 1
                self._next[connection_id] = self._clock() + self.delay(connection_id)
                reports.append(report)
            for gone in set(self._next) - set(ids):
                self._next.pop(gone, None)
                self._failures.pop(gone, None)
            if reports:
                self.link(db, ids)
        return reports

    def link(self, db: DbSession, ids: list[int]) -> None:
        """Link sessions to runs and tasks, then to actors and projects, from local evidence,
        rules, pins and the fresh copy."""
        try:
            connections = [db.get(Connection, connection_id) for connection_id in ids]
            connections = [c for c in connections if c is not None]
            self._linker.run(db, connections)
            self._resolver.run(db, connections)
            db.commit()
            # The one write to WorkChord, only where publish mode is on.
            self._publisher.run(db, self._secrets, connections)
            db.commit()
        except Exception:
            db.rollback()
            log.exception("linking sessions to WorkChord failed; the next pass retries")

    def run(self, stop: threading.Event) -> list[threading.Thread]:
        def loop() -> None:
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("a WorkChord sync pass failed; the next one retries")
                stop.wait(TICK_SECONDS)

        thread = threading.Thread(target=loop, name="agdash-mirror", daemon=True)
        thread.start()
        return [thread]


def object_states(db: DbSession, connection_id: int) -> list[WcSyncObject]:
    rows = db.scalars(select(WcSyncObject).where(WcSyncObject.connection_id == connection_id))
    order = {kind: i for i, kind in enumerate(ORDER)}
    return sorted(rows, key=lambda r: order.get(r.kind, 99))
