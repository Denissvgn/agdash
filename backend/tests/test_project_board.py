"""The live board by project: grouping, agents, task and run context, and attention flags.

Every session, task, run and name here is synthetic, built so that each of the
four attention flags is raised by exactly the case it describes.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_api import local_client

from agdash.connectors.resolver import Resolver
from agdash.db.migrate import upgrade
from agdash.db.models import (
    Connection,
    Event,
    MappingRule,
    RunLink,
    Session,
    WcActor,
    WcAssignment,
    WcProject,
    WcRun,
    WcTask,
)
from agdash.vocab import EventKind, RuleMatchKind, RuleTargetKind, RunLinkBasis, SessionState

NOW = datetime.now(UTC)


@pytest.fixture
def url(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    return url


def session(db, name, state, *, cwd=None, models=(), parent=None) -> Session:
    found = Session(
        harness="codex",
        native_id=name,
        title=f"Session {name}",
        cwd=cwd,
        models=list(models),
        state=state,
        parent_session_id=parent,
        last_activity_at=NOW,
    )
    db.add(found)
    db.flush()
    return found


def exact(db, cid, session_id, run_id, task_id, assignment_id=None) -> None:
    db.add(
        RunLink(
            session_id=session_id,
            connection_id=cid,
            run_remote_id=run_id,
            task_remote_id=task_id,
            assignment_remote_id=assignment_id,
            basis=RunLinkBasis.TOOL_CALL,
            confidence=1.0,
            evidence={},
        )
    )


@pytest.fixture
def board_store(url) -> dict[str, int]:
    engine = create_engine(url)
    with sessionmaker(engine)() as db:
        row = Connection(kind="workchord", name="Team", base_url="http://127.0.0.1:8480")
        db.add(row)
        db.flush()
        cid = row.id
        db.add_all(
            [
                WcProject(
                    connection_id=cid,
                    remote_id=1,
                    name="Orchard",
                    health="on_track",
                    fetched_at=NOW,
                ),
                WcProject(connection_id=cid, remote_id=2, name="Meadow", fetched_at=NOW),
                # The claim on ORC-1 ran out an hour ago; ORC-2's runs for another day.
                WcTask(
                    connection_id=cid,
                    remote_id=101,
                    project_remote_id=1,
                    external_key="ORC-1",
                    title="Prune the rows",
                    status="in_progress",
                    iteration_name="Sprint 7",
                    claim_expires_at=NOW - timedelta(hours=1),
                    fetched_at=NOW,
                ),
                WcTask(
                    connection_id=cid,
                    remote_id=102,
                    project_remote_id=1,
                    external_key="ORC-2",
                    title="Water the beds",
                    status="in_progress",
                    claim_expires_at=NOW + timedelta(days=1),
                    fetched_at=NOW,
                ),
                WcActor(
                    connection_id=cid,
                    remote_id=31,
                    name="robin",
                    display_name="Robin",
                    role="worker",
                    fetched_at=NOW,
                ),
                WcActor(
                    connection_id=cid,
                    remote_id=32,
                    name="wren",
                    display_name="Wren",
                    role="verifier",
                    fetched_at=NOW,
                ),
                WcAssignment(
                    connection_id=cid,
                    remote_id=401,
                    actor_remote_id=31,
                    task_remote_id=101,
                    project_remote_id=1,
                    mode="execution",
                    status="executing",
                    fetched_at=NOW,
                ),
                WcAssignment(
                    connection_id=cid,
                    remote_id=402,
                    actor_remote_id=32,
                    task_remote_id=102,
                    project_remote_id=1,
                    mode="verification",
                    status="queued",
                    fetched_at=NOW,
                ),
                WcAssignment(
                    connection_id=cid,
                    remote_id=403,
                    actor_remote_id=32,
                    task_remote_id=None,
                    project_remote_id=2,
                    mode="execution",
                    status="completed",
                    fetched_at=NOW,
                ),
                # Bound to model-a; the session that runs it was seen using model-b.
                WcRun(
                    connection_id=cid,
                    remote_id=501,
                    task_remote_id=101,
                    actor_remote_id=31,
                    assignment_remote_id=401,
                    status="running",
                    resolved_model_id="model-a-0101",
                    declared_usage={"quantities": {"input_tokens": 900, "output_tokens": 90}},
                    started_at=NOW,
                    fetched_at=NOW,
                ),
                # Running in WorkChord, and no session here is linked to it.
                WcRun(
                    connection_id=cid,
                    remote_id=502,
                    task_remote_id=102,
                    actor_remote_id=32,
                    status="running",
                    started_at=NOW,
                    fetched_at=NOW,
                ),
                # Over: never a flag.
                WcRun(
                    connection_id=cid,
                    remote_id=503,
                    task_remote_id=102,
                    actor_remote_id=32,
                    status="completed",
                    started_at=NOW,
                    ended_at=NOW,
                    fetched_at=NOW,
                ),
                # Running and observed with the model it was bound to: no flag.
                WcRun(
                    connection_id=cid,
                    remote_id=504,
                    task_remote_id=102,
                    actor_remote_id=32,
                    assignment_remote_id=402,
                    status="running",
                    configured_model_alias="model-a",
                    started_at=NOW,
                    fetched_at=NOW,
                ),
            ]
        )
        a = session(db, "a", SessionState.ACTIVE, models=["model-b"])
        exact(db, cid, a.id, 501, 101, 401)
        child = session(db, "child", SessionState.ACTIVE, parent=a.id)
        b = session(db, "b", SessionState.WAITING, cwd="/work/orchard")
        c = session(db, "c", SessionState.IDLE, cwd="/elsewhere")
        ended = session(db, "ended", SessionState.ENDED, cwd="/work/orchard")
        f = session(db, "f", SessionState.ACTIVE, models=["model-a"])
        exact(db, cid, f.id, 504, 102, 402)
        db.add(
            MappingRule(
                connection_id=cid,
                match_kind=RuleMatchKind.FOLDER,
                match_value="/work/orchard",
                target_kind=RuleTargetKind.PROJECT,
                target_value="1",
            )
        )
        db.add(
            Event(
                session_id=a.id,
                sequence=0,
                occurred_at=NOW,
                kind=EventKind.USAGE,
                payload={"scope": "message", "input_tokens": 700, "output_tokens": 70},
            )
        )
        db.flush()
        Resolver().run(db, [row])
        db.commit()
        ids = {"a": a.id, "child": child.id, "b": b.id, "c": c.id, "ended": ended.id, "f": f.id}
    engine.dispose()
    return ids


def get_board(url, **params) -> dict:
    with local_client(url) as client:
        answer = client.get("/api/projects/board", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_sessions_group_under_their_project_and_the_rest_are_unmapped(url, board_store):
    board = get_board(url)
    names = [(g["project"] or {}).get("name") for g in board["groups"]]
    assert names == ["Orchard", None]
    orchard, unmapped = board["groups"]
    assert {card["id"] for card in orchard["sessions"]} == {board_store[k] for k in ("a", "b", "f")}
    assert [card["id"] for card in unmapped["sessions"]] == [board_store["c"]]
    assert unmapped["reason"] == "unmapped"
    shown = {card["id"] for g in board["groups"] for card in g["sessions"]}
    assert board_store["ended"] not in shown  # only live sessions
    lead = next(card for card in orchard["sessions"] if card["id"] == board_store["a"])
    assert [sub["id"] for sub in lead["subagents"]] == [board_store["child"]]


def test_each_session_carries_its_task_run_and_what_was_observed(url, board_store):
    orchard = get_board(url)["groups"][0]
    lead = next(card for card in orchard["sessions"] if card["id"] == board_store["a"])
    assert lead["task"] == {
        "id": 101,
        "key": "ORC-1",
        "title": "Prune the rows",
        "status": "in_progress",
        "iteration": "Sprint 7",
        "claim_expires_at": lead["task"]["claim_expires_at"],
    }
    assert lead["run"]["status"] == "running" and lead["run"]["bound_model"] == "model-a-0101"
    assert lead["run"]["declared_usage"]["quantities"]["input_tokens"] == 900
    assert lead["observed_usage"]["input_tokens"] == 700
    assert lead["observed_models"] == ["model-b"]
    assert lead["work"] == "execution" and lead["actor"]["name"] == "Robin"
    assert lead["actor"]["basis"] == "run" and lead["project_basis"] == "task"
    reviewer = next(card for card in orchard["sessions"] if card["id"] == board_store["f"])
    assert reviewer["work"] == "review" and reviewer["actor"]["name"] == "Wren"
    ruled = next(card for card in orchard["sessions"] if card["id"] == board_store["b"])
    assert ruled["project_basis"] == "rule" and ruled["task"] is None


def test_the_four_attention_flags_are_raised_by_their_cases(url, board_store):
    board = get_board(url)
    raised = {(f["code"], f["session_id"], f["run_id"]) for f in board["flags"]}
    assert raised == {
        ("claim_expired", board_store["a"], None),
        ("model_mismatch", board_store["a"], 501),
        ("no_task", board_store["b"], None),
        ("run_without_session", None, 502),
    }
    mismatch = next(f for f in board["flags"] if f["code"] == "model_mismatch")
    assert (mismatch["observed"], mismatch["bound"]) == ("model-b", "model-a-0101")
    lonely = next(f for f in board["flags"] if f["code"] == "run_without_session")
    assert (lonely["task_key"], lonely["actor"]) == ("ORC-2", "Wren")
    cards = {card["id"]: card for g in board["groups"] for card in g["sessions"]}
    assert set(cards[board_store["a"]]["flags"]) == {"claim_expired", "model_mismatch"}
    assert cards[board_store["f"]]["flags"] == []
    assert cards[board_store["c"]]["flags"] == []  # unmapped: no project, so no "no task"


def test_each_project_lists_its_agents(url, board_store):
    orchard = get_board(url)["groups"][0]
    assert [(a["name"], a["live_sessions"], a["assigned"]) for a in orchard["agents"]] == [
        ("Robin", 1, True),
        ("Wren", 1, True),
    ]


def test_without_a_connection_every_live_session_is_unmapped(url):
    engine = create_engine(url)
    with sessionmaker(engine)() as db:
        session(db, "x", SessionState.ACTIVE)
        db.commit()
    engine.dispose()
    board = get_board(url)
    assert board["connection"] is None and board["flags"] == []
    (only,) = board["groups"]
    assert only["reason"] == "unmapped" and len(only["sessions"]) == 1
    with local_client(url) as client:
        assert client.get("/api/projects/board", params={"connection_id": 9}).status_code == 404
