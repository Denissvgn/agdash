"""The connector settings the app changes: pairing, rules and pins, and nothing else.

The WorkChord server is the connector tests' stand-in; every id, name, folder
and token is synthetic.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker
from test_api import TOKEN
from test_workchord_connector import FakeWorkChord

from agdash.api.store import WRITABLE, settings_engine
from agdash.db.migrate import upgrade
from agdash.db.models import (
    Connection,
    Event,
    MappingRule,
    ProjectLink,
    Session,
    WcProject,
)
from agdash.main import create_app
from agdash.platform.secrets import FileStore
from agdash.vocab import EventKind, SessionState

T0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
ADDRESS = "http://127.0.0.1:8480"


@pytest.fixture
def url(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    return url


@pytest.fixture
def secrets(tmp_path) -> FileStore:
    return FileStore(tmp_path / "secrets.json")


def app_client(url, secrets, server: FakeWorkChord) -> TestClient:
    app = create_app(url, token=TOKEN, port=8765)
    app.state.secrets = secrets
    app.state.workchord_transport = server.transport
    return TestClient(
        app, base_url="http://127.0.0.1:8765", headers={"Authorization": f"Bearer {TOKEN}"}
    )


def seed(url) -> dict[str, int]:
    """A connected server with two projects, and two sessions that never called it."""
    engine = create_engine(url)
    with sessionmaker(engine)() as db:
        row = Connection(
            kind="workchord", base_url=ADDRESS, contract_seen={"auth_mode": "trusted_local"}
        )
        db.add(row)
        db.flush()
        db.add_all(
            [
                WcProject(connection_id=row.id, remote_id=1, name="Orchard", fetched_at=T0),
                WcProject(connection_id=row.id, remote_id=2, name="Meadow", fetched_at=T0),
            ]
        )
        inside = Session(
            harness="codex", native_id="a", cwd="/work/orchard/app", state=SessionState.ENDED
        )
        elsewhere = Session(harness="pi", native_id="b", cwd="/srv/x", state=SessionState.ENDED)
        db.add_all([inside, elsewhere])
        db.commit()
        ids = {"connection": row.id, "inside": inside.id, "elsewhere": elsewhere.id}
    engine.dispose()
    return ids


def project_of(client, session_id) -> dict:
    (only,) = client.get(f"/api/sessions/{session_id}/mapping").json()["connections"]
    return only["project"]


# -- pairing ------------------------------------------------------------------------


def test_a_managed_server_is_paired_from_the_app(url, secrets):
    server = FakeWorkChord()
    server.features = ["observer-sessions-v1"]
    with app_client(url, secrets, server) as client:
        started = client.post("/api/workchord/pairings", json={"address": ADDRESS, "name": "Team"})
        assert started.status_code == 202
        waiting = started.json()
        assert waiting["status"] == "waiting" and waiting["read_only"] is True
        assert waiting["verification_code"] == "ABCD-EF23"
        assert waiting["verification_url"].startswith(ADDRESS + "/mobile/connect")
        assert "verifier" not in started.text  # the private half never leaves the service

        finish = f"/api/workchord/pairings/{waiting['pairing_id']}/finish"
        assert client.post(finish).status_code == 202  # not approved yet
        for request_id in server.connections:
            server.approve(request_id)
        done = client.post(finish)
        assert done.status_code == 201 and done.json()["status"] == "connected"
        assert client.post(finish).status_code == 404  # used once

        (listed,) = client.get("/api/workchord/connections").json()
        assert (listed["name"], listed["credential"], listed["server"]) == (
            "Team",
            "read_only_session",
            ADDRESS,
        )
    assert secrets.get(f"workchord.{listed['id']}.session")
    # WorkChord saw only GETs and the two pairing requests.
    assert {(r.method, r.url.path) for r in server.requests if r.method != "GET"} == {
        ("POST", "/api/auth/native-connections/start"),
        ("POST", "/api/auth/native-connections/exchange"),
    }


def test_a_trusted_local_server_connects_at_once(url, secrets):
    with app_client(url, secrets, FakeWorkChord(mode="trusted_local")) as client:
        answer = client.post("/api/workchord/pairings", json={"address": ADDRESS})
        assert answer.status_code == 201 and answer.json()["status"] == "connected"
        (listed,) = client.get("/api/workchord/connections").json()
        assert listed["credential"] == "none" and listed["auth_mode"] == "trusted_local"


def test_bad_addresses_and_unknown_pairings_are_refused(url, secrets):
    with app_client(url, secrets, FakeWorkChord()) as client:
        bad = client.post("/api/workchord/pairings", json={"address": "http://work.example"})
        assert bad.status_code == 422 and "https" in bad.json()["detail"]
        assert client.post("/api/workchord/pairings/nope/finish").status_code == 404


# -- rules and pins -----------------------------------------------------------------


def test_a_folder_rule_and_a_pin_map_sessions_from_the_app(url, secrets):
    ids = seed(url)
    cid = ids["connection"]
    with app_client(url, secrets, FakeWorkChord()) as client:
        assert [
            p["name"] for p in client.get(f"/api/workchord/connections/{cid}/projects").json()
        ] == [
            "Meadow",
            "Orchard",
        ]
        made = client.post(
            f"/api/workchord/connections/{cid}/rules",
            json={"match": "folder", "value": "/work/orchard", "project_id": 1},
        )
        assert made.status_code == 201
        rule = made.json()
        assert (rule["match"], rule["target_name"]) == ("folder", "Orchard")
        mapped = project_of(client, ids["inside"])
        assert (mapped["state"], mapped["name"], mapped["basis"]) == ("mapped", "Orchard", "rule")

        (queued,) = client.get("/api/mapping/unmapped").json()["items"]
        assert queued["session_id"] == ids["elsewhere"] and queued["evidence"] == []
        pinned = client.post(
            f"/api/workchord/connections/{cid}/pins",
            json={"session_id": ids["elsewhere"], "project_id": 2},
        )
        assert pinned.status_code == 201
        assert project_of(client, ids["elsewhere"])["basis"] == "manual"
        assert client.get("/api/mapping/unmapped").json()["items"] == []

        # A second pin replaces the first; removing it puts the session back in the queue.
        again = client.post(
            f"/api/workchord/connections/{cid}/pins",
            json={"session_id": ids["elsewhere"], "project_id": 1},
        ).json()
        rules = client.get(f"/api/workchord/connections/{cid}/rules").json()
        assert [r["match"] for r in rules] == ["folder", "session"]
        assert client.delete(f"/api/workchord/rules/{again['id']}").status_code == 204
        assert [i["session_id"] for i in client.get("/api/mapping/unmapped").json()["items"]] == [
            ids["elsewhere"]
        ]


def test_conflicts_in_the_queue_name_each_basis(url, secrets):
    ids = seed(url)
    cid = ids["connection"]
    with app_client(url, secrets, FakeWorkChord()) as client:
        for value, project in (("/work", 2), ("/work/orchard", 1)):
            client.post(
                f"/api/workchord/connections/{cid}/rules",
                json={"match": "folder", "value": value, "project_id": project},
            )
        (queued,) = [
            i
            for i in client.get("/api/mapping/unmapped").json()["items"]
            if i["session_id"] == ids["inside"]
        ]
        assert queued["reason"] == "conflicting evidence"
        assert {(e["basis"], e["name"]) for e in queued["evidence"]} == {
            ("rule", "Orchard"),
            ("rule", "Meadow"),
        }


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"match": "folder", "value": "work/orchard", "project_id": 1}, 422),
        ({"match": "folder", "value": "/work", "project_id": 99}, 422),
        ({"match": "folder", "value": "/work"}, 422),
        ({"match": "session", "value": "1", "project_id": 1}, 422),
    ],
    ids=["relative", "unknown-project", "no-target", "pin-as-rule"],
)
def test_bad_rules_are_refused(url, secrets, body, status):
    ids = seed(url)
    with app_client(url, secrets, FakeWorkChord()) as client:
        answer = client.post(f"/api/workchord/connections/{ids['connection']}/rules", json=body)
        assert answer.status_code == status
        assert client.get(f"/api/workchord/connections/{ids['connection']}/rules").json() == []


def test_writes_need_the_token_and_agdash_s_own_origin(url, secrets):
    ids = seed(url)
    rule = {"match": "folder", "value": "/work", "project_id": 1}
    path = f"/api/workchord/connections/{ids['connection']}/rules"
    with app_client(url, secrets, FakeWorkChord()) as client:
        assert (
            client.post(path, json=rule, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
        assert (
            client.post(path, json=rule, headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
        assert client.get(path).json() == []


# -- the writer can change only the connector's tables ------------------------------


def test_the_settings_writer_cannot_touch_collected_data(url):
    ids = seed(url)
    engine = settings_engine(url)
    with sessionmaker(engine)() as db:
        db.add(
            MappingRule(
                connection_id=ids["connection"],
                match_kind="folder",
                match_value="/x",
                target_kind="project",
                target_value="1",
            )
        )
        db.commit()  # its own table: allowed
        for statement in (
            "UPDATE sessions SET title = 'changed'",
            "DELETE FROM events",
            "INSERT INTO wc_projects (connection_id, remote_id, name, fetched_at) "
            "VALUES (1, 9, 'x', '2026-01-01')",
            "DROP TABLE project_links",
            "CREATE TABLE extra (id INTEGER)",
        ):
            with pytest.raises(DatabaseError):
                db.execute(text(statement))
            db.rollback()
        db.add(
            Event(
                session_id=ids["inside"], sequence=0, kind=EventKind.STATUS, payload={"status": "x"}
            )
        )
        with pytest.raises(DatabaseError):
            db.commit()
        db.rollback()
        assert db.scalar(select(Session.title).where(Session.id == ids["inside"])) is None
        assert db.scalars(select(ProjectLink)).all() == []
    engine.dispose()
    assert WRITABLE == {
        "connections",
        "mapping_rules",
        "identity_links",
        "project_links",
        "settings",
    }
