"""The live board by WorkChord project, with each session's task and run, and what needs a look.

``GET /api/projects/board`` takes the live sessions (active, waiting or idle)
and, for one WorkChord connection:

* groups each top-level session under the project it maps to, with its
  subagents under it; a session with no project, or with evidence that
  disagrees, is in the unmapped group;
* lists each project's agents: those of its live sessions and those with a
  current assignment there;
* gives each session its task (key, title, status, iteration, claim expiry),
  run (status, model bound, declared usage), whether the work is execution
  or review, and what agdash observed (models, usage);
* raises four attention flags, read-only signals for a person to look at:

  - ``run_without_session``: WorkChord says a run is running, and no live
    session on this host is linked to it;
  - ``claim_expired``: a session is still live after its task's claim expired;
  - ``model_mismatch``: the model observed is not the one WorkChord bound;
  - ``no_task``: a session works in a mapped project but is linked to no task.

What WorkChord holds comes from the mirror's copy; nothing here asks it.
"""

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from agdash.api.routes import Db, _one_per_message, _usage, _usage_columns
from agdash.api.schemas import UsageTotals
from agdash.connectors.links import EXACT, RANK
from agdash.connectors.resolver import ACTOR_ORDER, PROJECT_ORDER, Candidate, decide
from agdash.connectors.workchord import KIND
from agdash.db.models import (
    Connection,
    EventUsage,
    IdentityLink,
    ProjectLink,
    RunLink,
    Session,
    WcActor,
    WcAssignment,
    WcProject,
    WcRun,
    WcTask,
)
from agdash.vocab import SessionState

router = APIRouter()

LIVE = (SessionState.ACTIVE, SessionState.WAITING, SessionState.IDLE)
RUNNING = {"running", "in_progress", "started", "active"}
FINISHED_ASSIGNMENTS = {
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
REVIEW = {"review", "verification", "verify", "design_handoff"}
FlagCode = Literal["run_without_session", "claim_expired", "model_mismatch", "no_task"]


class Flag(BaseModel):
    code: FlagCode
    session_id: int | None = None
    run_id: int | None = None
    task_id: int | None = None
    task_key: str | None = None
    actor: str | None = None
    observed: str | None = None
    bound: str | None = None
    since: datetime | None = Field(None, description="When the claim expired.")


class TaskOut(BaseModel):
    id: int
    key: str | None
    title: str
    status: str | None
    iteration: str | None
    claim_expires_at: datetime | None


class RunOut(BaseModel):
    id: int
    status: str | None
    bound_model: str | None = Field(
        description="The model WorkChord bound or recorded for the run."
    )
    declared_usage: dict[str, Any] | None
    heartbeat_at: datetime | None


class ActorOut(BaseModel):
    id: int
    name: str
    role: str | None
    basis: str | None = Field(
        None, description="Why the session maps to it; null for an agent row."
    )


class Card(BaseModel):
    id: int
    harness: str
    title: str | None
    state: SessionState
    cwd: str | None
    last_activity_at: datetime | None
    parent_session_id: int | None
    project_basis: str | None
    actor: ActorOut | None
    task: TaskOut | None
    run: RunOut | None
    work: Literal["execution", "review"] | None
    observed_models: list[str]
    observed_usage: UsageTotals
    flags: list[FlagCode]
    subagents: list["Card"] = []


class AgentOut(ActorOut):
    live_sessions: int
    assigned: bool = Field(description="Has a current assignment in this project.")
    assignments: int = Field(
        0, description="Current assignments in this project: what was declared."
    )
    observed_bases: dict[str, int] = Field(
        default_factory=dict,
        description="Live sessions mapped to this agent here, by the basis of the agent mapping.",
    )


class ProjectGroup(BaseModel):
    project: dict[str, Any] | None = Field(
        description="id, name, status, health; null for unmapped."
    )
    reason: Literal["mapped", "unmapped"]
    agents: list[AgentOut]
    sessions: list[Card]


class ActivitySession(BaseModel):
    id: int
    harness: str
    title: str | None
    state: SessionState
    parent_session_id: int | None
    start: datetime | None = Field(description="When it started, else its first known activity.")
    end: datetime | None = Field(description="Its last activity, or when it ended.")


class ProjectLane(BaseModel):
    project: dict[str, Any] | None = Field(description="Null for sessions mapped to no project.")
    sessions: list[ActivitySession] = Field(description="Oldest start first.")


class ProjectActivity(BaseModel):
    connection: dict[str, Any] | None
    days: int
    since: datetime
    until: datetime
    lanes: list[ProjectLane] = Field(description="By project name; sessions mapped to none last.")
    omitted: int = Field(description="Sessions in the range beyond the limit, left out.")


class Board(BaseModel):
    connection: dict[str, Any] | None
    connections: list[dict[str, Any]]
    groups: list[ProjectGroup]
    flags: list[Flag]
    generated_at: datetime


# -- matching models ----------------------------------------------------------------


def _same_model(observed: str, bound: str) -> bool:
    """Equal, or one names the other with a version or date after it (model-a, model-a-0101)."""
    a, b = observed.strip().lower(), bound.strip().lower()
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return long.startswith(short) and bool(re.match(r"[-_.@:]", long[len(short) :]))


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# -- the board ----------------------------------------------------------------------


@router.get("/projects/board", response_model=Board, summary="Live sessions by project")
def board(
    db: Db,
    connection_id: Annotated[
        int | None, Query(description="Default: the first connection.")
    ] = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    connections = list(
        db.scalars(select(Connection).where(Connection.kind == KIND).order_by(Connection.id))
    )
    listed = [{"id": c.id, "name": c.name, "server": c.base_url} for c in connections]
    if connection_id is not None and connection_id not in {c.id for c in connections}:
        raise HTTPException(status_code=404, detail="no such connection")
    row = next((c for c in connections if connection_id in (None, c.id)), None)
    live = list(
        db.scalars(
            select(Session)
            .where(Session.state.in_(LIVE))
            .order_by(Session.last_activity_at.desc(), Session.id.desc())
            .limit(500)
        )
    )
    ids = [s.id for s in live]
    usage = {
        sid: UsageTotals(**_usage(rest))
        for sid, *rest in db.execute(
            select(EventUsage.session_id, *_usage_columns())
            .where(
                EventUsage.event_id.in_(
                    _one_per_message().where(EventUsage.session_id.in_(ids or [-1]))
                )
            )
            .group_by(EventUsage.session_id)
        )
    }
    if row is None:
        cards = {s.id: _card(s, usage, None, None, None, None, None, None, []) for s in live}
        return {
            "connection": None,
            "connections": listed,
            "groups": _groups(live, cards, {}, {}, {}, []),
            "flags": [],
            "generated_at": now,
        }
    cid = row.id

    def candidates(model, column):
        found = defaultdict(list)
        for link in db.scalars(
            select(model).where(model.connection_id == cid, model.session_id.in_(ids or [-1]))
        ):
            found[link.session_id].append(
                Candidate(link.basis.value, getattr(link, column), link.evidence or {})
            )
        return found

    projects_of = {
        sid: decide(found, PROJECT_ORDER)
        for sid, found in candidates(ProjectLink, "project_remote_id").items()
    }
    actors_of = {
        sid: decide(found, ACTOR_ORDER)
        for sid, found in candidates(IdentityLink, "actor_remote_id").items()
    }
    runs_of: dict[int, RunLink] = {}
    for link in db.scalars(
        select(RunLink).where(
            RunLink.connection_id == cid,
            RunLink.basis.in_(EXACT),
            RunLink.session_id.in_(ids or [-1]),
        )
    ):
        best = runs_of.get(link.session_id)
        if best is None or (RANK[link.basis], -link.confidence) < (
            RANK[best.basis],
            -best.confidence,
        ):
            runs_of[link.session_id] = link
    tasks = {t.remote_id: t for t in db.scalars(select(WcTask).where(WcTask.connection_id == cid))}
    runs = {r.remote_id: r for r in db.scalars(select(WcRun).where(WcRun.connection_id == cid))}
    actors = {
        a.remote_id: a for a in db.scalars(select(WcActor).where(WcActor.connection_id == cid))
    }
    projects = {
        p.remote_id: p for p in db.scalars(select(WcProject).where(WcProject.connection_id == cid))
    }
    assignments = {
        a.remote_id: a
        for a in db.scalars(select(WcAssignment).where(WcAssignment.connection_id == cid))
    }

    flags: list[Flag] = []
    cards: dict[int, Card] = {}
    for session in live:
        project = projects_of.get(session.id)
        actor_map = actors_of.get(session.id)
        link = runs_of.get(session.id)
        run = runs.get(link.run_remote_id) if link and link.run_remote_id else None
        task_id = (link.task_remote_id if link else None) or (run.task_remote_id if run else None)
        task = tasks.get(task_id) if task_id is not None else None
        assignment_id = (link.assignment_remote_id if link else None) or (
            run.assignment_remote_id if run else None
        )
        assignment = assignments.get(assignment_id) if assignment_id else None
        actor = actors.get(actor_map.target) if actor_map and actor_map.state == "mapped" else None
        own: list[Flag] = []
        expires = _utc(task.claim_expires_at) if task else None
        if expires is not None and expires < now:
            own.append(
                Flag(
                    code="claim_expired",
                    session_id=session.id,
                    task_id=task.remote_id,
                    task_key=task.external_key,
                    since=expires,
                )
            )
        bound = ((run.resolved_model_id or run.configured_model_alias) if run else None) or (
            assignment.model_binding if assignment else None
        )
        observed = [m for m in session.models or [] if m]
        if (
            bound
            and observed
            and not bound.startswith("binding ")
            and not any(_same_model(m, bound) for m in observed)
        ):
            own.append(
                Flag(
                    code="model_mismatch",
                    session_id=session.id,
                    run_id=run.remote_id if run else None,
                    observed=observed[-1],
                    bound=bound,
                )
            )
        if project is not None and project.state == "mapped" and task is None and link is None:
            if session.parent_session_id is None:
                own.append(Flag(code="no_task", session_id=session.id))
        flags += own
        cards[session.id] = _card(
            session,
            usage,
            project,
            actor_map,
            actor,
            task,
            run,
            assignment,
            [f.code for f in own],
        )

    linked_live = {link.run_remote_id for link in runs_of.values() if link.run_remote_id}
    for run in runs.values():
        if (
            (run.status or "").lower() in RUNNING
            and run.ended_at is None
            and run.remote_id not in linked_live
        ):
            task = tasks.get(run.task_remote_id) if run.task_remote_id else None
            who = actors.get(run.actor_remote_id) if run.actor_remote_id else None
            flags.append(
                Flag(
                    code="run_without_session",
                    run_id=run.remote_id,
                    task_id=run.task_remote_id,
                    task_key=task.external_key if task else None,
                    actor=(who.display_name or who.name) if who else None,
                )
            )

    agents: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for assignment in assignments.values():
        if (
            assignment.project_remote_id is None
            or assignment.actor_remote_id is None
            or (assignment.status or "").lower() in FINISHED_ASSIGNMENTS
        ):
            continue
        entry = agents[assignment.project_remote_id].setdefault(
            assignment.actor_remote_id, _agent_entry()
        )
        entry["assigned"] = True
        entry["assignments"] += 1
    for session in live:
        project = projects_of.get(session.id)
        actor_map = actors_of.get(session.id)
        if project and project.state == "mapped" and actor_map and actor_map.state == "mapped":
            entry = agents[project.target].setdefault(actor_map.target, _agent_entry())
            entry["live_sessions"] += 1
            entry["bases"][actor_map.basis] += 1

    agent_rows = {
        project_id: [
            AgentOut(
                id=actor_id,
                name=_actor_name(actors.get(actor_id), actor_id),
                role=actors[actor_id].role if actor_id in actors else None,
                live_sessions=entry["live_sessions"],
                assigned=entry["assigned"],
                assignments=entry["assignments"],
                observed_bases=dict(entry["bases"]),
            )
            for actor_id, entry in sorted(
                found.items(), key=lambda item: (-item[1]["live_sessions"], item[0])
            )
        ]
        for project_id, found in agents.items()
    }
    return {
        "connection": {"id": row.id, "name": row.name, "server": row.base_url},
        "connections": listed,
        "groups": _groups(live, cards, projects_of, projects, agent_rows, flags),
        "flags": flags,
        "generated_at": now,
    }


def _agent_entry() -> dict[str, Any]:
    return {"live_sessions": 0, "assigned": False, "assignments": 0, "bases": Counter()}


def _actor_name(actor: WcActor | None, actor_id: int) -> str:
    if actor is None:
        return f"#{actor_id}"
    return actor.display_name or actor.name


def _card(session, usage, project, actor_map, actor, task, run, assignment, flags) -> Card:
    mode = (assignment.mode or "").lower() if assignment else ""
    return Card(
        id=session.id,
        harness=session.harness,
        title=session.title,
        state=session.state,
        cwd=session.cwd,
        last_activity_at=session.last_activity_at,
        parent_session_id=session.parent_session_id,
        project_basis=project.basis if project is not None and project.state == "mapped" else None,
        actor=ActorOut(
            id=actor.remote_id,
            name=actor.display_name or actor.name,
            role=actor.role,
            basis=actor_map.basis,
        )
        if actor is not None
        else None,
        task=TaskOut(
            id=task.remote_id,
            key=task.external_key,
            title=task.title,
            status=task.status,
            iteration=task.iteration_name,
            claim_expires_at=_utc(task.claim_expires_at),
        )
        if task is not None
        else None,
        run=RunOut(
            id=run.remote_id,
            status=run.status,
            bound_model=run.resolved_model_id or run.configured_model_alias,
            declared_usage=run.declared_usage,
            heartbeat_at=_utc(run.heartbeat_at),
        )
        if run is not None
        else None,
        work=("review" if mode in REVIEW else "execution") if mode else None,
        observed_models=list(session.models or []),
        observed_usage=usage.get(session.id, UsageTotals()),
        flags=flags,
    )


def _groups(live, cards, projects_of, projects, agent_rows, flags) -> list[ProjectGroup]:
    live_ids = {s.id for s in live}
    by_project: dict[int | None, list[Card]] = defaultdict(list)
    for session in live:
        card = cards[session.id]
        parent = session.parent_session_id
        if parent in live_ids:
            cards[parent].subagents.append(card)
            continue
        mapping = projects_of.get(session.id)
        key = mapping.target if mapping is not None and mapping.state == "mapped" else None
        by_project[key].append(card)
    groups = []
    for project_id in sorted(
        (k for k in by_project if k is not None),
        key=lambda k: (projects[k].name.lower() if k in projects else "", k),
    ):
        project = projects.get(project_id)
        groups.append(
            ProjectGroup(
                project={
                    "id": project_id,
                    "name": project.name if project else f"#{project_id}",
                    "status": project.status if project else None,
                    "health": project.health if project else None,
                },
                reason="mapped",
                agents=agent_rows.get(project_id, []),
                sessions=by_project[project_id],
            )
        )
    if by_project.get(None):
        groups.append(
            ProjectGroup(project=None, reason="unmapped", agents=[], sessions=by_project[None])
        )
    return groups


MAX_ACTIVITY = 2000
RANGES = (7, 30)


@router.get(
    "/projects/activity", response_model=ProjectActivity, summary="Sessions by project over time"
)
def activity(
    db: Db,
    days: Annotated[int, Query(description="How far back to look: 7 or 30 days.")] = 7,
    connection_id: Annotated[
        int | None, Query(description="Default: the first connection.")
    ] = None,
) -> dict[str, Any]:
    """Every session active in the last 7 or 30 days, in the lane of the project it maps to.

    A session counts as active in the range when its last activity falls in it. The
    project is the one the resolver decides from the session's links; a conflict or no
    evidence leaves it in the lane of sessions mapped to no project.
    """
    if days not in RANGES:
        raise HTTPException(status_code=422, detail="days must be 7 or 30")
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    connections = list(
        db.scalars(select(Connection).where(Connection.kind == KIND).order_by(Connection.id))
    )
    if connection_id is not None and connection_id not in {c.id for c in connections}:
        raise HTTPException(status_code=404, detail="no such connection")
    row = next((c for c in connections if connection_id in (None, c.id)), None)
    last = func.coalesce(Session.last_activity_at, Session.started_at)
    where = last >= since
    total = db.scalar(select(func.count()).select_from(Session).where(where)) or 0
    sessions = list(
        db.scalars(
            select(Session)
            .where(where)
            .order_by(last.desc(), Session.id.desc())
            .limit(MAX_ACTIVITY)
        )
    )
    ids = [s.id for s in sessions]
    mapped: dict[int, int] = {}
    projects: dict[int, WcProject] = {}
    if row is not None:
        found: dict[int, list[Candidate]] = defaultdict(list)
        for link in db.scalars(
            select(ProjectLink).where(
                ProjectLink.connection_id == row.id, ProjectLink.session_id.in_(ids or [-1])
            )
        ):
            found[link.session_id].append(
                Candidate(link.basis.value, link.project_remote_id, link.evidence or {})
            )
        for session_id, candidates in found.items():
            decided = decide(candidates, PROJECT_ORDER)
            if decided.state == "mapped" and decided.target is not None:
                mapped[session_id] = decided.target
        projects = {
            p.remote_id: p
            for p in db.scalars(select(WcProject).where(WcProject.connection_id == row.id))
        }
    lanes: dict[int | None, list[ActivitySession]] = defaultdict(list)
    for session in sessions:
        start = session.started_at or session.last_activity_at
        end = session.ended_at or session.last_activity_at or start
        lanes[mapped.get(session.id)].append(
            ActivitySession(
                id=session.id,
                harness=session.harness,
                title=session.title,
                state=session.state,
                parent_session_id=session.parent_session_id,
                start=start,
                end=end,
            )
        )
    for items in lanes.values():
        items.sort(key=lambda item: (item.start or now, item.id))

    def named(project_id: int) -> dict[str, Any]:
        project = projects.get(project_id)
        return {"id": project_id, "name": project.name if project else f"#{project_id}"}

    ordered = sorted(
        (k for k in lanes if k is not None), key=lambda k: (named(k)["name"].lower(), k)
    )
    return {
        "connection": {"id": row.id, "name": row.name, "server": row.base_url} if row else None,
        "days": days,
        "since": since,
        "until": now,
        "lanes": [ProjectLane(project=named(k), sessions=lanes[k]) for k in ordered]
        + ([ProjectLane(project=None, sessions=lanes[None])] if lanes.get(None) else []),
        "omitted": total - len(sessions),
    }
