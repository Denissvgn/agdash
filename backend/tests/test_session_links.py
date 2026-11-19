"""Links from sessions to WorkChord runs and tasks, and the evidence each rests on.

Transcripts, processes and the WorkChord copy are built by hand; every id,
name and value is synthetic.
"""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from test_workchord_mirror import KEY, FakeServer, connect

from agdash.connectors.links import Linker, read_work_result, strongest
from agdash.connectors.mirror import MirrorSync
from agdash.db.migrate import upgrade
from agdash.db.models import (
    Connection,
    Event,
    RunLink,
    Session,
    SessionProcess,
    WcRun,
    WcTask,
)
from agdash.platform.secrets import FileStore
from agdash.vocab import EventKind, RunLinkBasis, SessionState

ORIGIN = "http://127.0.0.1:8480"
OTHER = "https://work.example"
T0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def begun(run_id=501, task_id=101, assignment_id=401, actor_id=31) -> dict:
    """WorkChord's answer to beginning work, with the fields agdash reads and some it ignores."""
    return {
        "assignment": {"id": assignment_id, "task_id": task_id, "actor_id": actor_id},
        "task": {"id": task_id, "title": "Synthetic task"},
        "run": {
            "id": run_id,
            "task_id": task_id,
            "actor_id": actor_id,
            "assignment_id": assignment_id,
            "status": "running",
            "metadata": {},
            "started_at": "2026-11-16T09:00:00Z",
        },
        "claim_id": "claim-synthetic-not-kept",
        "claim_generation": 3,
        "claim_expires_at": "2026-11-16T10:00:00Z",
        "queue_revision": 12,
    }


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def server(db, base_url=ORIGIN) -> Connection:
    row = Connection(kind="workchord", base_url=base_url)
    db.add(row)
    db.flush()
    return row


def agent_session(db, native="s-1", harness="claude_code", **fields) -> Session:
    session = Session(harness=harness, native_id=native, state=SessionState.ENDED, **fields)
    db.add(session)
    db.flush()
    return session


def record(db, session: Session, *records) -> None:
    start = len(db.scalars(select(Event.id).where(Event.session_id == session.id)).all())
    for offset, (kind, payload) in enumerate(records):
        db.add(
            Event(
                session_id=session.id,
                sequence=start + offset,
                occurred_at=T0 + timedelta(seconds=start + offset),
                kind=kind,
                payload=payload,
            )
        )
    db.flush()


def mcp_begin(db, session, call_id="c1", result=None, *, is_error=False) -> None:
    """A Claude Code style MCP call: the result is a list of text blocks holding JSON."""
    record(
        db,
        session,
        (
            EventKind.TOOL_CALL,
            {
                "name": "mcp__workchord__agent_begin_my_work",
                "call_id": call_id,
                "input": {"payload": {"assignment_id": 401}},
            },
        ),
        (
            EventKind.TOOL_RESULT,
            {
                "call_id": call_id,
                "is_error": is_error,
                "output": [{"type": "text", "text": json.dumps(result or begun())}],
            },
        ),
    )


def links(db, session_id=None) -> list[RunLink]:
    query = select(RunLink).order_by(RunLink.id)
    if session_id is not None:
        query = query.where(RunLink.session_id == session_id)
    return list(db.scalars(query))


# -- 1. tool_call -------------------------------------------------------------------


def test_a_begin_work_result_in_a_transcript_is_an_exact_link(store):
    with store() as db:
        row = server(db)
        session = agent_session(db)
        mcp_begin(db, session)
        linked = Linker().run(db, [row])
        (link,) = links(db)
        assert linked.added == {"tool_call": 1}
        assert (link.basis, link.confidence) == (RunLinkBasis.TOOL_CALL, 1.0)
        assert (link.run_remote_id, link.task_remote_id, link.assignment_remote_id) == (
            501,
            101,
            401,
        )
        assert link.evidence == {
            "call": "agent_begin_my_work",
            "sequence": 0,
            "actor_id": 31,
            "claim_generation": 3,
            "claim_expires_at": "2026-11-16T10:00:00Z",
        }
        assert "claim-synthetic-not-kept" not in json.dumps(link.evidence)


def test_an_http_begin_from_a_shell_is_placed_on_the_server_it_names(store):
    with store() as db:
        local, remote = server(db), server(db, OTHER)
        session = agent_session(db, harness="codex")
        record(
            db,
            session,
            (
                EventKind.TOOL_CALL,
                {
                    "name": "shell",
                    "call_id": "sh-1",
                    "input": {
                        "command": ["curl", "-X", "POST", f"{OTHER}/api/agent/me/work/begin"]
                    },
                },
            ),
            (
                EventKind.TOOL_RESULT,
                {"call_id": "sh-1", "output": "HTTP/1.1 200\n" + json.dumps(begun(run_id=9))},
            ),
        )
        Linker().run(db, [local, remote])
        (link,) = links(db)
        assert (link.connection_id, link.run_remote_id, link.evidence["call"]) == (
            remote.id,
            9,
            "http",
        )


def test_with_several_servers_a_call_goes_where_its_task_is_or_nowhere(store):
    with store() as db:
        first, second = server(db), server(db, OTHER)
        db.add(WcTask(connection_id=second.id, remote_id=101, title="t", fetched_at=T0))
        placed = agent_session(db, native="placed")
        mcp_begin(db, placed)
        unknown = agent_session(db, native="unknown")
        mcp_begin(db, unknown, result=begun(run_id=77, task_id=999))
        linked = Linker().run(db, [first, second])
        (link,) = links(db)
        assert (link.session_id, link.connection_id) == (placed.id, second.id)
        assert linked.unplaced == 1


def test_failed_calls_and_results_that_name_no_run_link_nothing(store):
    with store() as db:
        row = server(db)
        session = agent_session(db)
        mcp_begin(db, session, "c1", is_error=True)
        mcp_begin(db, session, "c2", result={"detail": "queue changed"})
        record(
            db,
            session,
            (EventKind.TOOL_CALL, {"name": "mcp__workchord__agent_list_my_runs", "call_id": "c3"}),
            (EventKind.TOOL_RESULT, {"call_id": "c3", "output": json.dumps([begun()["run"]])}),
        )
        Linker().run(db, [row])
        assert links(db) == []


def test_each_call_is_read_once_and_links_are_not_repeated(store):
    with store() as db:
        row = server(db)
        session = agent_session(db)
        mcp_begin(db, session, "c1")
        linker = Linker()
        first = linker.run(db, [row])
        mcp_begin(db, session, "c2")  # a renewal names the same run
        again = linker.run(db, [row])
        assert again.last_event_id > first.last_event_id
        assert again.added == {} and len(links(db)) == 1


def scripted(db, session, call_id="x1", *, printed=None, script=None) -> None:
    """A Codex code-mode call: one exec tool runs a script that calls the MCP tool and
    prints what it chooses, here the result cut short as a cautious script may."""
    if script is None:
        script = (
            "const r = await tools.mcp__workchord__agent_begin_my_work("
            "{idempotency_key: 'k', payload: {assignment_id: 401, lease_seconds: 600}});\n"
            "text(JSON.stringify({isError: false, safe: JSON.stringify(r).slice(0, 80)}));"
        )
    if printed is None:
        printed = {"isError": False, "safe": json.dumps(begun())[:80]}
    record(
        db,
        session,
        (EventKind.TOOL_CALL, {"name": "exec", "call_id": call_id, "input": script}),
        (
            EventKind.TOOL_RESULT,
            {"call_id": call_id, "output": "Script completed\nOutput:\n\n" + json.dumps(printed)},
        ),
    )


def mirrored_run(db, row, *, started=T0 + timedelta(minutes=1), ended=None) -> None:
    db.add(
        WcRun(
            connection_id=row.id,
            remote_id=501,
            task_remote_id=101,
            assignment_remote_id=401,
            actor_remote_id=31,
            started_at=started,
            ended_at=ended,
            fetched_at=T0,
        )
    )
    db.flush()


def test_a_scripted_begin_cut_short_links_through_the_assignment_it_sends(store):
    with store() as db:
        row = server(db)
        mirrored_run(db, row)
        session = agent_session(db, harness="codex")
        scripted(db, session)
        linked = Linker().run(db, [row])
        (link,) = links(db)
        assert linked.added == {"tool_call": 1}
        assert (link.basis, link.run_remote_id, link.task_remote_id) == (
            RunLinkBasis.TOOL_CALL,
            501,
            101,
        )
        assert link.evidence == {
            "call": "agent_begin_my_work",
            "sequence": 0,
            "actor_id": 31,
            "read_from": "call input",
        }


def test_a_scripted_begin_that_prints_the_whole_result_links_from_the_result(store):
    with store() as db:
        row = server(db)
        session = agent_session(db, harness="codex")
        scripted(db, session, printed={"isError": False, "safe": json.dumps(begun(run_id=9))})
        Linker().run(db, [row])
        (link,) = links(db)
        assert (link.run_remote_id, link.evidence["call"]) == (9, "agent_begin_my_work")
        assert "read_from" not in link.evidence


def soon() -> datetime:
    return T0 + timedelta(minutes=5)


def test_a_call_waits_for_its_run_to_reach_the_copy(store):
    with store() as db:
        row = server(db)
        session = agent_session(db, harness="codex")
        scripted(db, session)
        linker = Linker(clock=soon)
        linker.run(db, [row])
        assert links(db) == []
        mirrored_run(db, row)
        linker.run(db, [row])
        (link,) = links(db)
        assert link.run_remote_id == 501


def test_a_call_waits_for_its_result(store):
    with store() as db:
        row = server(db)
        session = agent_session(db)
        record(
            db,
            session,
            (
                EventKind.TOOL_CALL,
                {"name": "mcp__workchord__agent_begin_my_work", "call_id": "c1", "input": {}},
            ),
        )
        linker = Linker(clock=soon)
        linker.run(db, [row])
        assert links(db) == []
        record(
            db,
            session,
            (EventKind.TOOL_RESULT, {"call_id": "c1", "output": json.dumps(begun())}),
        )
        linker.run(db, [row])
        (link,) = links(db)
        assert link.run_remote_id == 501


def test_a_call_stops_waiting_once_it_is_old(store):
    now = [soon()]
    with store() as db:
        row = server(db)
        session = agent_session(db, harness="codex")
        scripted(db, session)
        linker = Linker(clock=lambda: now[0])
        linker.run(db, [row])
        now[0] = T0 + timedelta(hours=2)
        linker.run(db, [row])
        mirrored_run(db, row)
        linker.run(db, [row])
        assert links(db) == []


def test_an_old_call_is_not_kept_waiting(store):
    with store() as db:
        row = server(db)
        session = agent_session(db, harness="codex")
        scripted(db, session)
        linker = Linker(clock=lambda: T0 + timedelta(days=2))
        linker.run(db, [row])
        mirrored_run(db, row)
        linker.run(db, [row])
        assert links(db) == []


def test_a_result_that_names_only_the_run_takes_its_task_from_the_copy(store):
    with store() as db:
        row = server(db)
        mirrored_run(db, row)
        session = agent_session(db)
        submitted = {"run": {"id": 501, "status": "succeeded"}, "assignment_state": "fulfilled"}
        mcp_begin(db, session, "c1")
        mcp_begin(db, session, "c2", result=submitted)
        Linker().run(db, [row])
        (link,) = links(db)
        assert (link.run_remote_id, link.task_remote_id, link.assignment_remote_id) == (
            501,
            101,
            401,
        )


@pytest.mark.parametrize(
    "case",
    ["mentions the tool only", "printed a failure", "run at another time"],
)
def test_a_scripted_call_needs_its_run_named_live_and_not_failed(store, case):
    with store() as db:
        row = server(db)
        session = agent_session(db, harness="codex")
        if case == "run at another time":
            mirrored_run(db, row, started=T0 + timedelta(hours=3))
            scripted(db, session)
        else:
            mirrored_run(db, row)
        if case == "mentions the tool only":
            # Code being edited that calls the tool, with an answer that names nothing.
            record(
                db,
                session,
                (
                    EventKind.TOOL_CALL,
                    {
                        "name": "Write",
                        "call_id": "w1",
                        "input": {"content": "agent_begin_my_work({'assignment_id': 401})"},
                    },
                ),
                (EventKind.TOOL_RESULT, {"call_id": "w1", "output": "File written"}),
            )
        if case == "printed a failure":
            scripted(db, session, printed={"isError": True, "safe": json.dumps(begun())[:80]})
        Linker().run(db, [row])
        assert links(db) == []


@pytest.mark.parametrize(
    "output",
    [
        begun(),
        {"content": [{"type": "text", "text": json.dumps(begun())}]},
        {"structuredContent": begun()},
        json.dumps(begun()),
        {"isError": False, "safe": json.dumps(begun())},
        "Script completed\n" + json.dumps({"keys": []}) + "\n" + json.dumps(begun()) + "\n",
    ],
    ids=["object", "content", "structured", "text", "nested text", "several printed"],
)
def test_a_work_result_is_read_however_it_is_wrapped(output):
    claimed = read_work_result(output)
    assert (claimed.run_id, claimed.task_id, claimed.assignment_id) == (501, 101, 401)


# -- 2. trace_id --------------------------------------------------------------------


def test_a_run_that_declared_the_session_links_to_it_exactly(store):
    with store() as db:
        row = server(db)
        claude = agent_session(db, native="shared-id")
        codex = agent_session(db, native="shared-id", harness="codex")
        db.add(
            WcRun(
                connection_id=row.id,
                remote_id=601,
                task_remote_id=102,
                assignment_remote_id=402,
                harness_kind="claude-code",
                harness_session_id="shared-id",
                started_at=T0,
                fetched_at=T0,
            )
        )
        Linker().run(db, [row])
        (link,) = links(db)
        assert link.session_id == claude.id and link.session_id != codex.id
        assert (link.basis, link.confidence) == (RunLinkBasis.TRACE_ID, 0.95)
        assert (link.run_remote_id, link.task_remote_id) == (601, 102)


def test_runs_people_may_list_bring_their_declared_session(tmp_path, store):
    secrets = FileStore(tmp_path / "secrets.json")

    class PersonReads(FakeServer):
        def handle(self, request):
            if request.url.path == "/api/agent/runs" and not request.headers.get("X-Agent-API-Key"):
                self.requests.append(request)
                return httpx.Response(200, json=self.routes["/api/agent/runs"][1])
            return super().handle(request)

    fake = PersonReads()
    fake.routes["/api/auth/me"] = (
        200,
        {"mode": "managed", "authenticated": True, "features": ["person-run-reads-v1"]},
    )
    fake.routes["/api/agent/runs"] = (
        200,
        [
            {
                "id": 701,
                "task_id": 101,
                "assignment_id": 401,
                "actor_id": 31,
                "status": "running",
                "trace_id": "attempt-1",
                "harness": {"kind": "codex", "session_id": "codex-session-1"},
                "started_at": "2026-11-16T09:00:00Z",
                "usage": {"source": "actor", "quantities": {"output_tokens": 5}, "id": "u"},
            }
        ],
    )
    with store() as db:
        connect(db, secrets)
        agent_session(db, native="codex-session-1", harness="codex")
        db.commit()
    MirrorSync(store, secrets, transport=fake.transport).tick()
    with store() as db:
        run = db.scalars(select(WcRun)).one()
        assert (run.harness_kind, run.harness_session_id) == ("codex", "codex-session-1")
        assert run.declared_usage == {"source": "actor", "quantities": {"output_tokens": 5}}
        (link,) = links(db)
        assert (link.basis, link.run_remote_id) == (RunLinkBasis.TRACE_ID, 701)
    assert not any(r.headers.get("X-Agent-API-Key") == KEY for r in fake.requests)
    assert {r.method for r in fake.requests} == {"GET"}


# -- 3. mcp_config ------------------------------------------------------------------


def test_the_server_address_in_process_arguments_links_to_the_server_only(store):
    with store() as db:
        row = server(db)
        started = agent_session(db, harness="codex")
        elsewhere = agent_session(db, native="s-2", harness="codex")
        for session, argv in (
            (started, ["codex", "-c", "mcp_servers.wc.url=http://localhost:8480/api/mcp"]),
            (elsewhere, ["codex", "-c", "mcp_servers.x.url=http://127.0.0.1:9999/mcp"]),
        ):
            db.add(
                SessionProcess(
                    session_id=session.id,
                    pid=4000 + session.id,
                    process_started_at=T0,
                    argv_redacted=argv,
                    first_seen_at=T0,
                    last_seen_at=T0,
                )
            )
        Linker().run(db, [row])
        (link,) = links(db)
        assert (link.session_id, link.basis, link.confidence) == (
            started.id,
            RunLinkBasis.MCP_CONFIG,
            0.5,
        )
        assert link.run_remote_id is None and link.task_remote_id is None


# -- 4. heuristic -------------------------------------------------------------------


def copy(db, row, *, run_start=T0 + timedelta(minutes=30)) -> None:
    db.add_all(
        [
            WcTask(
                connection_id=row.id, remote_id=102, external_key="ORC-2", title="t", fetched_at=T0
            ),
            WcTask(
                connection_id=row.id, remote_id=120, external_key="ORC-20", title="u", fetched_at=T0
            ),
            WcRun(
                connection_id=row.id,
                remote_id=801,
                task_remote_id=102,
                started_at=run_start,
                heartbeat_at=run_start + timedelta(minutes=5),
                fetched_at=T0,
            ),
        ]
    )
    db.flush()


def worked_on(db, native: str, branch: str) -> Session:
    return agent_session(
        db,
        native=native,
        branch=branch,
        started_at=T0,
        last_activity_at=T0 + timedelta(hours=1),
    )


def test_a_task_key_in_the_branch_and_an_overlapping_run_is_only_a_suggestion(store):
    with store() as db:
        row = server(db)
        copy(db, row)
        matching = worked_on(db, "a", "feature/orc-2-tidy")
        longer_key = worked_on(db, "b", "feature/ORC-200")
        Linker().run(db, [row])
        (link,) = links(db)
        assert link.session_id == matching.id and link.session_id != longer_key.id
        assert (link.basis, link.confidence) == (RunLinkBasis.HEURISTIC, 0.3)
        assert (link.run_remote_id, link.task_remote_id) == (801, 102)
        assert link.evidence == {"matched": "branch or worktree", "task_key": "ORC-2"}


def test_a_run_at_another_time_is_no_evidence(store):
    with store() as db:
        row = server(db)
        copy(db, row, run_start=T0 + timedelta(hours=3))
        worked_on(db, "a", "orc-2")
        Linker().run(db, [row])
        assert links(db) == []


def test_a_suggestion_never_overrides_stronger_evidence(store):
    with store() as db:
        row = server(db)
        copy(db, row)
        session = worked_on(db, "a", "orc-2")
        linker = Linker()
        linker.run(db, [row])
        (suggestion,) = links(db)
        assert suggestion.basis is RunLinkBasis.HEURISTIC

        # The transcript later shows the session began a different run: the guess goes.
        mcp_begin(db, session, result=begun(run_id=900, task_id=120))
        linked = linker.run(db, [row])
        (exact,) = links(db, session.id)
        assert (exact.basis, exact.run_remote_id) == (RunLinkBasis.TOOL_CALL, 900)
        assert linked.removed == 1

        # Later passes do not bring the suggestion back.
        linker.run(db, [row])
        assert [link.basis for link in links(db, session.id)] == [RunLinkBasis.TOOL_CALL]


def test_the_strongest_link_wins_whatever_its_confidence(store):
    with store() as db:
        row = server(db)
        session = agent_session(db)
        guess = RunLink(
            session_id=session.id,
            connection_id=row.id,
            basis=RunLinkBasis.HEURISTIC,
            confidence=1.0,
        )
        server_only = RunLink(
            session_id=session.id,
            connection_id=row.id,
            basis=RunLinkBasis.MCP_CONFIG,
            confidence=0.5,
        )
        assert strongest([guess, server_only]) is server_only
        assert strongest([]) is None
