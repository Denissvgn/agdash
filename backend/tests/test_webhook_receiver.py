"""The WorkChord webhook receiver: signed deliveries wake a sync, everything else is refused.

Deliveries are built the way WorkChord builds them: a compact JSON body, signed
with HMAC-SHA256 over the raw bytes, sent as ``sha256=<hex>``. Every id, name
and secret here is synthetic.
"""

import hashlib
import hmac
import json
import socket
import threading

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agdash import runtime
from agdash.config import Settings
from agdash.connectors import cli
from agdash.connectors.mirror import MirrorSync
from agdash.connectors.webhook import create_receiver
from agdash.connectors.workchord import webhook_ref
from agdash.db.migrate import upgrade
from agdash.db.models import Connection
from agdash.platform.secrets import FileStore

SECRET = "synthetic-signing-secret"


def delivery(event_id="e1f2a3b4c5d6", event_type="task.updated", **data) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "entity_type": "task",
        "entity_id": 101,
        "occurred_at": "2026-11-16T09:00:00+00:00",
        "data": {"task_id": 101, **data},
    }


def encode(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def signed(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def headers(payload: dict, body: bytes, *, signature: str | None = "auto") -> dict:
    found = {
        "Content-Type": "application/json",
        "X-WorkChord-Event": payload["event_type"],
        "X-WorkChord-Event-Id": payload["event_id"],
        "X-WorkChord-Delivery-Id": "7",
    }
    if signature == "auto":
        found["X-WorkChord-Signature-256"] = signed(body)
    elif signature is not None:
        found["X-WorkChord-Signature-256"] = signature
    return found


@pytest.fixture
def setup(tmp_path):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    secrets = FileStore(tmp_path / "secrets.json")
    with sessions() as db:
        row = Connection(kind="workchord", base_url="http://127.0.0.1:8480")
        db.add(row)
        db.commit()
        connection_id = row.id
    secrets.set(webhook_ref(connection_id), SECRET)
    woken: list[int] = []
    client = TestClient(create_receiver(sessions, secrets, woken.append))
    yield client, connection_id, woken, secrets, sessions
    engine.dispose()


def post(client, connection_id, payload=None, body=None, **options):
    payload = payload or delivery()
    body = encode(payload) if body is None else body
    return client.post(
        f"/workchord/{connection_id}", content=body, headers=headers(payload, body, **options)
    )


def test_a_signed_delivery_wakes_a_sync_of_its_connection(setup):
    client, connection_id, woken, *_ = setup
    response = post(client, connection_id)
    assert response.status_code == 204 and woken == [connection_id]


@pytest.mark.parametrize(
    "signature",
    [
        None,
        "",
        "sha256=",
        "sha256=" + "0" * 64,
        "sha1=whatever",
        "auto-wrong-secret",
        "auto-uppercase",
    ],
    ids=["missing", "empty", "no-digest", "wrong-digest", "other-scheme", "wrong-secret", "case"],
)
def test_a_missing_or_wrong_signature_is_refused(setup, signature):
    client, connection_id, woken, *_ = setup
    payload = delivery()
    body = encode(payload)
    if signature == "auto-wrong-secret":
        signature = signed(body, "another-secret")
    elif signature == "auto-uppercase":
        signature = signed(body).upper()
    response = post(client, connection_id, payload, body, signature=signature)
    assert response.status_code == 401 and response.content == b""
    assert woken == []


def test_a_body_changed_after_signing_is_refused(setup):
    client, connection_id, woken, *_ = setup
    payload = delivery()
    signature = signed(encode(payload))
    tampered = encode(delivery(changes={"status": "done"}))
    response = post(client, connection_id, payload, tampered, signature=signature)
    assert response.status_code == 401 and woken == []


def test_a_connection_without_a_secret_or_unknown_refuses_everything(setup):
    client, connection_id, woken, secrets, _ = setup
    secrets.delete(webhook_ref(connection_id))
    assert post(client, connection_id).status_code == 401
    assert post(client, connection_id + 41).status_code == 401
    assert woken == []


def test_a_repeated_event_changes_nothing_and_a_test_delivery_syncs_nothing(setup):
    client, connection_id, woken, *_ = setup
    assert post(client, connection_id).status_code == 204
    assert post(client, connection_id).status_code == 204  # WorkChord's retry, or a replay
    assert woken == [connection_id]
    ping = delivery(event_id="f0e1d2c3", event_type="webhook.test")
    assert post(client, connection_id, ping).status_code == 204
    assert woken == [connection_id]
    unnamed = {k: v for k, v in headers(delivery(), encode(delivery())).items()}
    unnamed.pop("X-WorkChord-Event-Id")
    response = client.post(
        f"/workchord/{connection_id}", content=encode(delivery()), headers=unnamed
    )
    assert response.status_code == 401


def test_oversized_bodies_and_other_methods_are_refused(setup):
    client, connection_id, woken, *_ = setup
    huge = delivery(note="x" * (1024 * 1024))
    assert post(client, connection_id, huge).status_code == 413
    assert client.get(f"/workchord/{connection_id}").status_code == 405
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/api/sessions").status_code == 404
    assert woken == []


def test_waking_puts_the_connection_at_the_front_of_the_mirror(setup):
    *_, secrets, sessions = setup
    clock = {"now": 100.0}
    mirror = MirrorSync(sessions, secrets, clock=lambda: clock["now"])
    mirror._next[1] = 160.0
    assert not mirror.due(1)
    mirror.wake(1)
    assert mirror.due(1)


# -- the listener -------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_with_the_receiver_off_no_listener_is_opened(setup):
    *_, secrets, sessions = setup
    before = {thread.name for thread in threading.enumerate()}
    assert Settings().webhook_port is None
    assert runtime.start_receiver(Settings(), sessions, secrets, lambda _: None) is None
    assert {thread.name for thread in threading.enumerate()} == before


def test_with_the_receiver_on_it_listens_on_loopback_alone(setup):
    *_, secrets, sessions = setup
    port = free_port()
    woken: list[int] = []
    settings = Settings(webhook_port=port, port=free_port())
    server, thread = runtime.start_receiver(settings, sessions, secrets, woken.append)
    try:
        for _ in range(200):
            if server.started:
                break
            threading.Event().wait(0.02)
        payload = delivery()
        body = encode(payload)
        response = httpx.post(
            f"http://127.0.0.1:{port}/workchord/1", content=body, headers=headers(payload, body)
        )
        assert response.status_code == 204 and woken == [1]
    finally:
        server.should_exit = True
        thread.join(5)


def test_the_receiver_refuses_a_shared_port_or_an_outside_address(setup):
    *_, secrets, sessions = setup
    with pytest.raises(ValueError):
        runtime.start_receiver(
            Settings(webhook_port=8765, port=8765), sessions, secrets, lambda _: None
        )
    with pytest.raises(runtime.NotLoopback):
        runtime.start_receiver(
            Settings(host="0.0.0.0", webhook_port=8766), sessions, secrets, lambda _: None
        )


# -- the secret ---------------------------------------------------------------------


def test_the_secret_is_set_cleared_and_forgotten_with_its_connection(setup, monkeypatch, capsys):
    _, connection_id, _, secrets, sessions = setup
    monkeypatch.setattr("sys.stdin", _Lines(f"  {SECRET}-2  \n"))
    with sessions() as db:
        assert cli.run(["webhook-secret", str(connection_id)], db, secrets) == 0
    assert secrets.get(webhook_ref(connection_id)) == f"{SECRET}-2"
    assert f"/workchord/{connection_id}" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", _Lines("\n"))
    with sessions() as db:
        cli.run(["webhook-secret", str(connection_id)], db, secrets)
    assert secrets.get(webhook_ref(connection_id)) is None
    secrets.set(webhook_ref(connection_id), SECRET)
    with sessions() as db:
        assert cli.run(["forget", str(connection_id)], db, secrets) == 0
    assert secrets.get(webhook_ref(connection_id)) is None


class _Lines:
    def __init__(self, text: str) -> None:
        self._text = text

    def isatty(self) -> bool:
        return False

    def readline(self) -> str:
        return self._text
