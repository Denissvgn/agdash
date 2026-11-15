"""Local access control and the live stream: loopback only, the token, agdash's own origins."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker

from agdash.api import stream
from agdash.api.security import (
    LazyToken,
    NotLoopback,
    ensure_loopback,
    load_or_create_token,
)
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session
from agdash.main import create_app
from agdash.platform.secrets import FileStore
from agdash.vocab import EventKind, SessionState

TOKEN = "local-token-for-tests"
PORT = 8765
OWN = f"http://127.0.0.1:{PORT}"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def database(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    with sessionmaker(engine)() as db:
        db.add(Session(harness="pi", native_id="first", title="a session"))
        db.commit()
    engine.dispose()
    return url


@pytest.fixture
def client(database):
    with TestClient(create_app(database, token=TOKEN, port=PORT), base_url=OWN) as test_client:
        yield test_client


# -- the token ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/api/sessions", "/api/stats", "/api/stream", "/api/openapi.json"]
)
def test_requests_without_the_right_token_are_refused(client, path):
    missing = client.get(path)
    assert missing.status_code == 401 and missing.headers["www-authenticate"] == "Bearer"
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get(path, headers={"Authorization": TOKEN}).status_code == 401
    assert "a session" not in missing.text


def test_the_right_token_is_let_in(client):
    response = client.get("/api/sessions", headers=BEARER)
    assert response.status_code == 200 and response.json()["items"][0]["title"] == "a session"


def test_health_needs_no_token_and_says_nothing_private(client):
    response = client.get("/health")
    assert response.status_code == 200 and set(response.json()) == {"status", "version"}


def test_the_token_is_created_once_and_kept(tmp_path):
    store = FileStore(tmp_path / "secrets" / "secrets.json")
    first = load_or_create_token(store)
    assert len(first) >= 40 and load_or_create_token(store) == first
    assert load_or_create_token(FileStore(tmp_path / "secrets" / "secrets.json")) == first


def test_the_token_is_read_on_first_use_not_at_start():
    calls = []
    token = LazyToken(lambda: calls.append(1) or "t")
    assert calls == []
    assert token() == token() == "t" and calls == [1]


# -- Host and Origin ----------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["evil.example", f"evil.example:{PORT}", "127.0.0.1:9999", "192.168.1.5:8765", ""]
)
def test_a_foreign_host_is_refused_even_with_the_token(client, host):
    response = client.get("/api/sessions", headers={**BEARER, "Host": host})
    assert response.status_code == 421
    assert client.get("/health", headers={"Host": host}).status_code == 421


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}"])
def test_loopback_hosts_are_let_in(client, host):
    assert client.get("/api/sessions", headers={**BEARER, "Host": host}).status_code == 200


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "null", f"http://evil.example:{PORT}", "http://127.0.0.1:9999"],
)
def test_a_foreign_origin_is_refused_even_with_the_token(client, origin):
    response = client.get("/api/sessions", headers={**BEARER, "Origin": origin})
    assert response.status_code == 403
    preflight = client.options(
        "/api/sessions",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 403


@pytest.mark.parametrize("origin", [OWN, f"http://localhost:{PORT}", "tauri://localhost"])
def test_agdash_origins_are_let_in_without_cors_headers(client, origin):
    response = client.get("/api/sessions", headers={**BEARER, "Origin": origin})
    assert response.status_code == 200
    assert not [h for h in response.headers if h.lower().startswith("access-control-")]


def test_extra_origins_come_from_settings(database, monkeypatch):
    from agdash.config import get_settings

    monkeypatch.setenv("AGDASH_EXTRA_ORIGINS", '["http://127.0.0.1:5173"]')
    get_settings.cache_clear()
    try:
        app = create_app(database, token=TOKEN, port=PORT)
    finally:
        get_settings.cache_clear()
    with TestClient(app, base_url=OWN) as dev:
        ok = dev.get("/api/sessions", headers={**BEARER, "Origin": "http://127.0.0.1:5173"})
    assert ok.status_code == 200


def test_the_stream_is_a_get_route_like_every_data_route():
    for route in stream.router.routes:
        assert isinstance(route, APIRoute) and route.methods <= {"GET", "HEAD"}


# -- loopback only ------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "127.8.9.10", "::1", "[::1]", "localhost"])
def test_loopback_addresses_are_accepted(host):
    ensure_loopback(host)


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "::", "192.168.1.10", "10.0.0.1", "8.8.8.8", "no-such-host.invalid"]
)
def test_other_addresses_are_refused(host):
    with pytest.raises(NotLoopback):
        ensure_loopback(host)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
def test_the_service_refuses_to_start_on_a_non_loopback_address(host, tmp_path):
    port = free_port()
    env = {
        **os.environ,
        "AGDASH_HOST": host,
        "AGDASH_PORT": str(port),
        "AGDASH_DATABASE_URL": f"sqlite:///{tmp_path / 'agdash.db'}",
    }
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-m", "agdash.serve"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=Path(__file__).parents[1],
    )
    assert done.returncode == 2
    assert "refusing to start" in done.stderr and "loopback" in done.stderr
    assert time.monotonic() - started < 30
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "nothing listens on the port"


# -- the live stream ----------------------------------------------------------------


class Running:
    """The app served by uvicorn on a free loopback port, as the service runs it."""

    def __init__(self, app, port: int) -> None:
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=1,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline, "the server did not start"
            time.sleep(0.05)
        return self

    def __exit__(self, *_exc):
        self.server.should_exit = True
        self.thread.join(10)


def frames(response: httpx.Response):
    """(event, data) pairs as they arrive."""
    name = None
    for line in response.iter_lines():
        if line.startswith("event: "):
            name = line.removeprefix("event: ")
        elif line.startswith("data: ") and name:
            yield name, json.loads(line.removeprefix("data: "))
            name = None


def test_a_state_change_reaches_a_connected_client_within_two_seconds(database):
    port = free_port()
    app = create_app(database, token=TOKEN, port=port)
    writer = create_engine(database)
    with (
        Running(app, port),
        httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=BEARER, timeout=10) as http,
    ):
        with http.stream("GET", "/api/stream") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            incoming = frames(response)
            assert next(incoming)[0] == "hello"

            with sessionmaker(writer)() as db:
                session = db.query(Session).one()
                session.state = SessionState.ACTIVE
                db.commit()
                session_id = session.id
            changed_at = time.monotonic()
            for name, data in incoming:
                if name == "sessions" and any(
                    s["id"] == session_id and s["state"] == "active" for s in data["sessions"]
                ):
                    break
            assert time.monotonic() - changed_at < 2.0

            with sessionmaker(writer)() as db:
                db.execute(
                    insert(Event),
                    [
                        {
                            "session_id": session_id,
                            "sequence": n,
                            "kind": EventKind.STATUS,
                            "payload": {"status": "line"},
                        }
                        for n in (0, 32, 64)
                    ],
                )
                db.commit()
            written_at = time.monotonic()
            for name, data in incoming:
                if name == "events":
                    (grown,) = data["sessions"]
                    assert grown == {"id": session_id, "newest_sequence": 64, "new_events": 3}
                    break
            assert time.monotonic() - written_at < 2.0
    writer.dispose()


def test_the_stream_refuses_a_client_without_the_token(database):
    port = free_port()
    with Running(create_app(database, token=TOKEN, port=port), port):
        response = httpx.get(f"http://127.0.0.1:{port}/api/stream", timeout=5)
        foreign = httpx.get(
            f"http://127.0.0.1:{port}/api/stream",
            headers={**BEARER, "Origin": "https://evil.example"},
            timeout=5,
        )
    assert response.status_code == 401 and foreign.status_code == 403
