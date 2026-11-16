"""The WorkChord connection: pairing, stored credentials and the GET-only client.

The server here is a stand-in that follows the contract of WorkChord's
native-connection flow as its code defines it: an S256 proof, a pending state
until a signed-in person approves, ten minutes to finish, and one use only.
"""

import base64
import hashlib
import io
import json
import re
import secrets as random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from agdash.connectors import cli
from agdash.connectors.http import (
    PairingTransport,
    ReadOnlyTransport,
    WriteRefused,
    pairing_client,
    server_origin,
)
from agdash.connectors.workchord import (
    PAIRING_POSTS,
    PairingError,
    WorkChordClient,
    WorkChordConnector,
    finish_pairing,
    open_client,
    pair,
    set_run_key,
    start_pairing,
)
from agdash.db.migrate import upgrade
from agdash.db.models import Connection
from agdash.platform.secrets import FileStore

ORIGIN = "http://127.0.0.1:8480"


class FakeWorkChord:
    """Enough of WorkChord to pair with and read from, recording every request."""

    def __init__(self, mode: str = "managed") -> None:
        self.mode = mode
        self.requests: list[httpx.Request] = []
        self.connections: dict[str, dict] = {}
        self.sessions: dict[str, datetime] = {}
        self.agent_keys = {"agent-key-planning"}

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def approve(self, request_id: str) -> None:
        """What the person does in the browser."""
        self.connections[request_id]["approved"] = True

    def _principal(self, request: httpx.Request):
        bearer = request.headers.get("Authorization", "")
        key = request.headers.get("X-Agent-API-Key")
        found = []
        if bearer:
            token = bearer.removeprefix("Bearer ")
            expires = self.sessions.get(token)
            if expires is None or expires <= datetime.now(UTC):
                return "invalid"
            found.append("person")
        if key:
            if key not in self.agent_keys:
                return "invalid"
            found.append("agent")
        if len(found) > 1:
            return "conflict"
        return found[0] if found else None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        who = self._principal(request)
        if who == "conflict":
            return httpx.Response(401, json={"code": "conflicting_credentials"})
        if path == "/api/auth/me" and method == "GET":
            body = {
                "mode": self.mode,
                "authenticated": who == "person",
                "principal": None,
                "workspace_role": None,
                "projects": {},
                "authentication_error": None,
            }
            if who == "person":
                body.update(
                    principal={"id": 7, "kind": "human", "display_name": "Sam Example"},
                    workspace_role="member",
                    projects={"1": "viewer", "2": "editor"},
                )
            elif who == "invalid":
                body["authentication_error"] = "authentication_required"
            return httpx.Response(200, json=body)
        if path == "/api/auth/native-connections/start" and method == "POST":
            challenge = json.loads(request.content)["code_challenge"]
            assert re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge)
            request_id = random.token_urlsafe(32)[:43]
            expires = datetime.now(UTC) + timedelta(minutes=10)
            self.connections[request_id] = {
                "challenge": challenge,
                "approved": False,
                "consumed": False,
                "expires": expires,
            }
            return httpx.Response(
                201,
                json={
                    "request_id": request_id,
                    "verification_code": "ABCD-EF23",
                    "verification_path": f"/mobile/connect?request={request_id}",
                    "expires_at": expires.isoformat(),
                },
            )
        if path == "/api/auth/native-connections/exchange" and method == "POST":
            data = json.loads(request.content)
            row = self.connections.get(data["request_id"])
            digest = hashlib.sha256(data["code_verifier"].encode()).digest()
            if (
                row is None
                or base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != row["challenge"]
            ):
                return httpx.Response(404, json={"code": "native_connection_unavailable"})
            if row["consumed"] or row["expires"] <= datetime.now(UTC):
                return httpx.Response(410, json={"code": "native_connection_expired"})
            if not row["approved"]:
                return httpx.Response(200, json={"status": "pending"})
            row["consumed"] = True
            token = random.token_urlsafe(32)
            expires = datetime.now(UTC) + timedelta(hours=8)
            self.sessions[token] = expires
            return httpx.Response(
                200,
                json={
                    "access_token": token,
                    "token_type": "Bearer",
                    "expires_at": expires.isoformat(),
                },
            )
        if method != "GET":
            return httpx.Response(405)
        if path.startswith("/api/agent/") and path != "/api/agent/pipeline":
            if who != "agent":
                return httpx.Response(403, json={"code": "agent_scope_required"})
            return httpx.Response(200, json=[] if "assignments" in path else {"ok": True})
        if who is None and self.mode != "trusted_local":
            return httpx.Response(401, json={"code": "authentication_required"})
        if who == "invalid":
            return httpx.Response(401, json={"code": "authentication_required"})
        return httpx.Response(200, json=[{"id": 1}] if not re.search(r"/\d+$", path) else {"id": 1})


@pytest.fixture
def server() -> FakeWorkChord:
    return FakeWorkChord()


@pytest.fixture
def place(tmp_path):
    """A store and a secret file, which a restart opens again from the same paths."""
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    return url, tmp_path / "secrets" / "secrets.json"


def opened(place):
    url, secret_path = place
    engine = create_engine(url)
    return engine, sessionmaker(engine, expire_on_commit=False), FileStore(secret_path)


def approving(server: FakeWorkChord):
    """A sleep that approves the pending connection, as a person would meanwhile."""

    def sleep(_seconds: float) -> None:
        for request_id, row in server.connections.items():
            if not row["consumed"]:
                server.approve(request_id)

    return sleep


# -- pairing ------------------------------------------------------------------------


def test_pairing_completes_and_survives_a_restart(server, place):
    engine, store, secrets = opened(place)
    shown = []
    with store() as db:
        row = pair(
            db,
            secrets,
            ORIGIN + "/api/",
            name="Team",
            show=shown.append,
            transport=server.transport,
            sleep=approving(server),
        )
        db.commit()
        connection_id, ref = row.id, row.credential_ref
    engine.dispose()
    assert shown and shown[0].verification_url.startswith(f"{ORIGIN}/mobile/connect?request=")
    assert ref == f"workchord.{connection_id}.session"

    # A new process: new engine, new secret store object, same files.
    engine, store, secrets = opened(place)
    with store() as db:
        row = db.get(Connection, connection_id)
        access = WorkChordConnector(server.transport).access(db, secrets, row)
        with open_client(secrets, row, server.transport) as client:
            assert client.projects() == [{"id": 1}]
    engine.dispose()
    assert access.authenticated and access.problem is None
    assert (access.mode, access.principal, access.workspace_role, access.projects) == (
        "session",
        "Sam Example",
        "member",
        2,
    )
    assert access.expires_at and access.expires_at > datetime.now(UTC) + timedelta(hours=7)


def test_the_session_is_kept_in_the_secret_store_not_the_database(server, place):
    engine, store, secrets = opened(place)
    with store() as db:
        row = pair(db, secrets, ORIGIN, transport=server.transport, sleep=approving(server))
        db.commit()
        token = json.loads(secrets.get(row.credential_ref))["token"]
    engine.dispose()
    url = place[0].removeprefix("sqlite:///")
    assert token.encode() not in open(url, "rb").read()
    assert token in server.sessions


def test_a_trusted_local_server_is_connected_without_a_credential(place):
    server = FakeWorkChord(mode="trusted_local")
    engine, store, secrets = opened(place)
    with store() as db:
        row = pair(db, secrets, ORIGIN, transport=server.transport)
        db.commit()
        access = WorkChordConnector(server.transport).access(db, secrets, row)
        assert row.credential_ref is None and row.contract_seen["auth_mode"] == "trusted_local"
    engine.dispose()
    assert access.authenticated and access.mode == "open" and access.notes
    assert not [r for r in server.requests if r.url.path.startswith("/api/auth/native")]
    assert all("Authorization" not in r.headers for r in server.requests)


def test_pairing_waits_while_pending_and_gives_up_at_expiry(server):
    pending = start_pairing(ORIGIN, server.transport)
    moments = iter([pending.expires_at - timedelta(minutes=1)] * 3 + [pending.expires_at])
    with pytest.raises(PairingError, match="not approved in time"):
        finish_pairing(
            pending, transport=server.transport, sleep=lambda _s: None, now=lambda: next(moments)
        )


def test_a_connection_is_used_once(server):
    pending = start_pairing(ORIGIN, server.transport)
    server.approve(pending.request_id)
    finish_pairing(pending, transport=server.transport, sleep=lambda _s: None)
    with pytest.raises(PairingError, match="ended"):
        finish_pairing(pending, transport=server.transport, sleep=lambda _s: None)


def test_an_expired_or_missing_session_is_reported_not_used(server, place):
    engine, store, secrets = opened(place)
    with store() as db:
        row = pair(db, secrets, ORIGIN, transport=server.transport, sleep=approving(server))
        stored = json.loads(secrets.get(row.credential_ref))
        stored["expires_at"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
        secrets.set(row.credential_ref, json.dumps(stored))
        before = len(server.requests)
        expired = WorkChordConnector(server.transport).access(db, secrets, row)
        assert len(server.requests) == before, "an expired session is not sent"
        secrets.delete(row.credential_ref)
        missing = WorkChordConnector(server.transport).access(db, secrets, row)
    engine.dispose()
    assert not expired.authenticated and "expired" in expired.problem
    assert not missing.authenticated and "missing" in missing.problem


def test_a_server_that_is_not_workchord_is_refused(place):
    def handler(_request):
        return httpx.Response(200, json={"hello": "world"})

    engine, store, secrets = opened(place)
    with store() as db, pytest.raises(PairingError, match="did not answer as a WorkChord"):
        pair(db, secrets, ORIGIN, transport=httpx.MockTransport(handler))
    engine.dispose()


@pytest.mark.parametrize(
    ("address", "origin"),
    [
        ("http://127.0.0.1:8480/api/", "http://127.0.0.1:8480"),
        ("http://localhost:8480", "http://localhost:8480"),
        ("https://work.example/some/path", "https://work.example"),
        ("https://work.example:8443", "https://work.example:8443"),
    ],
)
def test_addresses_become_origins(address, origin):
    assert server_origin(address) == origin


@pytest.mark.parametrize(
    "address", ["http://work.example", "ftp://work.example", "https://user:pw@work.example", "work"]
)
def test_unsafe_or_odd_addresses_are_refused(address):
    with pytest.raises(ValueError):
        server_origin(address)


# -- the client only reads ----------------------------------------------------------


def every_client_call(client: WorkChordClient) -> None:
    client.me()
    client.projects()
    client.iterations()
    client.task_lookup(q="x")
    client.iteration_tasks(1)
    client.task(1)
    client.team_member_profiles()
    client.pipeline()
    client.assignments(task_id=1)
    client.run(1)
    client.actors()
    client.model_bindings()


def test_the_client_only_ever_sends_get(server):
    token = random.token_urlsafe(16)
    server.sessions[token] = datetime.now(UTC) + timedelta(hours=1)
    with WorkChordClient(
        ORIGIN, session_token=token, run_key="agent-key-planning", transport=server.transport
    ) as client:
        every_client_call(client)
    methods = {request.method for request in server.requests}
    assert methods == {"GET"}, methods


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_the_guard_stops_any_other_method_before_it_is_sent(server, method):
    with WorkChordClient(ORIGIN, session_token="x", transport=server.transport) as client:
        transport = client._main._transport
        assert isinstance(transport, ReadOnlyTransport)
        with pytest.raises(WriteRefused):
            client._main.request(method, "/api/tasks/1", json={"status": "done"})
    assert server.requests == [], "nothing reached the server"


def test_pairing_may_post_only_to_its_two_paths(server):
    with pairing_client(ORIGIN, PAIRING_POSTS, transport=server.transport) as client:
        assert isinstance(client._transport, PairingTransport)
        for method, path in (
            ("POST", "/api/tasks"),
            ("PUT", "/api/auth/native-connections/start"),
            ("POST", "/api/auth/native-connections/x/approve"),
        ):
            with pytest.raises(WriteRefused):
                client.request(method, path, json={})
    assert server.requests == []


def test_the_run_key_travels_alone_and_only_to_run_endpoints(server):
    token = random.token_urlsafe(16)
    server.sessions[token] = datetime.now(UTC) + timedelta(hours=1)
    with WorkChordClient(
        ORIGIN, session_token=token, run_key="agent-key-planning", transport=server.transport
    ) as client:
        every_client_call(client)
    for request in server.requests:
        has_key = "X-Agent-API-Key" in request.headers
        has_bearer = "Authorization" in request.headers
        assert not (has_key and has_bearer)
        agent_only = (
            request.url.path.startswith("/api/agent/") and "pipeline" not in request.url.path
        )
        assert has_key == agent_only, request.url.path


def test_without_an_agent_key_agent_reads_are_not_asked_for(server):
    with WorkChordClient(ORIGIN, transport=server.transport) as client:
        for call in (client.actors, client.model_bindings, lambda: client.run(1)):
            with pytest.raises(PermissionError):
                call()
    assert server.requests == []


def test_without_an_agent_key_assignments_are_asked_of_the_person():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[])

    with WorkChordClient(
        ORIGIN, session_token="person-token", transport=httpx.MockTransport(handler)
    ) as client:
        assert client.assignments() == []
    assert seen[0].url.path == "/api/agent/assignments"
    assert seen[0].headers["Authorization"] == "Bearer person-token"
    assert "X-Agent-API-Key" not in seen[0].headers


def test_redirects_are_not_followed(server):
    def handler(request):
        server.requests.append(request)
        return httpx.Response(302, headers={"Location": "https://elsewhere.example/api/auth/me"})

    with WorkChordClient(
        ORIGIN, session_token="secret-token", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.me()
    assert [r.url.host for r in server.requests] == ["127.0.0.1"]


# -- the command line ---------------------------------------------------------------


def test_the_command_line_pairs_lists_checks_and_forgets(place, capsys, monkeypatch):
    server = FakeWorkChord(mode="trusted_local")
    engine, store, secrets = opened(place)
    with store() as db:
        assert cli.run(["pair", ORIGIN, "--name", "Local"], db, secrets, server.transport) == 0
        assert cli.run(["list"], db, secrets, server.transport) == 0
        assert cli.run(["status", "1"], db, secrets, server.transport) == 0
        monkeypatch.setattr("sys.stdin", io.StringIO("agent-key-planning\n"))
        assert cli.run(["run-key", "1"], db, secrets, server.transport) == 0
        assert secrets.get("workchord.1.run-key") == "agent-key-planning"
        assert cli.run(["forget", "1"], db, secrets, server.transport) == 0
        assert db.scalars(select(Connection)).all() == []
        assert secrets.get("workchord.1.run-key") is None
    engine.dispose()
    printed = capsys.readouterr().out
    assert "open local workspace" in printed and "agent-key-planning" not in printed


def test_set_run_key_clears_as_well(server, place):
    engine, store, secrets = opened(place)
    with store() as db:
        row = pair(db, secrets, ORIGIN, transport=server.transport, sleep=approving(server))
        set_run_key(db, secrets, row, "agent-key-planning")
        assert WorkChordConnector(server.transport).access(db, secrets, row).run_traces
        set_run_key(db, secrets, row, None)
        assert row.run_credential_ref is None and secrets.get(f"workchord.{row.id}.run-key") is None
    engine.dispose()


# -- the access document ------------------------------------------------------------

ACCESS_DOC = Path(__file__).parents[2] / "docs" / "workchord-access.md"


def test_every_endpoint_the_client_reads_is_in_the_access_document(server):
    token = random.token_urlsafe(16)
    server.sessions[token] = datetime.now(UTC) + timedelta(hours=1)
    with WorkChordClient(
        ORIGIN, session_token=token, run_key="agent-key-planning", transport=server.transport
    ) as client:
        every_client_call(client)
    document = ACCESS_DOC.read_text()
    for request in server.requests:
        path = re.sub(r"/\d+(?=/|$)", "/{id}", request.url.path.removeprefix("/api"))
        forms = (f"`GET {path}`", f"`GET /api{path}`", f"`{path}`")
        assert any(form in document for form in forms), path
    assert "8efbcef87" in document, "the WorkChord commit the rules were read from"
