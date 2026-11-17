"""What agdash reads on this host, and what it keeps: the two reports behind Settings."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.adapters import claude_code, codex, pi
from agdash.api import sources, storage
from agdash.archive import Archiving
from agdash.config import get_settings
from agdash.db.migrate import sqlite_file, upgrade
from agdash.db.models import IngestCursor, RawChunk, Session, SourceHealth
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.main import create_app
from agdash.platform import Host, OperatingSystem
from agdash.vocab import SessionState

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
TOKEN = "sources-test-token"
ORDER = ["claude_code", "codex", "antigravity", "pi"]


@pytest.fixture
def url(tmp_path) -> str:
    database = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(database)
    return database


@pytest.fixture
def store(url):
    engine = create_engine(url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def filled(store, tmp_path):
    """Claude Code, Codex and Pi read from the synthetic fixtures, lines archived."""
    for module, folder in ((claude_code, "claude"), (codex, "codex"), (pi, "pi/agent")):
        root = FIXTURES / folder
        access = ReadOnlyAccess([root], snapshot_dir=tmp_path / "snapshots")
        writer = Archiving(module.Writer(), module.HARNESS)
        Ingestor(access, SqlLedger(store, writer), harness=module.HARNESS).scan(
            discover_files(access, [root], module.PATTERNS)
        )
    return store


@pytest.fixture
def home(tmp_path) -> Path:
    """A home where Claude Code and Codex keep files, and Pi and Antigravity do not."""
    found = tmp_path / "home"
    (found / ".claude").mkdir(parents=True)
    (found / ".codex").mkdir()
    return found


def client(url: str, home: Path | None = None) -> TestClient:
    host = Host(OperatingSystem.LINUX, home, {}) if home else None
    return TestClient(
        create_app(url, token=TOKEN, port=8765, host=host),
        base_url="http://127.0.0.1:8765",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def with_settings(monkeypatch, **values: str):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def settings_as_found():
    yield
    get_settings.cache_clear()


# -- sources ------------------------------------------------------------------------


def test_each_harness_says_where_it_is_looked_for_and_what_was_read(url, filled, home):
    with client(url, home) as api:
        found = api.get("/api/sources").json()["sources"]
    assert [source["harness"] for source in found] == ORDER
    by_name = {source["harness"]: source for source in found}
    with filled() as db:
        files = dict(
            db.execute(select(IngestCursor.harness, func.count()).group_by("harness")).all()
        )
        sessions = dict(db.execute(select(Session.harness, func.count()).group_by("harness")).all())

    claude = by_name["claude_code"]
    assert claude["found"] is True
    assert claude["locations"] == [
        {"path": str(home / ".claude"), "role": "records", "found": True},
        {"path": str(home / ".claude.json"), "role": "settings", "found": False},
    ]
    assert claude["files"] == files["claude_code"] > 0
    assert claude["sessions"] == sessions["claude_code"] > 0
    assert claude["bytes_read"] > 0 and claude["last_read_at"] is not None

    # Read earlier, though its folder is not on this host now: still told, as not found.
    assert by_name["pi"]["found"] is False
    assert by_name["pi"]["files"] == files["pi"] and by_name["pi"]["sessions"] == sessions["pi"]
    assert all(not place["found"] for place in by_name["pi"]["locations"])

    nothing = by_name["antigravity"]
    assert nothing["found"] is False and nothing["files"] == 0 and nothing["sessions"] == 0
    assert nothing["bytes_read"] == 0 and nothing["last_read_at"] is None
    assert nothing["format_version"] is None and nothing["formats"] == []
    # A settings file is never a place records are read from.
    assert {place["role"] for place in nothing["locations"]} == {"records", "settings"}
    assert all(
        place["path"].endswith(".json") == (place["role"] == "settings")
        for source in found
        for place in source["locations"]
    )


def test_a_settings_file_alone_does_not_make_a_harness_found(url, tmp_path):
    lone = tmp_path / "home"
    lone.mkdir()
    (lone / ".claude.json").write_text("{}")
    with client(url, lone) as api:
        claude = api.get("/api/sources").json()["sources"][0]
    assert claude["harness"] == "claude_code" and claude["found"] is False
    assert [place["found"] for place in claude["locations"]] == [False, True]


def test_records_not_understood_are_flagged_only_while_recent(url, store, home):
    now = datetime.now(UTC)

    def seen(version: str, days_ago: int, *, unknown: int = 0, errors: int = 0) -> SourceHealth:
        at = now - timedelta(days=days_ago)
        return SourceHealth(
            adapter="codex",
            format_version=version,
            unknown_record_count=unknown,
            parse_error_count=errors,
            first_seen_at=at - timedelta(days=5),
            last_seen_at=at,
        )

    with store() as db:
        db.add_all(
            [
                seen("unknown", 0, unknown=1),
                seen("1.0", 1),
                seen("0.9", 2, unknown=3, errors=2),
                seen("0.5", 60, errors=1),
            ]
        )
        db.commit()
    with client(url, home) as api:
        source = next(
            item for item in api.get("/api/sources").json()["sources"] if item["harness"] == "codex"
        )
    assert source["format_version"] == "1.0", "the newest version that names itself"
    formats = source["formats"]
    assert [item["version"] for item in formats] == [None, "1.0", "0.9", "0.5"]
    assert [item["recent"] for item in formats] == [True, False, True, False]
    assert formats[2]["unknown_records"] == 3 and formats[2]["unreadable_lines"] == 2
    assert formats[2]["first_seen_at"] < formats[2]["last_seen_at"]


# -- storage ------------------------------------------------------------------------


def add(db, name: str, *, days_ago: float, state=SessionState.UNKNOWN, parent=None) -> Session:
    moment = datetime.now(UTC) - timedelta(days=days_ago)
    session = Session(
        harness="pi",
        native_id=name,
        state=state,
        started_at=moment,
        last_activity_at=moment,
        parent_session_id=parent.id if parent else None,
    )
    db.add(session)
    db.flush()
    return session


def test_storage_says_where_the_store_is_and_how_much_it_holds(url, store, tmp_path):
    with store() as db:
        add(db, "old", days_ago=400)
        add(db, "new", days_ago=1)
        db.commit()
    with client(url) as api:
        answer = api.get("/api/storage")
    found = answer.json()
    path = tmp_path / "agdash.db"
    assert found["store"]["file"] == str(path)
    assert found["store"]["bytes"] >= path.stat().st_size > 0
    assert found["store"]["sessions"] == 2
    first = datetime.fromisoformat(found["store"]["first_started_at"])
    assert timedelta(days=399) < datetime.now(UTC) - first < timedelta(days=401)
    assert found["archive"] == {
        "enabled": True,
        "source": "default",
        "chunks": 0,
        "keep_encoded": False,
        "keep_encoded_source": "default",
        "untrimmed_chunks": 0,
        "trimming": False,
        "last_trim": None,
    }
    # Off unless a limit is set: nothing is worked out, and nothing is named as removable.
    # This process has no keeper, so it says it removes nothing by itself.
    assert found["retention"] == {
        "days": None,
        "source": "default",
        "cutoff": None,
        "removable_sessions": None,
        "kept_alive": None,
        "automatic": False,
        "running": False,
        "next_removal_at": None,
        "last_removal": None,
    }
    assert "sqlite" not in answer.text, "the store's address is never returned"


def test_retention_in_effect_says_what_it_would_remove_and_removes_nothing(url, store, monkeypatch):
    with store() as db:
        add(db, "old", days_ago=90)
        add(db, "old-too", days_ago=45)
        add(db, "old-but-idle", days_ago=60, state=SessionState.IDLE)
        parent = add(db, "old-parent", days_ago=80)
        add(db, "recent-child", days_ago=2, parent=parent)
        add(db, "recent", days_ago=3)
        db.commit()
    with_settings(monkeypatch, AGDASH_RETENTION_DAYS="30", AGDASH_KEEP_RAW="false")
    with client(url) as api:
        found = api.get("/api/storage").json()
    retention = found["retention"]
    assert retention["days"] == 30 and retention["source"] == "environment"
    cutoff = datetime.fromisoformat(retention["cutoff"])
    assert timedelta(days=29) < datetime.now(UTC) - cutoff < timedelta(days=31)
    assert retention["removable_sessions"] == 2
    # The idle one may still run, and the old parent stays with its recent child.
    assert retention["kept_alive"] == 2
    assert found["archive"]["enabled"] is False and found["archive"]["source"] == "environment"
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Session)) == 6


def test_the_archive_size_is_what_is_stored(url, filled):
    with filled() as db:
        chunks = db.scalar(select(func.count()).select_from(RawChunk))
        stored = db.scalar(select(func.sum(func.length(RawChunk.data))))
    assert chunks > 0
    with client(url) as api:
        assert api.get("/api/storage/archive").json() == {"chunks": chunks, "bytes": stored}
        assert api.get("/api/storage").json()["archive"]["chunks"] == chunks


def test_an_empty_archive_measures_as_nothing(url):
    with client(url) as api:
        assert api.get("/api/storage/archive").json() == {"chunks": 0, "bytes": 0}


def test_only_a_store_that_is_a_file_has_a_path():
    assert sqlite_file("sqlite:////data/agdash.db") == Path("/data/agdash.db")
    assert sqlite_file("sqlite+aiosqlite:////data/agdash.db") == Path("/data/agdash.db")
    assert sqlite_file("sqlite://") is None
    assert sqlite_file("sqlite:///:memory:") is None
    assert sqlite_file("postgresql+asyncpg://agdash:example@db.invalid/agdash") is None


# -- read only ----------------------------------------------------------------------


def test_these_routes_only_read_and_need_the_token(url, home):
    for route in sources.router.routes:
        assert isinstance(route, APIRoute) and route.methods <= {"GET", "HEAD"}, route.path
    # On storage, exactly the two routes it names may change anything.
    writing = {
        (method, route.path)
        for route in storage.router.routes
        for method in route.methods - {"GET", "HEAD"}
    }
    assert writing == storage.WRITE_ROUTES
    with client(url, home) as api:
        for path in (
            "/api/sources",
            "/api/storage",
            "/api/storage/archive",
            "/api/storage/retention/preview?days=30",
        ):
            assert api.get(path).status_code == 200, path
            assert api.get(path, headers={"Authorization": ""}).status_code == 401, path
            for method in ("post", "put", "patch", "delete"):
                assert getattr(api, method)(path).status_code == 405, (method, path)
