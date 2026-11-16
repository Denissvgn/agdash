"""What each session maps to in WorkChord, with the evidence, and the Unmapped queue.

* ``/api/sessions/{id}/mapping``: per WorkChord connection, the session's runs
  and tasks, its actor and its project, each with its state (mapped, conflict
  or unmapped), its basis and every piece of evidence behind it.
* ``/api/mapping/unmapped``: top-level sessions with no project on a
  connection, because nothing points at one or because the evidence disagrees.

Read-only, like the rest of the API. Rules and pins are made with
``agdash connect``.
"""

from collections import defaultdict
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from agdash.api.routes import Db
from agdash.connectors.links import RANK
from agdash.connectors.resolver import (
    ACTOR_ORDER,
    PROJECT_ORDER,
    Candidate,
    Mapping,
    decide,
)
from agdash.connectors.workchord import KIND
from agdash.db.models import (
    Connection,
    IdentityLink,
    ProjectLink,
    RunLink,
    Session,
    WcActor,
    WcProject,
    WcTask,
)
from agdash.vocab import RunLinkBasis, SessionState

router = APIRouter()

State = Literal["mapped", "conflict", "unmapped"]
QUEUE_LIMIT = 500


class Evidence(BaseModel):
    basis: str
    target: int = Field(description="The actor or project id on the server.")
    name: str | None
    evidence: dict[str, Any]


class Resolution(BaseModel):
    state: State
    target: int | None = Field(description="Null unless mapped.")
    name: str | None
    basis: str | None
    suggestion: int | None = Field(
        description="In a conflict, what the strongest evidence points at, if it agrees."
    )
    evidence: list[Evidence] = Field(description="Strongest basis first.")


class RunOut(BaseModel):
    basis: RunLinkBasis
    confidence: float
    suggestion: bool = Field(description="True for a heuristic link, shown as a guess.")
    run_id: int | None
    task_id: int | None
    task_key: str | None
    task_title: str | None
    evidence: dict[str, Any]


class ConnectionMapping(BaseModel):
    connection_id: int
    server: str
    runs: list[RunOut]
    actor: Resolution
    project: Resolution


class SessionMapping(BaseModel):
    session_id: int
    connections: list[ConnectionMapping]


class QueueItem(BaseModel):
    session_id: int
    harness: str
    title: str | None
    cwd: str | None
    state: SessionState
    last_activity_at: datetime | None
    connection_id: int
    reason: Literal["no evidence", "conflicting evidence"]
    suggestion: int | None
    evidence: list[Evidence] = Field(description="What points where, strongest basis first.")


class Queue(BaseModel):
    items: list[QueueItem] = Field(description="Most recently active first.")
    total: int


def _connections(db) -> list[Connection]:
    return list(
        db.scalars(select(Connection).where(Connection.kind == KIND).order_by(Connection.id))
    )


def _named(db, model, connection_id: int, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    label = model.name
    if model is WcActor:
        label = func.coalesce(WcActor.display_name, WcActor.name)
    return dict(
        db.execute(
            select(model.remote_id, label).where(
                model.connection_id == connection_id, model.remote_id.in_(ids)
            )
        ).all()
    )


def _resolution(mapping: Mapping, names: dict[int, str]) -> Resolution:
    return Resolution(
        state=mapping.state,
        target=mapping.target,
        name=names.get(mapping.target) if mapping.target is not None else None,
        basis=mapping.basis,
        suggestion=mapping.suggestion,
        evidence=[
            Evidence(basis=c.basis, target=c.target, name=names.get(c.target), evidence=c.evidence)
            for c in mapping.candidates
        ],
    )


@router.get(
    "/sessions/{session_id}/mapping",
    response_model=SessionMapping,
    summary="A session's WorkChord runs, actor and project",
)
def session_mapping(db: Db, session_id: int) -> dict[str, Any]:
    """Per connection, with the state and evidence of each mapping."""
    if db.get(Session, session_id) is None:
        raise HTTPException(status_code=404, detail="no such session")
    found = []
    for connection in _connections(db):
        cid = connection.id
        actors = db.scalars(
            select(IdentityLink).where(
                IdentityLink.session_id == session_id, IdentityLink.connection_id == cid
            )
        ).all()
        projects = db.scalars(
            select(ProjectLink).where(
                ProjectLink.session_id == session_id, ProjectLink.connection_id == cid
            )
        ).all()
        runs = sorted(
            db.scalars(
                select(RunLink).where(
                    RunLink.session_id == session_id, RunLink.connection_id == cid
                )
            ),
            key=lambda link: (RANK[link.basis], -link.confidence, link.id),
        )
        tasks = {
            row.remote_id: row
            for row in db.scalars(
                select(WcTask).where(
                    WcTask.connection_id == cid,
                    WcTask.remote_id.in_({r.task_remote_id for r in runs} - {None} or {-1}),
                )
            )
        }
        actor = decide(
            (Candidate(r.basis.value, r.actor_remote_id, r.evidence or {}) for r in actors),
            ACTOR_ORDER,
        )
        project = decide(
            (Candidate(r.basis.value, r.project_remote_id, r.evidence or {}) for r in projects),
            PROJECT_ORDER,
        )
        found.append(
            ConnectionMapping(
                connection_id=cid,
                server=connection.base_url,
                runs=[
                    RunOut(
                        basis=r.basis,
                        confidence=r.confidence,
                        suggestion=r.basis is RunLinkBasis.HEURISTIC,
                        run_id=r.run_remote_id,
                        task_id=r.task_remote_id,
                        task_key=tasks[r.task_remote_id].external_key
                        if r.task_remote_id in tasks
                        else None,
                        task_title=tasks[r.task_remote_id].title
                        if r.task_remote_id in tasks
                        else None,
                        evidence=r.evidence or {},
                    )
                    for r in runs
                ],
                actor=_resolution(
                    actor, _named(db, WcActor, cid, {r.actor_remote_id for r in actors})
                ),
                project=_resolution(
                    project, _named(db, WcProject, cid, {r.project_remote_id for r in projects})
                ),
            )
        )
    return {"session_id": session_id, "connections": found}


@router.get("/mapping/unmapped", response_model=Queue, summary="Sessions with no project")
def unmapped(
    db: Db,
    connection_id: Annotated[int | None, Query(description="One connection; default all.")] = None,
    limit: Annotated[int, Query(ge=1, le=QUEUE_LIMIT)] = 100,
) -> dict[str, Any]:
    """Top-level sessions with no project on a connection, or with disagreeing evidence."""
    connections = [c for c in _connections(db) if connection_id in (None, c.id)]
    roots = db.execute(
        select(
            Session.id,
            Session.harness,
            Session.title,
            Session.cwd,
            Session.state,
            Session.last_activity_at,
        ).where(Session.parent_session_id.is_(None))
    ).all()
    items: list[QueueItem] = []
    for connection in connections:
        names = dict(
            db.execute(
                select(WcProject.remote_id, WcProject.name).where(
                    WcProject.connection_id == connection.id
                )
            ).all()
        )
        evidence = defaultdict(list)
        for row in db.scalars(
            select(ProjectLink).where(ProjectLink.connection_id == connection.id)
        ):
            evidence[row.session_id].append(
                Candidate(row.basis.value, row.project_remote_id, row.evidence or {})
            )
        for root in roots:
            mapping = decide(evidence.get(root.id, []), PROJECT_ORDER)
            if mapping.state == "mapped":
                continue
            items.append(
                QueueItem(
                    session_id=root.id,
                    harness=root.harness,
                    title=root.title,
                    cwd=root.cwd,
                    state=root.state,
                    last_activity_at=root.last_activity_at,
                    connection_id=connection.id,
                    reason="no evidence" if mapping.state == "unmapped" else "conflicting evidence",
                    suggestion=mapping.suggestion,
                    evidence=[
                        Evidence(
                            basis=c.basis,
                            target=c.target,
                            name=names.get(c.target),
                            evidence=c.evidence,
                        )
                        for c in mapping.candidates
                    ],
                )
            )
    ordered = sorted(
        items,
        key=lambda item: (
            item.last_activity_at is not None,
            item.last_activity_at.timestamp() if item.last_activity_at else 0.0,
            item.session_id,
        ),
        reverse=True,
    )
    return {"items": ordered[:limit], "total": len(ordered)}
