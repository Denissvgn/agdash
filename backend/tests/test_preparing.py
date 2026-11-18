"""While the store is brought up to date, the service's port says so."""

import json
import socket
import time
import urllib.error
import urllib.request

import pytest
from fastapi.testclient import TestClient

from agdash import __version__, cli
from agdash.api.preparing import WhilePreparing, preparing_app
from agdash.config import Settings
from agdash.platform import Host, OperatingSystem
from agdash.platform.service import ServiceState

PORT = 8765


@pytest.fixture
def client() -> TestClient:
    return TestClient(preparing_app(PORT), base_url=f"http://127.0.0.1:{PORT}")


def test_health_says_preparing_and_everything_else_says_to_ask_again(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "preparing", "version": __version__}
    for path in ("/api/sessions", "/api/stream", "/", "/api/storage/settings"):
        answer = client.get(path)
        assert answer.status_code == 503, path
        assert answer.json() == {
            "detail": "the service is preparing its store",
            "status": "preparing",
        }
        assert answer.headers["retry-after"] == "5"
        assert answer.headers["cache-control"] == "no-store"
    assert client.post("/api/storage/settings", json={"keep_raw": False}).status_code == 503


def test_it_asks_for_no_token_and_never_reaches_for_one(client, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("nothing is read from the secret store while preparing")

    monkeypatch.setattr(Host, "secret_store", refuse)
    monkeypatch.setattr("agdash.api.security.load_or_create_token", refuse)
    assert client.get("/api/sessions").status_code == 503
    assert client.get("/api/sessions", headers={"Authorization": "Bearer x"}).status_code == 503


def test_the_host_and_origin_checks_still_hold(client):
    assert client.get("/health", headers={"Host": "example.invalid"}).status_code == 421
    foreign = client.get("/api/sessions", headers={"Origin": "https://example.invalid"})
    assert foreign.status_code == 403 and "access-control-allow-origin" not in foreign.headers
    own = client.get("/api/sessions", headers={"Origin": "tauri://localhost"})
    assert own.status_code == 503, "the app's page can read why"
    assert own.headers["access-control-allow-origin"] == "tauri://localhost"
    asked = client.options(
        "/api/sessions",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert asked.status_code == 204


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:  # noqa: S310
            return json.loads(response.read())
    except (urllib.error.URLError, OSError):
        return None


def test_it_listens_only_while_preparing_takes_long_and_then_frees_the_port():
    port = free_port()
    settings = Settings(port=port)
    with WhilePreparing(settings, after=0):
        deadline = time.monotonic() + 5
        while health(port) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert health(port) == {"status": "preparing", "version": __version__}
    assert not listening(port), "the service proper can take the port"
    # Preparing that is over in a moment never listens at all.
    quick = WhilePreparing(settings, after=30)
    with quick:
        pass
    assert quick._server is None and not listening(port)


def test_the_commands_say_that_the_service_is_preparing(tmp_path, monkeypatch, capsys):
    host = Host(OperatingSystem.LINUX, tmp_path, {})
    monkeypatch.setenv("AGDASH_DATABASE_URL", f"sqlite:///{tmp_path / 'agdash.db'}")
    cli.get_settings.cache_clear()
    monkeypatch.setattr(cli, "_health", lambda: {"status": "preparing", "version": "9.9"})

    class Manager:
        unit_path = tmp_path / "unit"

        def state(self):
            return ServiceState.RUNNING

    try:
        assert cli.status(host, Manager()) == 0
        said = capsys.readouterr().out
        assert "Answering: not yet" in said and "preparing its store" in said
        answering = [
            check for check in cli.doctor_checks(host, Manager()) if check.name == "Answering"
        ]
        assert [(check.level, "preparing its store" in check.detail) for check in answering] == [
            ("warn", True)
        ]
        errors: list[str] = []
        assert cli.open_ui(host, out=lambda _text: None, err=errors.append) == 1
        assert "preparing its store" in errors[0]
    finally:
        cli.get_settings.cache_clear()
