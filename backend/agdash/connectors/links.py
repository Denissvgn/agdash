"""Link sessions to WorkChord runs and tasks, each link with the evidence it rests on.

Four kinds of evidence, strongest first:

1. ``tool_call`` — the session's transcript holds the result of a WorkChord work
   call (begin, renew, submit or fail its work, or start a run), through the MCP
   server, the HTTP API, or a script the harness runs that calls the MCP tool
   (Codex's code mode). The result names the run, the task and the
   assignment. Exact. When the result does not name the run, as when a script
   prints only part of it, the run or assignment id the call itself sends
   picks the run from the WorkChord copy, provided that run was live when the
   call was made.
2. ``trace_id`` — a run's actor declared this harness session when it began
   (``metadata.harness`` on the run). Exact, though WorkChord does not check the
   declaration.
3. ``mcp_config`` — an agent process of the session was started with the
   server's address among its arguments. Links to the server, not to a task.
4. ``heuristic`` — a suggestion only, stored while the session has no exact link
   to that server and removed once it has one. Two things suggest a link. The
   session's branch or worktree names a task's key, and the session overlaps
   one of that task's runs. Or a run declares a harness session that nothing on
   this host is named after, and exactly one session known only by its process
   was running, a short while, when that declared session began: a harness
   told to keep no record leaves no id to match, only the moment.

Nothing is guessed beyond that: a session with no evidence gets no link. Only
agdash's own store is read and written; the WorkChord copy comes from the
mirror.
"""

import json
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import String, cast, delete, or_, select
from sqlalchemy.orm import Session as DbSession

from agdash.collector.processes import PROCESS_SESSION_PREFIX
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
# What a suggestion from the moment a run's session began says it matched.
STARTED_WITH = "started with the run"
# How long before that moment the process may have started: it starts, then its session.
STARTED_WITHIN = timedelta(seconds=120)
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
# A script that calls a work tool, such as Codex's code mode running
# tools.mcp__workchord__agent_begin_my_work({...}) inside its one exec tool.
SCRIPTED = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(WORK_TOOLS) + r")\s*\(")
# The run or assignment a call sends, whether as JSON, a script's object or a keyword.
SENT_IDS = re.compile(r"""\b(run_id|assignment_id)["']?\s*[:=]\s*(\d+)\b""")
TOOL_BATCH = 500
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# A recent call whose result is not recorded yet, or whose run is not in the copy yet,
# is tried again on each pass until it is this old.
RETRY_WINDOW = timedelta(hours=1)
# How far a call may fall outside its run: a begin is recorded just before its run starts.
CALL_SLACK = timedelta(minutes=10)
JSON_STARTS = 20  # places in one text where a JSON object is looked for


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
        # A script may print a result as JSON text inside an object of its own.
        for value in output.values():
            if isinstance(value, str) and value.lstrip().startswith("{"):
                yield from _documents(value)
    elif isinstance(output, list):
        for item in output:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                yield from _documents(item["text"])
            else:
                yield from _documents(item)
    elif isinstance(output, str):
        for found in _json_objects(output):
            yield from _documents(found)


def _json_objects(text: str) -> Iterable[Any]:
    """The JSON objects in a text: one after a preamble such as an HTTP status or a
    script's banner, and any more that start lines of their own."""
    starts = sorted({text.find("{")} | {m.end() for m in re.finditer(r"\n[ \t]*(?=\{)", text)})
    decoder = json.JSONDecoder()
    done = 0
    for start in [s for s in starts if s >= 0][:JSON_STARTS]:
        if start < done:
            continue
        try:
            found, done = decoder.raw_decode(text, start)
        except ValueError:
            continue
        yield found


def _reports_error(output: Any) -> bool:
    """Whether a call's output says it failed, as a script printing the result may."""
    return any(
        document.get("isError") is True or document.get("is_error") is True
        for document in _documents(output)
    )


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


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _routes_in(value: Any) -> list[str]:
    text = _text(value)
    return [route for route in WORK_ROUTES if route in text]


def _scripted_tool(value: Any) -> str | None:
    """The first work tool a script in a call's input calls, if any."""
    found = SCRIPTED.search(_text(value))
    return found.group(1) if found else None


def _sent_ids(value: Any) -> dict[str, set[int]]:
    sent: dict[str, set[int]] = {}
    for key, number in SENT_IDS.findall(_text(value)):
        sent.setdefault(key, set()).add(int(number))
    return sent


def _live_at(run: WcRun, at: datetime | None) -> bool:
    """Whether a call made at ``at`` falls within the run, give or take the slack."""
    if at is None or run.started_at is None:
        return False
    if run.started_at - CALL_SLACK > at:
        return False
    return run.ended_at is None or run.ended_at + CALL_SLACK >= at


def _with_task(db: DbSession, connection: Connection, claimed: Claimed) -> Claimed:
    """A result that names only the run, as a submit's may, takes its task from the copy."""
    if claimed.task_id is not None:
        return claimed
    run = db.scalars(
        select(WcRun).where(WcRun.connection_id == connection.id, WcRun.remote_id == claimed.run_id)
    ).first()
    if run is None or run.task_remote_id is None:
        return claimed
    return replace(
        claimed,
        task_id=run.task_remote_id,
        assignment_id=claimed.assignment_id or run.assignment_remote_id,
    )


def _echoes(output: Any, run: WcRun) -> bool:
    """Whether a call's output names the run's assignment or task by key, as WorkChord's
    answer does even when a script prints only part of it. Code that merely mentions a
    work tool, such as a test being edited, prints no such thing."""
    ids = "|".join(str(i) for i in (run.assignment_remote_id, run.task_remote_id) if i)
    if not ids:
        return False
    keyed = rf"""\b(?:id|assignment_id|task_id)\\?["']?\s*:\s*(?:{ids})\b"""
    return re.search(keyed, _text(output)) is not None


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
    the last one, and tries again the few still waiting for their result or for
    their run to reach the WorkChord copy.
    """

    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._read_up_to: dict[int, int] = {}
        self._waiting: set[int] = set()  # call event ids
        self._clock = clock

    def _recent(self, at: datetime | None) -> bool:
        return at is not None and at >= self._clock() - RETRY_WINDOW

    def run(self, db: DbSession, connections: list[Connection]) -> Linked:
        linked = Linked()
        if not connections:
            return linked
        self._retry(db, connections, linked)
        start = min(self._read_up_to.get(c.id, 0) for c in connections)
        linked.last_event_id = self._tool_calls(db, connections, start, linked)
        for connection in connections:
            self._read_up_to[connection.id] = linked.last_event_id
            self._declared(db, connection, linked)
            self._process_arguments(db, connection, linked)
            self._suggestions(db, connection, linked)
            self._started_with(db, connection, linked)
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

    _CALL = (Event.id, Event.session_id, Event.sequence, Event.occurred_at, Event.payload)

    def _tool_calls(
        self, db: DbSession, connections: list[Connection], after: int, linked: Linked
    ) -> int:
        name = Event.payload["name"].as_string()
        text = cast(Event.payload, String)
        matches = or_(
            *(name.like(f"%{tool}") for tool in WORK_TOOLS),
            *(text.like(f"%{tool}%") for tool in WORK_TOOLS),
            *(text.like(f"%{route}%") for route in WORK_ROUTES),
        )
        last = after
        while True:
            calls = db.execute(
                select(*self._CALL)
                .where(Event.kind == EventKind.TOOL_CALL, Event.id > last, matches)
                .order_by(Event.id)
                .limit(TOOL_BATCH)
            ).all()
            if not calls:
                return last
            for event_id, session_id, sequence, at, payload in calls:
                last = event_id
                settled = self._tool_call(
                    db, connections, session_id, sequence, at, payload or {}, linked
                )
                if not settled and self._recent(at):
                    self._waiting.add(event_id)

    def _retry(self, db: DbSession, connections: list[Connection], linked: Linked) -> None:
        if not self._waiting:
            return
        calls = db.execute(select(*self._CALL).where(Event.id.in_(self._waiting))).all()
        self._waiting = {
            event_id
            for event_id, session_id, sequence, at, payload in calls
            if self._recent(at)
            and not self._tool_call(
                db, connections, session_id, sequence, at, payload or {}, linked
            )
        }

    def _tool_call(self, db, connections, session_id, sequence, at, payload, linked) -> bool:
        """Link one work call. False while it may still link: its result is not recorded
        yet, or the run it names is not in the copy yet."""
        call = payload.get("input")
        tool = _work_tool(payload.get("name")) or _scripted_tool(call)
        call_id = payload.get("call_id")
        if not (tool or _routes_in(call)) or not isinstance(call_id, str):
            return True
        results = db.scalars(
            select(Event.payload).where(
                Event.session_id == session_id,
                Event.kind == EventKind.TOOL_RESULT,
                Event.sequence > sequence,
                Event.payload["call_id"].as_string() == call_id,
            )
        ).all()
        if not results:
            return False
        settled = True
        for result in results:
            output = (result or {}).get("output")
            if (result or {}).get("is_error") or _reports_error(output):
                continue
            claimed = read_work_result(output)
            evidence: dict[str, Any] = {"call": tool or "http", "sequence": sequence}
            if claimed is not None:
                connection = self._connection_for(db, connections, claimed, call)
                if connection is None:
                    linked.unplaced += 1
                    return True
                evidence |= {
                    "actor_id": claimed.actor_id,
                    "claim_generation": claimed.claim_generation,
                    "claim_expires_at": claimed.claim_expires_at,
                }
                claimed = _with_task(db, connection, claimed)
            else:
                placed = self._sent_run(db, connections, call, at, output)
                if placed is None:
                    # The run the call sends may reach the copy on a later pass.
                    settled = settled and not _sent_ids(call)
                    continue
                connection, run = placed
                claimed = Claimed(
                    run_id=run.remote_id,
                    task_id=run.task_remote_id,
                    assignment_id=run.assignment_remote_id,
                    actor_id=run.actor_remote_id,
                )
                evidence |= {"actor_id": run.actor_remote_id, "read_from": "call input"}
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
            return True
        return settled

    @staticmethod
    def _sent_run(
        db: DbSession,
        connections: list[Connection],
        call: Any,
        at: datetime | None,
        output: Any,
    ) -> tuple[Connection, WcRun] | None:
        """The one run a call sends the id of, or the run of the one assignment it sends,
        live when the call was made and named in its output. None when there is no such
        run, or more than one."""
        sent = _sent_ids(call)
        for key, column in (
            ("run_id", WcRun.remote_id),
            ("assignment_id", WcRun.assignment_remote_id),
        ):
            ids = sent.get(key, set())
            if len(ids) != 1:
                continue
            found = [
                (connection, run)
                for connection in connections
                for run in db.scalars(
                    select(WcRun).where(
                        WcRun.connection_id == connection.id, column == next(iter(ids))
                    )
                )
                if _live_at(run, at) and _echoes(output, run)
            ]
            return found[0] if len(found) == 1 else None
        return None

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

    def _started_with(self, db: DbSession, connection: Connection, linked: Linked) -> None:
        """Suggest the one process-named session that ran when a run's session began."""
        exact = db.execute(
            select(RunLink.session_id, RunLink.run_remote_id).where(
                RunLink.connection_id == connection.id, RunLink.basis.in_(EXACT)
            )
        ).all()
        linked_sessions = {session_id for session_id, _ in exact}
        linked_runs = {run_id for _, run_id in exact if run_id is not None}
        stale = [
            link
            for link in db.scalars(
                select(RunLink).where(
                    RunLink.connection_id == connection.id,
                    RunLink.basis == RunLinkBasis.HEURISTIC,
                    RunLink.run_remote_id.in_(linked_runs or {-1}),
                )
            )
            if (link.evidence or {}).get("matched") == STARTED_WITH
        ]
        for link in stale:  # the run has found its session since
            db.delete(link)
        linked.removed += len(stale)

        known = {harness.value for harness in Harness}
        candidates: dict[int, list[tuple[Session, datetime]]] = {}
        runs: dict[int, WcRun] = {}
        for run in db.scalars(
            select(WcRun).where(
                WcRun.connection_id == connection.id,
                WcRun.harness_session_id.is_not(None),
                WcRun.remote_id.not_in(linked_runs or {-1}),
            )
        ):
            kind = (run.harness_kind or "").lower().replace("-", "_").replace(".", "_")
            began = _began(run)
            if kind not in known or began is None:
                continue
            found = db.scalars(
                select(Session).where(
                    Session.harness == kind,
                    Session.native_id.like(f"{PROCESS_SESSION_PREFIX}%"),
                    Session.started_at >= began - STARTED_WITHIN,
                    Session.started_at <= began,
                    or_(Session.ended_at.is_(None), Session.ended_at >= began),
                    Session.id.not_in(linked_sessions or {-1}),
                )
            ).all()
            if found:
                runs[run.remote_id] = run
                candidates[run.remote_id] = [(session, began) for session in found]
        wanted_by: dict[int, int] = {}
        for found in candidates.values():
            for session, _ in found:
                wanted_by[session.id] = wanted_by.get(session.id, 0) + 1
        for run_id, found in candidates.items():
            # One session for the run, and the run alone for that session: anything
            # else is two agents started together, which the moment cannot tell apart.
            if len(found) != 1 or wanted_by[found[0][0].id] != 1:
                continue
            session, began = found[0]
            run = runs[run_id]
            before = int((began - _aware(session.started_at)).total_seconds())
            if _store(
                db,
                session.id,
                connection.id,
                RunLinkBasis.HEURISTIC,
                {"matched": STARTED_WITH, "seconds_before": before},
                run_id=run.remote_id,
                task_id=run.task_remote_id,
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


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _began(run: WcRun) -> datetime | None:
    """When the harness session a run declares began.

    A time-ordered id (UUID version 7) carries the moment it was made, to the
    millisecond. Any other id says nothing, and the run's own start stands in: a run
    is begun just after its session.
    """
    try:
        declared = uuid.UUID(run.harness_session_id or "")
    except ValueError:
        declared = None
    if declared is not None and declared.version == 7:
        return datetime.fromtimestamp((declared.int >> 80) / 1000, UTC)
    return _aware(run.started_at) if run.started_at is not None else None


def _run_end(run: WcRun) -> datetime:
    return run.ended_at or run.heartbeat_at or run.started_at


def _key_of(keys: dict[str, int], task_id: int) -> str:
    return next(key for key, found in keys.items() if found == task_id)


def strongest(links: Iterable[RunLink]) -> RunLink | None:
    """The link resting on the strongest evidence; a suggestion never outranks an exact one."""
    ordered = sorted(links, key=lambda link: (RANK[link.basis], -link.confidence, link.id or 0))
    return ordered[0] if ordered else None
