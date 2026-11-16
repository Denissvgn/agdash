"""Sessions mapped to WorkChord actors and projects, with the evidence for each.

The WorkChord copy, the transcripts and the rules are built by hand; every id,
name, folder and address is synthetic.
"""

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from test_api import local_client

from agdash.adapters.codex import repository_address
from agdash.connectors import cli
from agdash.connectors.resolver import Candidate, Resolver, decide, inside, repository_key
from agdash.db.migrate import upgrade
from agdash.db.models import (
    Connection,
    Event,
    IdentityLink,
    MappingRule,
    ProjectLink,
    RunLink,
    Session,
    SessionProcess,
    WcActor,
    WcAssignment,
    WcProject,
    WcRun,
    WcTask,
)
from agdash.platform.secrets import FileStore
from agdash.vocab import (
    EventKind,
    MirrorObject,
    RuleMatchKind,
    RuleTargetKind,
    RunLinkBasis,
    SessionState,
)

T0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
ORDER = ["task", "assignment", "rule", "manual"]


@pytest.fixture
def url(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    return url


@pytest.fixture
def store(url):
    engine = create_engine(url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def server(store) -> int:
    """A connection with two projects, their tasks, two actors and their assignments."""
    with store() as db:
        row = Connection(kind="workchord", base_url="http://127.0.0.1:8480")
        db.add(row)
        db.flush()
        cid = row.id
        db.add_all(
            [
                WcProject(connection_id=cid, remote_id=1, name="Orchard", fetched_at=T0),
                WcProject(connection_id=cid, remote_id=2, name="Meadow", fetched_at=T0),
                WcTask(
                    connection_id=cid,
                    remote_id=101,
                    project_remote_id=1,
                    external_key="ORC-1",
                    title="Prune the rows",
                    fetched_at=T0,
                ),
                WcTask(
                    connection_id=cid, remote_id=201, project_remote_id=2, title="t", fetched_at=T0
                ),
                WcActor(
                    connection_id=cid,
                    remote_id=31,
                    name="robin",
                    display_name="Robin (synthetic)",
                    runtime_ref="runtime://backend-worker",
                    fetched_at=T0,
                ),
                WcActor(connection_id=cid, remote_id=32, name="wren", fetched_at=T0),
                WcAssignment(
                    connection_id=cid,
                    remote_id=401,
                    actor_remote_id=31,
                    task_remote_id=101,
                    project_remote_id=1,
                    status="executing",
                    fetched_at=T0,
                ),
                WcAssignment(
                    connection_id=cid,
                    remote_id=402,
                    actor_remote_id=31,
                    task_remote_id=201,
                    project_remote_id=2,
                    status="completed",  # over, so it does not count
                    fetched_at=T0,
                ),
                WcRun(
                    connection_id=cid,
                    remote_id=501,
                    task_remote_id=101,
                    actor_remote_id=31,
                    started_at=T0,
                    fetched_at=T0,
                ),
            ]
        )
        db.commit()
    return cid


def session(db, native, *, harness="codex", cwd=None, repo=None, parent=None) -> Session:
    found = Session(
        harness=harness,
        native_id=native,
        cwd=cwd,
        repo=repo,
        parent_session_id=parent,
        state=SessionState.ENDED,
        last_activity_at=T0,
    )
    db.add(found)
    db.flush()
    return found


def rule(db, cid, kind, value, target_kind, target, **fields) -> MappingRule:
    made = MappingRule(
        connection_id=cid,
        match_kind=kind,
        match_value=value,
        target_kind=target_kind,
        target_value=str(target),
        **fields,
    )
    db.add(made)
    db.flush()
    return made


def resolve(store, cid, resolver=None) -> None:
    with store() as db:
        (resolver or Resolver()).run(db, [db.get(Connection, cid)])
        db.commit()


def mapping(url, session_id) -> dict:
    with local_client(url) as client:
        response = client.get(f"/api/sessions/{session_id}/mapping")
    assert response.status_code == 200, response.text
    (only,) = response.json()["connections"]
    return only


def queue(url, **params) -> dict:
    with local_client(url) as client:
        response = client.get("/api/mapping/unmapped", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# -- rules: sessions that never called WorkChord -------------------------------------


def test_a_folder_rule_maps_a_session_that_never_called_workchord(url, store, server):
    with store() as db:
        inside_it = session(db, "a", cwd="/work/orchard/app")
        beside_it = session(db, "b", cwd="/work/orchard-2")
        rule(db, server, RuleMatchKind.FOLDER, "/work/orchard", RuleTargetKind.PROJECT, 1)
        db.commit()
    resolve(store, server)
    project = mapping(url, inside_it.id)["project"]
    assert (project["state"], project["target"], project["name"], project["basis"]) == (
        "mapped",
        1,
        "Orchard",
        "rule",
    )
    assert project["evidence"][0]["evidence"]["match"] == "folder"
    assert mapping(url, beside_it.id)["project"]["state"] == "unmapped"


def test_a_repository_rule_matches_however_the_address_is_spelled(url, store, server):
    with store() as db:
        found = session(db, "a", repo="https://github.example/acme/orchard.git")
        rule(
            db,
            server,
            RuleMatchKind.REPOSITORY,
            "git@github.example:acme/orchard",
            RuleTargetKind.PROJECT,
            2,
            harness="codex",
        )
        other = session(db, "b", harness="pi", repo="https://github.example/acme/orchard")
        db.commit()
    resolve(store, server)
    assert mapping(url, found.id)["project"]["target"] == 2
    assert mapping(url, other.id)["project"]["state"] == "unmapped"  # the rule is codex only


# -- no evidence ----------------------------------------------------------------------


def test_a_session_with_no_evidence_is_unmapped_and_queued(url, store, server):
    with store() as db:
        alone = session(db, "alone", cwd="/elsewhere")
        child = session(db, "child", parent=alone.id)
        mapped = session(db, "mapped", cwd="/work/orchard")
        rule(db, server, RuleMatchKind.FOLDER, "/work/orchard", RuleTargetKind.PROJECT, 1)
        db.commit()
    resolve(store, server)
    found = mapping(url, alone.id)
    assert found["actor"] == {
        "state": "unmapped",
        "target": None,
        "name": None,
        "basis": None,
        "suggestion": None,
        "evidence": [],
    }
    assert found["project"]["state"] == "unmapped"
    items = queue(url)["items"]
    assert [(i["session_id"], i["reason"]) for i in items] == [(alone.id, "no evidence")]
    assert child.id not in {i["session_id"] for i in items} and mapped.id not in {
        i["session_id"] for i in items
    }


# -- evidence, bases and conflicts ----------------------------------------------------


def exact_run(db, cid, session_id, run_id=501, task_id=101) -> None:
    db.add(
        RunLink(
            session_id=session_id,
            connection_id=cid,
            run_remote_id=run_id,
            task_remote_id=task_id,
            basis=RunLinkBasis.TOOL_CALL,
            confidence=1.0,
            evidence={"actor_id": 31},
        )
    )
    db.flush()


def test_each_mapping_stores_its_basis(url, store, server):
    with store() as db:
        worker = session(db, "w")
        exact_run(db, server, worker.id)
        db.commit()
    resolve(store, server)
    with store() as db:
        actors = db.scalars(select(IdentityLink)).all()
        projects = db.scalars(select(ProjectLink)).all()
        assert {(r.actor_remote_id, r.basis.value) for r in actors} == {(31, "run")}
        assert {(r.project_remote_id, r.basis.value) for r in projects} == {
            (1, "task"),
            (1, "assignment"),
        }
    found = mapping(url, worker.id)
    assert (found["actor"]["state"], found["actor"]["basis"], found["actor"]["name"]) == (
        "mapped",
        "run",
        "Robin (synthetic)",
    )
    assert (found["project"]["target"], found["project"]["basis"]) == (1, "task")
    assert [e["basis"] for e in found["project"]["evidence"]] == ["task", "assignment"]
    (run,) = found["runs"]
    assert (run["task_key"], run["task_title"], run["suggestion"]) == (
        "ORC-1",
        "Prune the rows",
        False,
    )


def test_conflicting_evidence_is_surfaced_and_a_pin_settles_it(url, store, server, tmp_path):
    with store() as db:
        worker = session(db, "w", cwd="/work/meadow")
        exact_run(db, server, worker.id)
        rule(db, server, RuleMatchKind.FOLDER, "/work/meadow", RuleTargetKind.PROJECT, 2)
        db.commit()
    resolve(store, server)
    project = mapping(url, worker.id)["project"]
    assert (project["state"], project["target"], project["suggestion"]) == ("conflict", None, 1)
    assert {(e["basis"], e["target"]) for e in project["evidence"]} == {
        ("task", 1),
        ("assignment", 1),
        ("rule", 2),
    }
    (queued,) = queue(url)["items"]
    assert (queued["session_id"], queued["reason"], queued["suggestion"]) == (
        worker.id,
        "conflicting evidence",
        1,
    )

    with store() as db:
        assert (
            cli.run(["pin", str(server), str(worker.id), "--project", "2"], db, _secrets(tmp_path))
            == 0
        )
    resolve(store, server)
    project = mapping(url, worker.id)["project"]
    assert (project["state"], project["target"], project["basis"]) == ("mapped", 2, "manual")
    assert {e["basis"] for e in project["evidence"]} == {"manual", "task", "assignment", "rule"}
    assert queue(url)["items"] == []


def test_the_calling_actor_named_by_capabilities_disagrees_with_the_run(url, store, server):
    with store() as db:
        worker = session(db, "w", harness="claude_code")
        exact_run(db, server, worker.id)
        for sequence, (kind, payload) in enumerate(
            [
                (EventKind.TOOL_CALL, {"name": "mcp__wc__agent_get_capabilities", "call_id": "k"}),
                (
                    EventKind.TOOL_RESULT,
                    {
                        "call_id": "k",
                        "output": [
                            {
                                "type": "text",
                                "text": json.dumps({"actor": {"id": 32, "name": "wren"}}),
                            }
                        ],
                    },
                ),
            ]
        ):
            db.add(
                Event(
                    session_id=worker.id,
                    sequence=sequence,
                    occurred_at=T0,
                    kind=kind,
                    payload=payload,
                )
            )
        db.commit()
    resolver = Resolver()
    resolve(store, server, resolver)
    actor = mapping(url, worker.id)["actor"]
    assert (actor["state"], actor["suggestion"]) == ("conflict", 32)
    assert [(e["basis"], e["target"]) for e in actor["evidence"]] == [
        ("capabilities", 32),
        ("run", 31),
    ]
    # Without a mapped actor, no project comes from assignments.
    project = mapping(url, worker.id)["project"]
    assert [e["basis"] for e in project["evidence"]] == ["task"]
    # The call is read once, and its evidence kept on later passes.
    resolve(store, server, resolver)
    assert mapping(url, worker.id)["actor"]["evidence"][0]["basis"] == "capabilities"


def test_a_launcher_rule_names_a_team_member_and_their_assignments_the_project(url, store, server):
    with store() as db:
        worker = session(db, "w", cwd="/somewhere/else")
        db.add(
            SessionProcess(
                session_id=worker.id,
                pid=4242,
                process_started_at=T0,
                argv_redacted=["codex", "exec", "work"],
                first_seen_at=T0,
                last_seen_at=T0,
            )
        )
        rule(
            db,
            server,
            RuleMatchKind.LAUNCHER,
            "codex *",
            RuleTargetKind.RUNTIME_REF,
            "runtime://backend-worker",
        )
        db.commit()
    resolve(store, server)
    found = mapping(url, worker.id)
    assert (found["actor"]["target"], found["actor"]["basis"]) == (31, "runtime_ref")
    assert (found["project"]["target"], found["project"]["basis"]) == (1, "assignment")


def test_removing_a_rule_removes_its_mapping_on_the_next_pass(url, store, server, tmp_path):
    with store() as db:
        found = session(db, "a", cwd="/work/orchard")
        made = rule(db, server, RuleMatchKind.FOLDER, "/work/orchard", RuleTargetKind.PROJECT, 1)
        db.commit()
    resolve(store, server)
    assert mapping(url, found.id)["project"]["state"] == "mapped"
    with store() as db:
        assert cli.run(["unrule", str(server), str(made.id)], db, _secrets(tmp_path)) == 0
    resolve(store, server)
    assert mapping(url, found.id)["project"]["state"] == "unmapped"


# -- the command line -----------------------------------------------------------------


def _secrets(tmp_path):
    return FileStore(tmp_path / "secrets.json")


def test_rules_are_made_listed_and_checked_from_the_command_line(store, server, tmp_path, capsys):
    secrets = _secrets(tmp_path)
    with store() as db:
        assert (
            cli.run(
                ["rule", str(server), "--folder", "work/orchard", "--project", "1"], db, secrets
            )
            == 1
        )
        assert (
            cli.run(
                [
                    "rule",
                    str(server),
                    "--folder",
                    "/work/orchard",
                    "--project",
                    "1",
                    "--harness",
                    "pi",
                ],
                db,
                secrets,
            )
            == 0
        )
        assert cli.run(["pin", str(server), "999", "--actor", "31"], db, secrets) == 1
        capsys.readouterr()
        assert cli.run(["rules", str(server)], db, secrets) == 0
    listed = capsys.readouterr().out
    assert "folder /work/orchard (pi only) -> project 1" in listed


# -- the pieces -----------------------------------------------------------------------


def test_decide():
    assert decide([], ORDER).state == "unmapped"
    agree = decide([Candidate("rule", 1, {}), Candidate("task", 1, {})], ORDER)
    assert (agree.state, agree.target, agree.basis) == ("mapped", 1, "task")
    split = decide([Candidate("task", 1, {}), Candidate("task", 2, {})], ORDER)
    assert (split.state, split.target, split.suggestion) == ("conflict", None, None)
    pinned = decide([Candidate("task", 1, {}), Candidate("manual", 2, {})], ORDER)
    assert (pinned.state, pinned.target, pinned.basis) == ("mapped", 2, "manual")


@pytest.mark.parametrize(
    ("path", "folder", "expected"),
    [
        ("/work/orchard", "/work/orchard", True),
        ("/work/orchard/app", "/work/orchard/", True),
        ("/work/orchard-2", "/work/orchard", False),
        ("C:\\work\\orchard\\app", "C:\\work\\orchard", True),
        (None, "/work", False),
    ],
)
def test_inside(path, folder, expected):
    assert inside(path, folder) is expected


def test_repository_addresses_compare_without_scheme_user_or_suffix():
    keys = {
        repository_key(a)
        for a in (
            "https://github.example/Acme/Orchard.git",
            "git@github.example:acme/orchard.git",
            "ssh://git@github.example/acme/orchard/",
        )
    }
    assert keys == {"github.example/acme/orchard"}


def test_a_recorded_repository_address_never_keeps_credentials():
    assert repository_address("https://someone:hunter2@github.example/a/b.git") == (
        "https://github.example/a/b.git"
    )
    assert repository_address("git@github.example:a/b.git") == "git@github.example:a/b.git"


def test_the_mirror_keeps_runtime_references_from_the_team(tmp_path, store):
    from test_workchord_mirror import FakeServer, connect

    from agdash.connectors.mirror import sync_connection

    secrets = _secrets(tmp_path)
    fake = FakeServer()
    fake.routes["/api/auth/me"] = (
        200,
        {"mode": "managed", "authenticated": True, "features": ["team-topology-read-v1"]},
    )
    fake.routes["/api/agent/team-setup/topology"] = (
        200,
        {
            "schema_version": "agent-team-topology-v1",
            "topology_state": "runtime_ready",
            "members": [
                {
                    "actor_key": "robin",
                    "actor_id": 31,
                    "actor_name": "robin",
                    "display_name": "Robin (synthetic)",
                    "role": "worker",
                    "runtime_ref": "runtime://backend-worker",
                    "credential_ref": "never-stored",
                },
                {
                    "actor_key": "lark",
                    "actor_id": None,
                    "actor_name": "lark",
                    "role": "pm",
                    "runtime_ref": "runtime://primary-pm",
                },
            ],
        },
    )
    with store() as db:
        row = connect(db, secrets)
        report = sync_connection(db, secrets, row, transport=fake.transport)
        db.commit()
        actors = {a.remote_id: a for a in db.scalars(select(WcActor))}
        assert actors[31].runtime_ref == "runtime://backend-worker"
        assert "1 runtime references" in report.objects[MirrorObject.ACTORS].detail
        assert not any("never-stored" in json.dumps(a.runtime_ref) for a in actors.values())
