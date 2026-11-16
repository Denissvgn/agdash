"""The WorkChord mirror: what it copies, what it reports, and how it backs off.

The server is a stand-in that answers with the response shapes of WorkChord's
read endpoints. All names, ids and values are synthetic.
"""

import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agdash.connectors import cli
from agdash.connectors.mirror import MirrorSync, object_states, sync_connection
from agdash.connectors.workchord import Session, session_ref, set_run_key
from agdash.db.migrate import upgrade
from agdash.db.models import (
    Connection,
    WcActor,
    WcAssignment,
    WcProject,
    WcRun,
    WcSyncObject,
    WcTask,
)
from agdash.platform.secrets import FileStore
from agdash.vocab import MirrorObject, MirrorState, SyncState

ORIGIN = "http://127.0.0.1:8480"
TOKEN = "person-session-token"
KEY = "agent-key-planning"


def task(task_id, title, *, project=1, iteration=7, key=None, claim=None, children=()):
    return {
        "id": task_id,
        "title": title,
        "status": "planned",
        "project_id": project,
        "iteration_id": iteration,
        "external_key": key,
        "claimed_by": claim,
        "claim_expires_at": "2026-11-17T12:00:00Z" if claim else None,
        "children": list(children),
        "brief": {"goal": "not stored"},
    }


ROBIN = {"id": 31, "name": "robin", "display_name": "Robin (synthetic)"}
WREN = {"id": 32, "name": "wren", "display_name": "Wren (synthetic)", "role": "verifier"}


class FakeServer:
    """Answers WorkChord's read endpoints; a test can change any answer."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down = False
        self.routes: dict[str, tuple[int, object]] = {
            "/api/auth/me": (200, {"mode": "managed", "authenticated": True}),
            "/api/projects": (
                200,
                [
                    {
                        "id": 1,
                        "name": "Orchard",
                        "status": "active",
                        "health": "on_track",
                        "initiative": {"id": 5, "name": "Spring"},
                    },
                    {
                        "id": 2,
                        "name": "Meadow",
                        "status": "planned",
                        "health": "unknown",
                        "initiative": None,
                    },
                ],
            ),
            "/api/iterations": (200, [{"id": 7, "name": "Sprint 7", "project_id": 1}]),
            "/api/projects/1/tasks": (
                200,
                [
                    task(
                        101,
                        "Prune the trees",
                        key="ORC-1",
                        claim=ROBIN,
                        children=[task(102, "Sharpen the shears", key="ORC-2")],
                    ),
                    task(103, "Plan next season", iteration=None),
                ],
            ),
            "/api/projects/2/tasks": (200, [task(201, "Mow", project=2, iteration=None)]),
            "/api/agent/pipeline": (
                200,
                {
                    "ready_to_assign": [task(103, "Plan next season", iteration=None)],
                    "executing": [task(101, "Prune the trees", key="ORC-1", claim=ROBIN)],
                    "needs_definition": [],
                },
            ),
            "/api/agent/assignments": (
                200,
                [
                    {
                        "id": 401,
                        "task_id": 101,
                        "actor_id": 31,
                        "purpose": "execution",
                        "state": "accepted",
                        "actor": {**ROBIN, "role": "worker"},
                        "model": {
                            "model_catalog_key": "k",
                            "provider": "p",
                            "configured_model_alias": "synthetic-model-1",
                        },
                    },
                    {
                        "id": 402,
                        "task_id": 201,
                        "actor_id": 32,
                        "purpose": "verification",
                        "state": "queued",
                        "actor": WREN,
                        "model": None,
                        "model_binding_id": 9,
                    },
                ],
            ),
            "/api/agent/actors": (
                200,
                [
                    {**ROBIN, "role": "worker", "lifecycle_state": "active", "scopes": []},
                    {**WREN, "lifecycle_state": "active", "scopes": []},
                    {
                        "id": 33,
                        "name": "lark",
                        "display_name": None,
                        "role": "pm",
                        "lifecycle_state": "onboarding",
                    },
                ],
            ),
            "/api/agent/runs/501": (
                200,
                {
                    "id": 501,
                    "task_id": 101,
                    "actor_id": 31,
                    "assignment_id": 401,
                    "status": "running",
                    "trace_id": "trace-synthetic-1",
                    "configured_model_alias": "synthetic-model-1",
                    "resolved_model_id": "synthetic-model-1-0101",
                    "metadata": {"harness_session": "s-1", "api_key": "sk-synthetic-not-real"},
                    "started_at": "2026-11-16T09:00:00Z",
                    "heartbeat_at": "2026-11-16T09:05:00Z",
                    "ended_at": None,
                    "events": [],
                },
            ),
            "/api/agent/runs/501/usage": (
                200,
                {
                    "source": "actor",
                    "provenance": "declared",
                    "coverage": "complete",
                    "quantities": {"input_tokens": 1200, "output_tokens": 300},
                    "reported_cost": "0.42",
                    "currency": "USD",
                    "report_id": "r-1",
                },
            ),
        }

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        key = request.headers.get("X-Agent-API-Key")
        bearer = request.headers.get("Authorization")
        if key and bearer:
            return httpx.Response(400, json={"detail": "two principals"})
        path = request.url.path
        agent_only = path.startswith("/api/agent/runs") or path == "/api/agent/actors"
        if agent_only and key != KEY:
            return httpx.Response(401, json={"detail": "Missing X-Agent-API-Key header"})
        if path not in self.routes:
            return httpx.Response(404, json={"detail": "Not Found"})
        status, body = self.routes[path]
        return httpx.Response(status, json=body)


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    yield sessionmaker(engine, expire_on_commit=False), FileStore(tmp_path / "secrets.json")
    engine.dispose()


def connect(db, secrets, *, agent_key=False, expires=None) -> Connection:
    row = Connection(kind="workchord", base_url=ORIGIN)
    db.add(row)
    db.flush()
    expires = expires or datetime.now(UTC) + timedelta(hours=8)
    secrets.set(session_ref(row.id), Session(TOKEN, expires).dump())
    row.credential_ref = session_ref(row.id)
    if agent_key:
        set_run_key(db, secrets, row, KEY)
    db.commit()
    return row


def states(report) -> dict[str, str]:
    return {kind.value: outcome.state.value for kind, outcome in report.objects.items()}


def test_a_full_sync_copies_every_object(store):
    sessions, secrets = store
    server = FakeServer()
    with sessions() as db:
        row = connect(db, secrets, agent_key=True)
        db.add(WcRun(connection_id=row.id, remote_id=501, fetched_at=datetime.now(UTC)))
        db.commit()
        report = sync_connection(db, secrets, row, transport=server.transport)
        db.commit()

        assert report.reachable and set(states(report).values()) == {"ok"}
        projects = {p.remote_id: p for p in db.scalars(select(WcProject))}
        assert projects[1].initiative_name == "Spring" and projects[2].health == "unknown"

        tasks = {t.remote_id: t for t in db.scalars(select(WcTask))}
        assert set(tasks) == {101, 102, 103, 201}  # children and the backlog included
        assert tasks[102].external_key == "ORC-2" and tasks[101].iteration_name == "Sprint 7"
        assert tasks[101].claim_holder_remote_id == 31
        assert tasks[101].pipeline_stage == "executing"
        assert tasks[103].pipeline_stage == "ready_to_assign"
        assert tasks[201].pipeline_stage is None

        assignments = {a.remote_id: a for a in db.scalars(select(WcAssignment))}
        assert assignments[401].project_remote_id == 1
        assert assignments[401].model_binding == "synthetic-model-1"
        assert assignments[402].model_binding == "binding 9"
        assert assignments[402].mode == "verification"

        actors = {a.remote_id: a for a in db.scalars(select(WcActor))}
        assert set(actors) == {31, 32, 33} and actors[33].lifecycle_state == "onboarding"

        run = db.scalars(select(WcRun)).one()
        assert run.status == "running" and run.resolved_model_id == "synthetic-model-1-0101"
        assert run.declared_usage["quantities"] == {"input_tokens": 1200, "output_tokens": 300}
        assert "report_id" not in run.declared_usage
        assert "sk-synthetic-not-real" not in json.dumps(run.run_metadata)
        assert run.run_metadata["harness_session"] == "s-1"

        assert row.sync_state is SyncState.OK and row.last_error is None
    assert {r.method for r in server.requests} == {"GET"}


def test_without_the_agent_key_the_rest_still_syncs(store):
    sessions, secrets = store
    server = FakeServer()
    with sessions() as db:
        row = connect(db, secrets)
        report = sync_connection(db, secrets, row, transport=server.transport)
        db.commit()
        assert states(report) == {
            "projects": "ok",
            "tasks": "ok",
            "pipeline": "ok",
            "assignments": "ok",
            "actors": "unavailable",
            "runs": "unavailable",
        }
        # Actors are learned from claims and assignments instead.
        actors = {a.remote_id: a for a in db.scalars(select(WcActor))}
        assert set(actors) == {31, 32}
        assert actors[32].role == "verifier"
        assert "learned" in report.objects[MirrorObject.ACTORS].detail
    assert not any("X-Agent-API-Key" in r.headers for r in server.requests)
    assert not any(r.url.path.startswith("/api/agent/runs") for r in server.requests)


def test_an_object_the_credentials_cannot_read_is_marked_unavailable(store):
    sessions, secrets = store
    server = FakeServer()
    server.routes["/api/agent/pipeline"] = (403, {"detail": "no workspace role"})
    with sessions() as db:
        row = connect(db, secrets, agent_key=True)
        report = sync_connection(db, secrets, row, transport=server.transport)
        db.commit()
        assert states(report)["pipeline"] == "unavailable"
        assert "HTTP 403" in report.objects[MirrorObject.PIPELINE].detail
        assert {k: v for k, v in states(report).items() if k != "pipeline"} == dict.fromkeys(
            ["projects", "tasks", "assignments", "actors", "runs"], "ok"
        )
        stored = {s.kind: s.state for s in object_states(db, row.id)}
        assert stored[MirrorObject.PIPELINE] is MirrorState.UNAVAILABLE
        assert db.scalar(select(WcTask).where(WcTask.remote_id == 101)) is not None


def test_an_object_that_no_longer_parses_stops_syncing_and_is_reported(store):
    sessions, secrets = store
    server = FakeServer()
    with sessions() as db:
        row = connect(db, secrets, agent_key=True)
        sync_connection(db, secrets, row, transport=server.transport)
        db.commit()
        before = {t.remote_id: (t.title, t.status) for t in db.scalars(select(WcTask))}

        # The server renames a field agdash relies on and changes another's type.
        changed = [{**task(101, "Renamed"), "title": None, "name": "secret-looking value"}]
        server.routes["/api/projects/1/tasks"] = (200, changed)
        server.routes["/api/projects"] = (200, {"items": "a page instead of a list"})
        report = sync_connection(db, secrets, row, transport=server.transport)
        db.commit()

        assert states(report)["projects"] == "unparseable"
        # Tasks fall back to the stored project list and stop at the changed shape.
        assert states(report)["tasks"] == "unparseable"
        detail = report.objects[MirrorObject.TASKS].detail
        assert "title" in detail and "secret-looking value" not in detail
        after = {t.remote_id: (t.title, t.status) for t in db.scalars(select(WcTask))}
        assert after == before  # left as it was, not filled with a guess
        assert {p.name for p in db.scalars(select(WcProject))} == {"Orchard", "Meadow"}
        status = db.scalars(
            select(WcSyncObject).where(WcSyncObject.kind == MirrorObject.TASKS)
        ).one()
        assert status.state is MirrorState.UNPARSEABLE and status.synced_at is not None
        assert states(report)["assignments"] == "ok"  # the rest carries on


def test_objects_gone_from_the_server_leave_the_copy(store):
    sessions, secrets = store
    server = FakeServer()
    with sessions() as db:
        row = connect(db, secrets, agent_key=True)
        sync_connection(db, secrets, row, transport=server.transport)
        server.routes["/api/projects"] = (200, [server.routes["/api/projects"][1][0]])
        server.routes["/api/agent/assignments"] = (200, [])
        report = sync_connection(db, secrets, row, transport=server.transport)
        db.commit()
        assert states(report)["tasks"] == "ok"
        assert {p.remote_id for p in db.scalars(select(WcProject))} == {1}
        assert {t.remote_id for t in db.scalars(select(WcTask))} == {101, 102, 103}
        assert db.scalars(select(WcAssignment)).all() == []


def test_an_expired_session_sends_nothing(store):
    sessions, secrets = store
    server = FakeServer()
    with sessions() as db:
        row = connect(db, secrets, expires=datetime.now(UTC) - timedelta(minutes=1))
        report = sync_connection(db, secrets, row, transport=server.transport)
        assert not report.reachable and "pair again" in report.problem
        assert row.sync_state is SyncState.ERROR
    assert server.requests == []


def test_an_unreachable_server_is_retried_with_backoff(store):
    sessions, secrets = store
    server = FakeServer()
    server.down = True
    now = [1000.0]
    with sessions() as db:
        connect(db, secrets)
    mirror = MirrorSync(
        sessions,
        secrets,
        interval=60,
        max_backoff=300,
        transport=server.transport,
        clock=lambda: now[0],
    )
    delays = []
    for _ in range(4):
        reports = mirror.tick()
        assert len(reports) == 1 and not reports[0].reachable
        delays.append(mirror.delay(reports[0].connection_id))
        assert mirror.tick() == []  # not due yet
        now[0] += delays[-1]
    assert delays == [120, 240, 300, 300]

    server.down = False
    reports = mirror.tick()
    assert reports[0].reachable
    assert mirror.delay(reports[0].connection_id) == 60
    with sessions() as db:
        row = db.scalars(select(Connection)).one()
        assert row.sync_state is SyncState.OK and row.last_synced_at is not None


def test_the_sync_command_reports_each_object(store):
    sessions, secrets = store
    server = FakeServer()
    with sessions() as db:
        row = connect(db, secrets)
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.run(["sync", str(row.id)], db, secrets, transport=server.transport)
    assert code == 0
    text = out.getvalue()
    assert "projects: ok, 2 rows" in text
    assert "runs: unavailable" in text and "agent key" in text
