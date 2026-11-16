"""Map sessions to WorkChord actors and projects, from evidence, local rules and pins.

Each mapping is kept as the evidence behind it (``identity_links`` and
``project_links``), one row per basis and target, strongest basis first:

**Actor.** ``capabilities``: the transcript holds the result of a WorkChord call
that names the calling actor (capabilities, or the agent's own work queue).
``run``: the actor of a run the session is exactly linked to. ``runtime_ref``:
a local rule ties sessions of a folder, repository or launcher to a team
member's runtime reference, and WorkChord's team names the actor with it.
``manual``: the person pinned the session to an actor.

**Project.** ``task``: the project of a task the session is exactly linked to.
``assignment``: every current assignment of the session's actor is in one
project. ``rule``: a local rule maps a folder or repository to a project, which
covers sessions that never talk to WorkChord. ``manual``: a pin.

:func:`decide` turns the evidence into a mapping without hiding anything: no
evidence is **unmapped**; evidence that disagrees is a **conflict**, with no
value and every candidate listed; a pin is the person's own answer and maps the
session even then, still listing what disagrees.
"""

import fnmatch
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session as DbSession

from agdash.connectors.links import _addresses, _documents, _int
from agdash.db.models import (
    Connection,
    Event,
    IdentityLink,
    MappingRule,
    ProjectLink,
    RunLink,
    Session,
    SessionProcess,
    WcActor,
    WcAssignment,
    WcRun,
    WcTask,
)
from agdash.vocab import (
    EventKind,
    IdentityLinkBasis,
    ProjectLinkBasis,
    RuleMatchKind,
    RuleTargetKind,
    RunLinkBasis,
)

EXACT_RUNS = (RunLinkBasis.TOOL_CALL, RunLinkBasis.TRACE_ID)
ACTOR_TOOLS = ("agent_get_capabilities", "agent_get_my_work")
ACTOR_ROUTES = ("/api/agent/capabilities", "/api/agent/me/work")
# Assignment states that are over; any other state counts as current.
FINISHED = {
    "completed",
    "complete",
    "done",
    "cancelled",
    "canceled",
    "rejected",
    "superseded",
    "expired",
    "failed",
    "closed",
}
TOOL_BATCH = 500


# -- deciding -----------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    basis: str
    target: int
    evidence: dict[str, Any]


@dataclass
class Mapping:
    state: str  # mapped, conflict or unmapped
    target: int | None = None
    basis: str | None = None
    suggestion: int | None = None
    candidates: list[Candidate] = field(default_factory=list)


def decide(candidates: Iterable[Candidate], order: Iterable[str]) -> Mapping:
    """One mapping from its evidence. ``order`` lists the bases, strongest first."""
    rank = {basis: i for i, basis in enumerate(order)}
    found = sorted(candidates, key=lambda c: (rank.get(c.basis, 99), c.target))
    if not found:
        return Mapping("unmapped")
    pins = [c for c in found if c.basis == "manual"]
    if pins:
        return Mapping("mapped", pins[0].target, "manual", candidates=found)
    targets = {c.target for c in found}
    if len(targets) == 1:
        return Mapping("mapped", found[0].target, found[0].basis, candidates=found)
    strongest = {c.target for c in found if c.basis == found[0].basis}
    suggestion = next(iter(strongest)) if len(strongest) == 1 else None
    return Mapping("conflict", suggestion=suggestion, candidates=found)


ACTOR_ORDER = [basis.value for basis in IdentityLinkBasis]
PROJECT_ORDER = [basis.value for basis in ProjectLinkBasis]


def mappings(db: DbSession, session_id: int, connection_id: int) -> tuple[Mapping, Mapping]:
    """The actor and project mappings of one session on one connection."""
    actors = db.scalars(
        select(IdentityLink).where(
            IdentityLink.session_id == session_id, IdentityLink.connection_id == connection_id
        )
    )
    projects = db.scalars(
        select(ProjectLink).where(
            ProjectLink.session_id == session_id, ProjectLink.connection_id == connection_id
        )
    )
    return (
        decide(
            (Candidate(r.basis.value, r.actor_remote_id, r.evidence or {}) for r in actors),
            ACTOR_ORDER,
        ),
        decide(
            (Candidate(r.basis.value, r.project_remote_id, r.evidence or {}) for r in projects),
            PROJECT_ORDER,
        ),
    )


# -- matching rules -----------------------------------------------------------------


def _path(text: str):
    return (
        PureWindowsPath(text) if re.match(r"^[A-Za-z]:[\\/]|^\\\\", text) else PurePosixPath(text)
    )


def inside(path: str | None, folder: str) -> bool:
    """Whether ``path`` is ``folder`` or within it, by whole path parts."""
    if not path:
        return False
    try:
        return _path(path).is_relative_to(_path(folder.rstrip("/\\") or folder))
    except (TypeError, ValueError):
        return False


def repository_key(address: str | None) -> str | None:
    """A remote's address reduced to host and path, so its spellings compare equal."""
    if not address:
        return None
    text = address.strip().lower()
    parts = urlsplit(text)
    if parts.scheme and parts.netloc:
        text = parts.netloc.rpartition("@")[2] + parts.path
    else:
        text = text.rpartition("@")[2].replace(":", "/", 1)  # git@host:owner/name
    text = text.rstrip("/")
    return text[:-4] if text.endswith(".git") else text


def _launchers(db: DbSession, session_ids: list[int]) -> dict[int, list[str]]:
    found: dict[int, list[str]] = defaultdict(list)
    if not session_ids:
        return found
    rows = db.execute(
        select(SessionProcess.session_id, SessionProcess.argv_redacted).where(
            SessionProcess.session_id.in_(session_ids)
        )
    )
    for session_id, argv in rows:
        if argv:
            found[session_id].append(" ".join(str(part) for part in argv))
    return found


def matches(rule: MappingRule, session: Session, launchers: list[str]) -> bool:
    if not rule.enabled or (rule.harness and rule.harness != session.harness):
        return False
    if rule.match_kind is RuleMatchKind.SESSION:
        return rule.session_id == session.id
    value = rule.match_value or ""
    if rule.match_kind is RuleMatchKind.FOLDER:
        return inside(session.cwd, value) or inside(session.worktree, value)
    if rule.match_kind is RuleMatchKind.REPOSITORY:
        key = repository_key(session.repo)
        return key is not None and key == repository_key(value)
    if rule.match_kind is RuleMatchKind.LAUNCHER:
        return any(fnmatch.fnmatchcase(line, value) for line in launchers)
    return False


# -- resolving ----------------------------------------------------------------------


@dataclass
class Resolved:
    actor_links: int = 0
    project_links: int = 0
    last_event_id: int = 0


class Resolver:
    """Rebuilds the evidence rows for every session on every WorkChord connection.

    Calls that name the actor are read once, from the events recorded since the
    previous pass, and their rows are kept; every other row is rebuilt from the
    current rules, run links and WorkChord copy, so a changed rule or a newly
    synced task takes effect on the next pass.
    """

    def __init__(self) -> None:
        self._read_up_to = 0

    def run(self, db: DbSession, connections: list[Connection]) -> Resolved:
        resolved = Resolved()
        if not connections:
            return resolved
        self._read_up_to = self._actor_calls(db, connections, self._read_up_to)
        resolved.last_event_id = self._read_up_to
        sessions = list(db.scalars(select(Session)))
        launchers = _launchers(db, [s.id for s in sessions])
        for connection in connections:
            self._connection(db, connection, sessions, launchers, resolved)
        db.flush()
        return resolved

    # The actor named by the session's own WorkChord calls.

    def _actor_calls(self, db: DbSession, connections: list[Connection], after: int) -> int:
        name = Event.payload["name"].as_string()
        wanted = or_(
            *(name.like(f"%{tool}") for tool in ACTOR_TOOLS),
            *(cast(Event.payload, String).like(f"%{route}%") for route in ACTOR_ROUTES),
        )
        last = after
        while True:
            calls = db.execute(
                select(Event.id, Event.session_id, Event.sequence, Event.payload)
                .where(Event.kind == EventKind.TOOL_CALL, Event.id > last, wanted)
                .order_by(Event.id)
                .limit(TOOL_BATCH)
            ).all()
            if not calls:
                return last
            for event_id, session_id, sequence, payload in calls:
                last = event_id
                self._actor_call(db, connections, session_id, sequence, payload or {})

    def _actor_call(self, db, connections, session_id, sequence, payload) -> None:
        tool = re.split(r"__|\.|/", payload.get("name") or "")[-1]
        text = json.dumps(payload.get("input"))
        if tool not in ACTOR_TOOLS and not any(route in text for route in ACTOR_ROUTES):
            return
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            return
        for result in db.scalars(
            select(Event.payload).where(
                Event.session_id == session_id,
                Event.kind == EventKind.TOOL_RESULT,
                Event.sequence > sequence,
                Event.payload["call_id"].as_string() == call_id,
            )
        ):
            if (result or {}).get("is_error"):
                continue
            actor_id = next(
                (
                    _int(document["actor"].get("id"))
                    for document in _documents((result or {}).get("output"))
                    if isinstance(document.get("actor"), dict)
                    and _int(document["actor"].get("id")) is not None
                ),
                None,
            )
            if actor_id is None:
                continue
            connection = _place(db, connections, actor_id, text)
            if connection is None:
                return
            _keep(
                db,
                IdentityLink,
                "actor_remote_id",
                session_id,
                connection.id,
                actor_id,
                IdentityLinkBasis.CAPABILITIES,
                {"call": tool if tool in ACTOR_TOOLS else "http", "sequence": sequence},
            )
            return

    # Everything rebuilt each pass.

    def _connection(self, db, connection, sessions, launchers, resolved: Resolved) -> None:
        cid = connection.id
        rules = list(
            db.scalars(
                select(MappingRule)
                .where(MappingRule.connection_id == cid, MappingRule.enabled.is_(True))
                .order_by(MappingRule.priority.desc(), MappingRule.id)
            )
        )
        by_ref = {
            ref: actor_id
            for actor_id, ref in db.execute(
                select(WcActor.remote_id, WcActor.runtime_ref).where(
                    WcActor.connection_id == cid, WcActor.runtime_ref.is_not(None)
                )
            )
        }
        runs = defaultdict(list)
        for link in db.scalars(
            select(RunLink).where(RunLink.connection_id == cid, RunLink.basis.in_(EXACT_RUNS))
        ):
            runs[link.session_id].append(link)
        run_actors = dict(
            db.execute(
                select(WcRun.remote_id, WcRun.actor_remote_id).where(WcRun.connection_id == cid)
            ).all()
        )
        task_projects = dict(
            db.execute(
                select(WcTask.remote_id, WcTask.project_remote_id).where(
                    WcTask.connection_id == cid
                )
            ).all()
        )
        assignments = defaultdict(set)
        for actor_id, project_id, status in db.execute(
            select(
                WcAssignment.actor_remote_id,
                WcAssignment.project_remote_id,
                WcAssignment.status,
            ).where(WcAssignment.connection_id == cid)
        ):
            if actor_id is not None and (status or "").lower() not in FINISHED:
                assignments[actor_id].add(project_id)

        actor_rows: dict[tuple, dict] = {}
        project_rows: dict[tuple, dict] = {}
        for session in sessions:
            mine = [r for r in rules if matches(r, session, launchers.get(session.id, []))]
            for link in runs.get(session.id, []):
                actor = run_actors.get(link.run_remote_id) or (link.evidence or {}).get("actor_id")
                if _int(actor) is not None:
                    actor_rows[(session.id, actor, IdentityLinkBasis.RUN)] = {
                        "run_id": link.run_remote_id,
                        "run_link": link.basis.value,
                    }
                project = task_projects.get(link.task_remote_id)
                if project is not None:
                    project_rows[(session.id, project, ProjectLinkBasis.TASK)] = {
                        "task_id": link.task_remote_id,
                        "run_link": link.basis.value,
                    }
            for rule in mine:
                target = rule.target_value
                if rule.target_kind is RuleTargetKind.RUNTIME_REF and target in by_ref:
                    basis = (
                        IdentityLinkBasis.MANUAL
                        if rule.match_kind is RuleMatchKind.SESSION
                        else IdentityLinkBasis.RUNTIME_REF
                    )
                    actor_rows[(session.id, by_ref[target], basis)] = _rule_evidence(rule)
                elif rule.target_kind is RuleTargetKind.ACTOR and target.isdigit():
                    if rule.match_kind is RuleMatchKind.SESSION:
                        key = (session.id, int(target), IdentityLinkBasis.MANUAL)
                        actor_rows[key] = _rule_evidence(rule)
                elif rule.target_kind is RuleTargetKind.PROJECT and target.isdigit():
                    basis = (
                        ProjectLinkBasis.MANUAL
                        if rule.match_kind is RuleMatchKind.SESSION
                        else ProjectLinkBasis.RULE
                    )
                    key = (session.id, int(target), basis)
                    project_rows.setdefault(key, _rule_evidence(rule))

        resolved.actor_links += _sync(
            db,
            IdentityLink,
            "actor_remote_id",
            cid,
            actor_rows,
            keep=IdentityLinkBasis.CAPABILITIES,
        )
        db.flush()
        # The assignment tier needs each session's actor, decided from the rows above.
        evidence = defaultdict(list)
        for row in db.scalars(select(IdentityLink).where(IdentityLink.connection_id == cid)):
            evidence[row.session_id].append(
                Candidate(row.basis.value, row.actor_remote_id, row.evidence or {})
            )
        for session_id, candidates in evidence.items():
            actor = decide(candidates, ACTOR_ORDER)
            if actor.state != "mapped":
                continue
            projects = assignments.get(actor.target, set())
            if len(projects) == 1 and None not in projects:
                (project,) = projects
                project_rows[(session_id, project, ProjectLinkBasis.ASSIGNMENT)] = {
                    "actor_id": actor.target
                }
        resolved.project_links += _sync(db, ProjectLink, "project_remote_id", cid, project_rows)


def _rule_evidence(rule: MappingRule) -> dict[str, Any]:
    found = {
        "rule_id": rule.id,
        "match": rule.match_kind.value,
        "value": rule.match_value,
        "note": rule.note,
    }
    return {k: v for k, v in found.items() if v is not None}


def _place(db, connections: list[Connection], actor_id: int, text: str) -> Connection | None:
    if len(connections) == 1:
        return connections[0]
    named = [c for c in connections if any(a in text.lower() for a in _addresses(c))]
    if len(named) == 1:
        return named[0]
    holding = [
        c
        for c in connections
        if db.scalar(
            select(WcActor.id).where(WcActor.connection_id == c.id, WcActor.remote_id == actor_id)
        )
    ]
    return holding[0] if len(holding) == 1 else None


def _keep(db, model, column: str, session_id, connection_id, target, basis, evidence) -> None:
    found = db.scalars(
        select(model).where(
            model.session_id == session_id,
            model.connection_id == connection_id,
            getattr(model, column) == target,
            model.basis == basis,
        )
    ).first()
    if found is None:
        db.add(
            model(
                session_id=session_id,
                connection_id=connection_id,
                basis=basis,
                evidence=evidence,
                **{column: target},
            )
        )
        db.flush()


def _sync(db, model, column: str, connection_id: int, wanted: dict, keep=None) -> int:
    """Make the rows on one connection exactly ``wanted``, leaving ``keep`` rows alone."""
    existing = {
        (row.session_id, getattr(row, column), row.basis): row
        for row in db.scalars(select(model).where(model.connection_id == connection_id))
    }
    for key, row in existing.items():
        if key[2] == keep:
            continue
        if key not in wanted:
            db.delete(row)
        elif row.evidence != wanted[key]:
            row.evidence = wanted[key]
    added = 0
    for (session_id, target, basis), evidence in wanted.items():
        if (session_id, target, basis) not in existing:
            db.add(
                model(
                    session_id=session_id,
                    connection_id=connection_id,
                    basis=basis,
                    evidence=evidence,
                    **{column: target},
                )
            )
            added += 1
    return added
