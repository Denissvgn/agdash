"""Publish mode: off, agdash only reads; on, it adds one link per session and task, once.

The server is a stand-in answering like WorkChord; every id, name and token is
synthetic.
"""

import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker
from test_workchord_mirror import TOKEN, FakeServer, connect

from agdash.connectors import cli
from agdash.connectors.http import WriteRefused, publish_client
from agdash.connectors.mirror import MirrorSync
from agdash.connectors.publish import (
    WRITE_PATH,
    Publisher,
    PublishError,
    enable,
    link_url,
)
from agdash.connectors.workchord import Session as Paired
from agdash.connectors.workchord import publish_ref, session_ref
from agdash.db.migrate import upgrade
from agdash.db.models import Connection, PublishedLink, RunLink, Session
from agdash.platform.secrets import FileStore
from agdash.vocab import RunLinkBasis, SessionState

FEATURES = ["observer-sessions-v1", "agdash-external-links-v1", "person-run-reads-v1"]
LINK_ROUTE = re.compile(r"/api/tasks/(\d+)/external-links")
PUBLISH_TOKEN = "publish-session-token"


class WorkChord(FakeServer):
    """Adds what publish mode touches: the features, pairing and the link route."""

    def __init__(self, mode="managed") -> None:
        super().__init__()
        self.routes["/api/auth/me"] = (
            200,
            {"mode": mode, "authenticated": True, "features": FEATURES},
        )
        self.links: dict[tuple[int, str], dict] = {}
        self.answer: tuple[int, dict] | None = None
        self.started: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/auth/native-connections/start":
            self.requests.append(request)
            self.started.append(json.loads(request.content))
            return httpx.Response(
                201,
                json={
                    "request_id": "r1",
                    "verification_code": "AB-12",
                    "verification_path": "/connect/r1",
                    "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                    "access": "full",
                },
            )
        if request.method == "POST" and path == "/api/auth/native-connections/exchange":
            self.requests.append(request)
            expires = (datetime.now(UTC) + timedelta(hours=8)).isoformat()
            return httpx.Response(
                200,
                json={
                    "access_token": PUBLISH_TOKEN,
                    "token_type": "Bearer",
                    "expires_at": expires,
                    "access": "full",
                },
            )
        found = LINK_ROUTE.fullmatch(path)
        if request.method == "POST" and found:
            self.requests.append(request)
            if self.answer is not None:
                return httpx.Response(self.answer[0], json=self.answer[1])
            body = json.loads(request.content)
            key = (int(found.group(1)), body["external_key"])
            if key not in self.links:  # WorkChord returns the existing link on a repeat
                self.links[key] = {"id": 900 + len(self.links), **body}
            return httpx.Response(201, json=self.links[key])
        return super().handle(request)

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != "GET"]


@pytest.fixture
def store(tmp_path):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    yield sessionmaker(engine, expire_on_commit=False), FileStore(tmp_path / "secrets.json")
    engine.dispose()


def observed(db, cid) -> Session:
    """A session exactly linked to task 101, suggested for 102 and linked to the server."""
    found = Session(harness="codex", native_id="codex-1", state=SessionState.ENDED)
    db.add(found)
    db.flush()
    for basis, task in (
        (RunLinkBasis.TOOL_CALL, 101),
        (RunLinkBasis.HEURISTIC, 102),
        (RunLinkBasis.MCP_CONFIG, None),
    ):
        db.add(
            RunLink(
                session_id=found.id,
                connection_id=cid,
                run_remote_id=501 if task == 101 else None,
                task_remote_id=task,
                basis=basis,
                confidence=0.5,
                evidence={},
            )
        )
    db.flush()
    return found


def trusted_local(db, *, publish=True) -> Connection:
    row = Connection(
        kind="workchord",
        base_url="http://127.0.0.1:8480",
        contract_seen={"auth_mode": "trusted_local", "features": FEATURES},
        publish_enabled=publish,
    )
    db.add(row)
    db.flush()
    return row


# -- off ----------------------------------------------------------------------------


def test_with_the_switch_off_agdash_only_reads(store):
    sessions, secrets = store
    server = WorkChord()
    with sessions() as db:
        row = connect(db, secrets)
        observed(db, row.id)
        db.commit()
    MirrorSync(sessions, secrets, transport=server.transport).tick()
    assert server.requests and {r.method for r in server.requests} == {"GET"}
    with sessions() as db:
        assert db.scalars(select(PublishedLink)).all() == []
        assert db.get(Connection, row.id).contract_seen["features"] == sorted(FEATURES)


# -- on -----------------------------------------------------------------------------


def test_with_it_on_one_write_route_adds_one_link_per_session_and_task(store):
    sessions, secrets = store
    server = WorkChord(mode="trusted_local")
    server.routes["/api/auth/me"] = (
        200,
        {"mode": "trusted_local", "authenticated": False, "features": FEATURES},
    )
    with sessions() as db:
        row = trusted_local(db)
        session = observed(db, row.id)
        db.commit()
    mirror = MirrorSync(sessions, secrets, transport=server.transport)
    mirror.tick()
    (write,) = server.writes()
    assert (write.method, write.url.path) == ("POST", "/api/tasks/101/external-links")
    assert "Authorization" not in write.headers  # a trusted-local server takes no credential
    assert json.loads(write.content) == {
        "provider": "agdash",
        "external_key": "codex:codex-1",
        "url": f"http://127.0.0.1:8765/#/history/sessions/{session.id}",
        "title": "Observed Codex session",
    }
    with sessions() as db:
        (record,) = db.scalars(select(PublishedLink)).all()
        assert (record.task_remote_id, record.state, record.remote_link_id) == (
            101,
            "published",
            900,
        )

    # Later passes send nothing more.
    mirror.wake(row.id)
    mirror.tick()
    assert len(server.writes()) == 1

    # Even if the record were lost, WorkChord keeps one link for the session.
    with sessions() as db:
        db.execute(delete(PublishedLink))
        db.commit()
    mirror.wake(row.id)
    mirror.tick()
    assert len(server.writes()) == 2 and len(server.links) == 1
    assert all(LINK_ROUTE.fullmatch(r.url.path) for r in server.writes())


def test_a_refusal_is_recorded_once_and_an_outage_is_retried(store):
    sessions, secrets = store
    server = WorkChord()
    with sessions() as db:
        row = trusted_local(db)
        observed(db, row.id)
        db.commit()
    publisher = Publisher(server.transport)

    server.answer = (503, {"detail": "busy"})
    with sessions() as db:
        assert publisher.run(db, secrets, [db.get(Connection, row.id)]).skipped
        db.commit()
        assert db.scalars(select(PublishedLink)).all() == []

    server.answer = (
        403,
        {"detail": {"code": "permission_denied", "message": "You do not have permission"}},
    )
    with sessions() as db:
        assert publisher.run(db, secrets, [db.get(Connection, row.id)]).refused == 1
        db.commit()
        (record,) = db.scalars(select(PublishedLink)).all()
        assert (record.state, record.detail) == ("refused", "HTTP 403 permission_denied")
        publisher.run(db, secrets, [db.get(Connection, row.id)])
    assert len(server.writes()) == 2


def test_a_server_without_agdash_links_is_never_written_to(store):
    sessions, secrets = store
    server = WorkChord()
    server.routes["/api/auth/me"] = (200, {"mode": "trusted_local", "authenticated": False})
    with sessions() as db:
        row = trusted_local(db)
        row.contract_seen = {"auth_mode": "trusted_local"}
        observed(db, row.id)
        result = Publisher(server.transport).run(db, secrets, [row])
        assert "does not accept agdash links" in result.skipped[row.id]
        row.publish_enabled = False
        with pytest.raises(PublishError):
            enable(db, secrets, row, transport=server.transport)
        assert not row.publish_enabled
    assert server.writes() == []


# -- the writer can do one thing ----------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/tasks/101/external-links"),
        ("PUT", "/api/tasks/101/external-links"),
        ("DELETE", "/api/external-links/900"),
        ("POST", "/api/tasks/101/runs"),
        ("POST", "/api/tasks/101/external-links/extra"),
        ("POST", "/api/tasks/abc/external-links"),
        ("POST", "/api/agent/me/work/begin"),
    ],
)
def test_the_publish_client_refuses_everything_but_the_one_write(method, path):
    sent = []
    transport = httpx.MockTransport(lambda request: sent.append(request) or httpx.Response(200))
    with publish_client("http://127.0.0.1:8480", WRITE_PATH, transport=transport) as client:
        with pytest.raises(WriteRefused):
            client.request(method, path)
    assert sent == []


def test_the_link_points_only_at_this_computer():
    assert link_url(7, "http://localhost:5174/#/history/sessions/{session_id}").endswith("/7")
    for template in (
        "http://192.168.1.4:8765/{session_id}",
        "https://agdash.example/{session_id}",
        "file:///tmp/{session_id}",
        "http://user:pw@127.0.0.1/{session_id}",
    ):
        with pytest.raises(PublishError):
            link_url(7, template)


# -- turning it on ------------------------------------------------------------------


def test_turning_it_on_pairs_a_full_session_kept_apart_from_the_read_one(store, capsys):
    sessions, secrets = store
    server = WorkChord()
    with sessions() as db:
        row = connect(db, secrets)
        observed(db, row.id)
        assert cli.run(["publish", str(row.id), "on"], db, secrets, server.transport) == 0
        assert server.started == [
            {
                "code_challenge": server.started[0]["code_challenge"],
                "access": "full",
                "client_label": "agdash publish mode",
            }
        ]
        assert Paired.load(secrets.get(publish_ref(row.id))).token == PUBLISH_TOKEN
        assert Paired.load(secrets.get(session_ref(row.id))).token == TOKEN
        assert db.get(Connection, row.id).publish_enabled

        Publisher(server.transport).run(db, secrets, [db.get(Connection, row.id)])
        (write,) = [r for r in server.writes() if LINK_ROUTE.fullmatch(r.url.path)]
        assert write.headers["Authorization"] == f"Bearer {PUBLISH_TOKEN}"

        assert cli.run(["publish", str(row.id), "off"], db, secrets, server.transport) == 0
        assert secrets.get(publish_ref(row.id)) is None
        assert not db.get(Connection, row.id).publish_enabled
    assert "AB-12" in capsys.readouterr().out
