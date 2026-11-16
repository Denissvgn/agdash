"""Link sessions to WorkChord runs and tasks, each link with the evidence it rests on.

Four kinds of evidence, strongest first:

1. ``tool_call`` — the session's transcript holds the result of a WorkChord work
   call (begin, renew, submit or fail its work, or start a run), through the MCP
   server or the HTTP API. The result names the run, the task and the
   assignment. Exact.
2. ``trace_id`` — a run's actor declared this harness session when it began
   (``metadata.harness`` on the run). Exact, though WorkChord does not check the
   declaration.
3. ``mcp_config`` — an agent process of the session was started with the
   server's address among its arguments. Links to the server, not to a task.
4. ``heuristic`` — the session's branch or worktree names a task's key, and the
   session overlaps one of that task's runs. A suggestion only: it is stored
   only while the session has no exact link to that server, and removed once
   it has one.

Nothing is guessed beyond that: a session with no evidence gets no link. Only
agdash's own store is read and written; the WorkChord copy comes from the
mirror.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import String, cast, delete, or_, select
from sqlalchemy.orm import Session as DbSession

from agdash.db.models import (
    Connection,
    Event,
    RunLink,
    Session,
    SessionProcess,
    WcRun,
    WcTask,
)
from agdash.vocab import EventKind, Harness, RunLinkBasis

CONFIDENCE = {
    RunLinkBasis.TOOL_CALL: 1.0,
    RunLinkBasis.TRACE_ID: 0.95,
    RunLinkBasis.MCP_CONFIG: 0.5,
    RunLinkBasis.HEURISTIC: 0.3,
}
RANK = {basis: rank for rank, basis in enumerate(RunLinkBasis)}
EXACT = (RunLinkBasis.TOOL_CALL, RunLinkBasis.TRACE_ID)
# WorkChord's MCP tools whose results name a run. A harness prefixes the server name
# the user chose, such as mcp__workchord__agent_begin_my_work, so only the end counts.
WORK_TOOLS = (
    "agent_begin_my_work",
    "agent_renew_my_work",
    "agent_submit_my_work",
    "agent_fail_my_work",
    "agent_start_run",
)
WORK_ROUTES = tuple(f"/api/agent/me/work/{step}" for step in ("begin", "renew", "submit", "fail"))
TOOL_BATCH = 500
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Claimed:
    """What a work call's result says about the run it belongs to."""

    run_id: int
    task_id: int | None
    assignment_id: int | None
    actor_id: int | None
    claim_generation: int | None = None
    claim_expires_at: str | None = None


@dataclass
class Linked:
    """How many links one pass added, kept or removed, by basis."""

    added: dict[str, int] = field(default_factory=dict)
    removed: int = 0
    unplaced: int = 0  # work calls that could not be tied to one server
    last_event_id: int = 0

    def count(self, basis: RunLinkBasis) -> None:
        self.added[basis.value] = self.added.get(basis.value, 0) + 1


# -- reading a work call's result ---------------------------------------------------


def _documents(output: Any) -> Iterable[dict[str, Any]]:
    """The JSON objects a tool's output holds, however the harness wrapped them."""
    if isinstance(output, dict):
        yield output
        for key in ("structuredContent", "result"):
            if isinstance(output.get(key), dict):
                yield output[key]
        if isinstance(output.get("content"), list):
            yield from _documents(output["content"])
    elif isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                yield from _documents(item["text"])
            else:
                yield from _documents(item)
    elif isinstance(output, str):
        text = output.strip()
        start = text.find("{")
        if start < 0:
            return
        try:
            yield from _documents(json.loads(text[start:]))
        except ValueError:
            return


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def read_work_result(output: Any) -> Claimed | None:
    """The run a work call's result names, or None when it names none."""
    for document in _documents(output):
        run = document.get("run") if isinstance(document.get("run"), dict) else None
        if run is None and _int(document.get("id")) and "actor_id" in document:
            run = document  # a run started directly is the run itself
        if run is None or _int(run.get("id")) is None:
            continue
        assignment = document.get("assignment")
        assignment = assignment if isinstance(assignment, dict) else {}
        expires = document.get("claim_expires_at")
        return Claimed(
            run_id=run["id"],
            task_id=_int(run.get("task_id")) or _int(assignment.get("task_id")),
            assignment_id=_int(run.get("assignment_id")) or _int(assignment.get("id")),
            actor_id=_int(run.get("actor_id")) or _int(assignment.get("actor_id")),
            claim_generation=_int(document.get("claim_generation")),
            claim_expires_at=expires if isinstance(expires, str) else None,
        )
    return None


def _work_tool(name: str | None) -> str | None:
    if not name:
        return None
    short = re.split(r"__|\.|/", name)[-1]
    return short if short in WORK_TOOLS else None


def _routes_in(value: Any) -> list[str]:
    text = json.dumps(value) if not isinstance(value, str) else value
    return [route for route in WORK_ROUTES if route in text]


# -- storing ------------------------------------------------------------------------


def _store(
    db: DbSession,
    session_id: int,
    connection_id: int,
    basis: RunLinkBasis,
    evidence: dict[str, Any],
    *,
    run_id: int | None = None,
    task_id: int | None = None,
    assignment_id: int | None = None,
) -> bool:
    """Add a link, or refresh the one already there. True when it is new."""
    found = db.scalars(
        select(RunLink).where(
            RunLink.session_id == session_id,
            RunLink.connection_id == connection_id,
            RunLink.basis == basis,
            RunLink.run_remote_id.is_(None) if run_id is None else RunLink.run_remote_id == run_id,
            RunLink.task_remote_id.is_(None)
            if task_id is None
            else RunLink.task_remote_id == task_id,
        )
    ).first()
    if found is not None:
        found.evidence = evidence
        found.assignment_remote_id = assignment_id or found.assignment_remote_id
        return False
    db.add(
        RunLink(
            session_id=session_id,
            connection_id=connection_id,
            run_remote_id=run_id,
            task_remote_id=task_id,
            assignment_remote_id=assignment_id,
            basis=basis,
            confidence=CONFIDENCE[basis],
            evidence=evidence,
        )
    )
    db.flush()
    return True


def _netloc(address: str) -> str:
    return urlsplit(address).netloc.lower()


def _addresses(connection: Connection) -> set[str]:
    """How the server's address may be written: as given, and its loopback aliases."""
    netloc = _netloc(connection.base_url)
    found = {connection.base_url.rstrip("/").lower(), netloc}
    host, _, port = netloc.rpartition(":")
    if host.strip("[]") in LOOPBACK and port:
        found |= {f"{alias}:{port}" for alias in ("127.0.0.1", "localhost", "[::1]")}
    return found


# -- the four tiers -----------------------------------------------------------------


class Linker:
    """Finds and stores links for every WorkChord connection.

    Work calls are read once: each pass reads only the tool calls recorded since
    the last one.
    """

    def __init__(self) -> None:
        self._read_up_to: dict[int, int] = {}

    def run(self, db: DbSession, connections: list[Connection]) -> Linked:
        linked = Linked()
        if not connections:
            return linked
        start = min(self._read_up_to.get(c.id, 0) for c in connections)
        linked.last_event_id = self._tool_calls(db, connections, start, linked)
        for connection in connections:
            self._read_up_to[connection.id] = linked.last_event_id
            self._declared(db, connection, linked)
            self._process_arguments(db, connection, linked)
            self._suggestions(db, connection, linked)
        db.flush()
        return linked

    # 1. tool_call

    def _connection_for(
        self, db: DbSession, connections: list[Connection], claimed: Claimed, call: Any
    ) -> Connection | None:
        if len(connections) == 1:
            return connections[0]
        named = [c for c in connections if any(a in json.dumps(call) for a in _addresses(c))]
        if len(named) == 1:
            return named[0]
        holding = [
            c
            for c in connections
            if db.scalar(
                select(WcRun.id).where(
                    WcRun.connection_id == c.id, WcRun.remote_id == claimed.run_id
                )
            )
            or (
                claimed.task_id is not None
                and db.scalar(
                    select(WcTask.id).where(
                        WcTask.connection_id == c.id, WcTask.remote_id == claimed.task_id
                    )
                )
            )
        ]
        return holding[0] if len(holding) == 1 else None

    def _tool_calls(
        self, db: DbSession, connections: list[Connection], after: int, linked: Linked
    ) -> int:
        name = Event.payload["name"].as_string()
        matches = or_(
            *(name.like(f"%{tool}") for tool in WORK_TOOLS),
            *(cast(Event.payload, String).like(f"%{route}%") for route in WORK_ROUTES),
        )
        last = after
        while True:
            calls = db.execute(
                select(Event.id, Event.session_id, Event.sequence, Event.payload)
                .where(Event.kind == EventKind.TOOL_CALL, Event.id > last, matches)
                .order_by(Event.id)
                .limit(TOOL_BATCH)
            ).all()
            if not calls:
                return last
            for event_id, session_id, sequence, payload in calls:
                last = event_id
                self._tool_call(db, connections, session_id, sequence, payload or {}, linked)

    def _tool_call(self, db, connections, session_id, sequence, payload, linked) -> None:
        tool = _work_tool(payload.get("name"))
        routes = _routes_in(payload.get("input"))
        call_id = payload.get("call_id")
        if not (tool or routes) or not isinstance(call_id, str):
            return
        results = db.scalars(
            select(Event.payload).where(
                Event.session_id == session_id,
                Event.kind == EventKind.TOOL_RESULT,
                Event.sequence > sequence,
                Event.payload["call_id"].as_string() == call_id,
            )
        ).all()
        for result in results:
            if (result or {}).get("is_error"):
                continue
            claimed = read_work_result((result or {}).get("output"))
            if claimed is None:
                continue
            connection = self._connection_for(db, connections, claimed, payload.get("input"))
            if connection is None:
                linked.unplaced += 1
                return
            evidence = {
                "call": tool or "http",
                "sequence": sequence,
                "actor_id": claimed.actor_id,
                "claim_generation": claimed.claim_generation,
                "claim_expires_at": claimed.claim_expires_at,
            }
            if _store(
                db,
                session_id,
                connection.id,
                RunLinkBasis.TOOL_CALL,
                {k: v for k, v in evidence.items() if v is not None},
                run_id=claimed.run_id,
                task_id=claimed.task_id,
                assignment_id=claimed.assignment_id,
            ):
                linked.count(RunLinkBasis.TOOL_CALL)
            return

    # 2. trace_id: the harness session the run declared

    def _declared(self, db: DbSession, connection: Connection, linked: Linked) -> None:
        runs = db.execute(
            select(WcRun).where(
                WcRun.connection_id == connection.id, WcRun.harness_session_id.is_not(None)
            )
        ).scalars()
        known = {harness.value for harness in Harness}
        for run in runs:
            kind = (run.harness_kind or "").lower().replace("-", "_").replace(".", "_")
            query = select(Session.id).where(Session.native_id == run.harness_session_id)
            if kind in known:
                query = query.where(Session.harness == kind)
            for session_id in db.scalars(query):
                if _store(
                    db,
                    session_id,
                    connection.id,
                    RunLinkBasis.TRACE_ID,
                    {"harness_kind": run.harness_kind},
                    run_id=run.remote_id,
                    task_id=run.task_remote_id,
                    assignment_id=run.assignment_remote_id,
                ):
                    linked.count(RunLinkBasis.TRACE_ID)

    # 3. mcp_config: the server's address in an agent process's arguments

    def _process_arguments(self, db: DbSession, connection: Connection, linked: Linked) -> None:
        addresses = _addresses(connection)
        rows = db.execute(
            select(SessionProcess.session_id, SessionProcess.argv_redacted).where(
                or_(*(cast(SessionProcess.argv_redacted, String).like(f"%{a}%") for a in addresses))
            )
        ).all()
        for session_id, argv in rows:
            if not any(
                address in str(part).lower() for part in argv or [] for address in addresses
            ):
                continue
            if _store(
                db,
                session_id,
                connection.id,
                RunLinkBasis.MCP_CONFIG,
                {"seen_in": "process arguments"},
            ):
                linked.count(RunLinkBasis.MCP_CONFIG)

    # 4. heuristic: a task key in the branch or worktree, and a run at the same time

    def _suggestions(self, db: DbSession, connection: Connection, linked: Linked) -> None:
        exact = set(
            db.scalars(
                select(RunLink.session_id).where(
                    RunLink.connection_id == connection.id, RunLink.basis.in_(EXACT)
                )
            )
        )
        if exact:
            linked.removed += db.execute(
                delete(RunLink).where(
                    RunLink.connection_id == connection.id,
                    RunLink.basis == RunLinkBasis.HEURISTIC,
                    RunLink.session_id.in_(exact),
                )
            ).rowcount
        keys = {
            key: task_id
            for task_id, key in db.execute(
                select(WcTask.remote_id, WcTask.external_key).where(
                    WcTask.connection_id == connection.id, WcTask.external_key.is_not(None)
                )
            )
            if key and len(key) >= 3
        }
        if not keys:
            return
        pattern = re.compile(
            r"(?<![A-Za-z0-9])("
            + "|".join(re.escape(k) for k in sorted(keys, key=len, reverse=True))
            + r")(?![0-9])",
            re.IGNORECASE,
        )
        by_lower = {key.lower(): task_id for key, task_id in keys.items()}
        sessions = db.scalars(
            select(Session).where(
                or_(Session.branch.is_not(None), Session.worktree.is_not(None)),
                Session.id.not_in(exact or {-1}),
            )
        )
        for session in sessions:
            named = {
                by_lower[m.group(1).lower()]
                for text in (session.branch, session.worktree)
                if text
                for m in pattern.finditer(text)
            }
            for task_id in named:
                for run in self._overlapping(db, connection.id, task_id, session):
                    if _store(
                        db,
                        session.id,
                        connection.id,
                        RunLinkBasis.HEURISTIC,
                        {"matched": "branch or worktree", "task_key": _key_of(keys, task_id)},
                        run_id=run.remote_id,
                        task_id=task_id,
                        assignment_id=run.assignment_remote_id,
                    ):
                        linked.count(RunLinkBasis.HEURISTIC)

    @staticmethod
    def _overlapping(db: DbSession, connection_id: int, task_id: int, session: Session):
        start = session.started_at
        end = session.ended_at or session.last_activity_at or start
        if start is None or end is None:
            return []
        runs = db.scalars(
            select(WcRun).where(
                WcRun.connection_id == connection_id,
                WcRun.task_remote_id == task_id,
                WcRun.started_at.is_not(None),
            )
        )
        return [run for run in runs if run.started_at <= end and _run_end(run) >= start]


def _run_end(run: WcRun) -> datetime:
    return run.ended_at or run.heartbeat_at or run.started_at


def _key_of(keys: dict[str, int], task_id: int) -> str:
    return next(key for key, found in keys.items() if found == task_id)


def strongest(links: Iterable[RunLink]) -> RunLink | None:
    """The link resting on the strongest evidence; a suggestion never outranks an exact one."""
    ordered = sorted(links, key=lambda link: (RANK[link.basis], -link.confidence, link.id or 0))
    return ordered[0] if ordered else None
