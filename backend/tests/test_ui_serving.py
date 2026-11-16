"""The UI served by the service: its files are open, its data still needs the token."""

import pytest
from fastapi.testclient import TestClient
from test_api import TOKEN

from agdash import cli
from agdash.api.ui import open_link, ui_directory
from agdash.db.migrate import upgrade
from agdash.main import create_app


@pytest.fixture
def ui(tmp_path):
    built = tmp_path / "dist"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>agdash</title><div id=root></div>")
    (built / "favicon.svg").write_text("<svg/>")
    (built / "assets" / "index-abc.js").write_text("console.log('ui')")
    (tmp_path / "private.txt").write_text("outside the UI")
    return built


@pytest.fixture
def client(tmp_path, ui):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    with TestClient(
        create_app(url, token=TOKEN, port=8765, ui_dir=str(ui)), base_url="http://127.0.0.1:8765"
    ) as found:
        yield found


def test_the_ui_files_need_no_token_and_the_data_still_does(client):
    page = client.get("/")
    assert page.status_code == 200 and "<div id=root>" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["referrer-policy"] == "no-referrer"
    assert client.get("/favicon.svg").status_code == 200
    script = client.get("/assets/index-abc.js")
    assert script.status_code == 200 and "immutable" in script.headers["cache-control"]
    assert client.get("/api/sessions").status_code == 401
    bearer = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/sessions", headers=bearer).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/assets/../../private.txt",
        "/assets/%2e%2e/%2e%2e/private.txt",
        "/private.txt",
        "/assets/missing.js",
        "/api",
    ],
)
def test_nothing_outside_the_ui_is_served(client, path):
    assert client.get(path).status_code in (401, 404)
    assert "outside the UI" not in client.get(path).text


def test_the_host_and_origin_checks_still_apply_to_the_ui(client):
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 421
    assert client.get("/", headers={"Origin": "https://attacker.example"}).status_code == 403


def test_the_ui_adds_nothing_to_the_api_document(client):
    paths = client.get("/api/openapi.json", headers={"Authorization": f"Bearer {TOKEN}"}).json()
    assert "/" not in paths["paths"] and "/{name}" not in paths["paths"]


def test_without_a_built_ui_only_the_api_is_served(tmp_path):
    assert ui_directory(str(tmp_path / "none")) is None  # and no copy is frozen into a checkout
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    with TestClient(
        create_app(url, token=TOKEN, port=8765, ui_dir=str(tmp_path / "none")),
        base_url="http://127.0.0.1:8765",
    ) as client:
        assert client.get("/").status_code in (401, 404)


def test_the_link_keeps_the_token_in_the_fragment():
    assert open_link(8765, "tok") == "http://127.0.0.1:8765/#agdash-token=tok"


def test_agdash_open_prints_the_link_only_when_the_service_serves_the_ui(monkeypatch, tmp_path):
    from agdash.platform import Host, OperatingSystem

    host = Host(OperatingSystem.LINUX, tmp_path, {})
    printed, errors = [], []
    monkeypatch.setattr(cli, "_health", lambda: None)
    assert cli.open_ui(host, out=printed.append, err=errors.append) == 1
    assert "not answering" in errors[-1] and printed == []

    monkeypatch.setattr(cli, "_health", lambda: {"status": "ok"})
    monkeypatch.setattr(cli, "_serves_ui", lambda: False)
    assert cli.open_ui(host, out=printed.append, err=errors.append) == 1
    assert "serves no UI" in errors[-1] and printed == []

    monkeypatch.setattr(cli, "_serves_ui", lambda: True)
    assert cli.open_ui(host, out=printed.append, err=errors.append) == 0
    assert printed[0].startswith("http://127.0.0.1:8765/#agdash-token=")
    assert len(printed[0].split("=", 1)[1]) >= 32
    assert any("anyone who opens it can read your sessions" in line for line in printed)
    assert any(line.strip().startswith("ssh -N -L 8765:127.0.0.1:8765") for line in printed)
