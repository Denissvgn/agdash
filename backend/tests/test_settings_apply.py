"""Settings saved from the Data page, and the keeper that removes sessions past the limit."""

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from agdash import retention as retention_module
from agdash import saved
from agdash.access import ReadOnlyAccess
from agdash.adapters import pi
from agdash.archive import Archiving
from agdash.config import get_settings
from agdash.db.migrate import upgrade
from agdash.db.models import Event, EventUsage, RawChunk, Session, Setting
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.main import create_app
from agdash.retention import DAILY, GRACE, SETTLE, Keeper, apply_retention, remove_in_steps
from agdash.runtime import writer_engine
from agdash.vocab import EventKind, SessionState

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
TOKEN = "settings-test-token"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def url(tmp_path) -> str:
    database = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(database)
    return database


@pytest.fixture
def store(url):
    """The service's own writing connection: foreign keys on, as the collector has them."""
    engine = writer_engine(url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def settings_as_found():
    yield
    get_settings.cache_clear()


def client(url: str, keeper: Keeper | None = None) -> TestClient:
    return TestClient(
        create_app(url, token=TOKEN, port=8765, retention=keeper),
        base_url="http://127.0.0.1:8765",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def add(db, name: str, *, days_ago: float, state=SessionState.UNKNOWN, parent=None) -> Session:
    """A session last active ``days_ago`` before now, with a prompt and a usage event."""
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
    db.add_all(
        [
            Event(
                session_id=session.id,
                sequence=0,
                occurred_at=moment,
                kind=EventKind.USER_PROMPT,
                payload={"text": "synthetic"},
                source_file=f"/files/{name}.jsonl",
            ),
            Event(
                session_id=session.id,
                sequence=1,
                occurred_at=moment,
                kind=EventKind.USAGE,
                payload={"input_tokens": 10, "output_tokens": 5},
                source_file=f"/files/{name}.jsonl",
            ),
        ]
    )
    db.add(
        RawChunk(
            harness="pi",
            source_file=f"/files/{name}.jsonl",
            first_offset=0,
            end_offset=10,
            line_count=2,
            data=b"synthetic",
        )
    )
    db.flush()
    return session


@pytest.fixture
def aged(store):
    """Two sessions past 30 days and free to go, two past it that must stay, two recent."""
    with store() as db:
        add(db, "old", days_ago=90)
        add(db, "old-too", days_ago=45)
        add(db, "old-but-idle", days_ago=60, state=SessionState.IDLE)
        parent = add(db, "old-parent", days_ago=80)
        add(db, "recent-child", days_ago=2, parent=parent)
        add(db, "recent", days_ago=3)
        db.commit()
    return store


def names(store) -> set[str]:
    with store() as db:
        return set(db.scalars(select(Session.native_id)))


def count(store, model) -> int:
    with store() as db:
        return db.scalar(select(func.count()).select_from(model)) or 0


# -- saving ---------------------------------------------------------------------------


def test_a_limit_saved_from_the_page_holds_and_removes_nothing(url, aged):
    with client(url) as api:
        answer = api.post("/api/storage/settings", json={"retention_days": 30})
        assert answer.status_code == 200, answer.text
        retention = answer.json()["retention"]
        assert retention["days"] == 30 and retention["source"] == "page"
        assert retention["removable_sessions"] == 2 and retention["kept_alive"] == 2
        assert api.get("/api/storage").json()["retention"]["days"] == 30
    assert len(names(aged)) == 6, "saving a limit removes nothing"


def test_what_the_page_saved_wins_over_what_the_service_was_started_with(url, monkeypatch):
    monkeypatch.setenv("AGDASH_RETENTION_DAYS", "10")
    monkeypatch.setenv("AGDASH_KEEP_RAW", "false")
    get_settings.cache_clear()
    with client(url) as api:
        found = api.get("/api/storage").json()
        assert (found["retention"]["days"], found["retention"]["source"]) == (10, "environment")
        assert (found["archive"]["enabled"], found["archive"]["source"]) == (False, "environment")
        # Null is a choice too: keep everything, whatever the service was started with.
        found = api.post(
            "/api/storage/settings", json={"retention_days": None, "keep_raw": True}
        ).json()
        assert (found["retention"]["days"], found["retention"]["source"]) == (None, "page")
        assert found["retention"]["removable_sessions"] is None
        assert (found["archive"]["enabled"], found["archive"]["source"]) == (True, "page")
        # Naming one setting leaves the other as it was.
        found = api.post("/api/storage/settings", json={"keep_raw": False}).json()
        assert found["retention"]["source"] == "page" and found["retention"]["days"] is None
        assert found["archive"]["enabled"] is False


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"retention_days": 0},
        {"retention_days": -5},
        {"retention_days": saved.MAX_DAYS + 1},
        {"retention_days": "soon"},
        {"keep_raw": None},
        {"keep_raw": "perhaps"},
    ],
)
def test_a_setting_that_makes_no_sense_is_refused_and_nothing_is_saved(url, store, body):
    with client(url) as api:
        assert api.post("/api/storage/settings", json=body).status_code == 422
    assert count(store, Setting) == 0


def test_saving_needs_the_token(url, store):
    with client(url) as api:
        refused = api.post(
            "/api/storage/settings", json={"retention_days": 30}, headers={"Authorization": ""}
        )
        assert refused.status_code == 401
        asked = api.post("/api/storage/retention/removal", headers={"Authorization": ""})
        assert asked.status_code == 401
    assert count(store, Setting) == 0


def test_a_limit_can_be_tried_before_it_is_saved(url, aged):
    with client(url) as api:
        found = api.get("/api/storage/retention/preview", params={"days": 30}).json()
        assert found["days"] == 30
        assert found["removable_sessions"] == 2 and found["kept_alive"] == 2
        wider = api.get("/api/storage/retention/preview", params={"days": 70}).json()
        assert wider["removable_sessions"] == 1
        for days in (0, saved.MAX_DAYS + 1):
            answer = api.get("/api/storage/retention/preview", params={"days": days})
            assert answer.status_code == 422
        assert api.get("/api/storage").json()["retention"]["days"] is None
    assert count(aged, Setting) == 0 and len(names(aged)) == 6


def test_the_archive_follows_the_saved_setting_from_the_next_lines_read(url, store, tmp_path):
    def ingest() -> None:
        root = FIXTURES / "pi" / "agent"
        access = ReadOnlyAccess([root], snapshot_dir=tmp_path / "snapshots")
        writer = Archiving(pi.Writer(), pi.HARNESS, enabled=lambda db: saved.keep_raw(db)[0])
        Ingestor(access, SqlLedger(store, writer), harness=pi.HARNESS).scan(
            discover_files(access, [root], pi.PATTERNS)
        )

    with client(url) as api:
        assert api.post("/api/storage/settings", json={"keep_raw": False}).status_code == 200
    ingest()
    assert count(store, Event) > 0 and count(store, RawChunk) == 0


# -- removing -------------------------------------------------------------------------


def test_in_steps_removes_what_one_transaction_would(aged):
    with aged() as db:
        expected = apply_retention(db, 30, dry_run=True)
    outcome = remove_in_steps(aged, 30, step=1, pause_seconds=0)
    assert (outcome.sessions, outcome.events, outcome.kept_because_alive) == (
        expected.sessions,
        expected.events,
        expected.kept_because_alive,
    )
    assert outcome.sessions == 2 and outcome.events == 4 and outcome.raw_chunks == 2
    assert names(aged) == {"old-but-idle", "old-parent", "recent-child", "recent"}
    # What hangs on an event goes with it: nothing is left counting usage of a removed session.
    assert count(aged, Event) == 8 and count(aged, EventUsage) == 4
    assert count(aged, RawChunk) == 4


def test_a_step_ends_at_a_number_of_sessions_or_of_events(aged):
    with aged() as db:
        ids = sorted(db.scalars(select(Session.id)))
        by_sessions = retention_module._steps(db, ids, 4, 1000)
        by_events = retention_module._steps(db, ids, 100, 5)
        alone = retention_module._steps(db, ids, 100, 1)
    assert [len(step) for step in by_sessions] == [4, 2]
    # Each session holds two events: two sessions fit under five events, three do not.
    assert [len(step) for step in by_events] == [2, 2, 2]
    # A session larger than a step is a step of its own, never left out.
    assert [len(step) for step in alone] == [1] * 6
    assert [i for step in by_events for i in step] == ids


def test_nothing_last_active_after_the_time_shown_is_removed(aged):
    now = datetime.now(UTC)
    # Shown sixty-five days back: only the session older than that goes, though the
    # limit of thirty days would by now take two.
    shown = now - timedelta(days=65)
    assert remove_in_steps(aged, 30, not_after=shown, pause_seconds=0).sessions == 1
    assert "old" not in names(aged) and "old-too" in names(aged)
    # A time later than the limit allows changes nothing: the limit still holds.
    later = now - timedelta(days=1)
    outcome = remove_in_steps(aged, 30, not_after=later, pause_seconds=0)
    assert outcome.sessions == 1 and "recent" in names(aged)
    assert names(aged) == {"old-but-idle", "old-parent", "recent-child", "recent"}


def test_stopped_part_way_the_rest_goes_the_next_time(aged):
    stop = threading.Event()
    stop.set()
    assert remove_in_steps(aged, 30, stop=stop, step=1).sessions == 0
    assert len(names(aged)) == 6
    assert remove_in_steps(aged, 30, step=1, pause_seconds=0).sessions == 2


def test_the_keeper_removes_what_is_past_the_limit_and_notes_it(url, aged):
    keeper = Keeper(aged)
    assert keeper.keep(automatic=True) is None, "no limit, so nothing is looked for"
    with aged() as db:
        assert saved.last_removal(db) is None
    with aged.begin() as db:
        saved.save_retention(db, 30)
    outcome = keeper.keep(automatic=False)
    assert outcome is not None and outcome.sessions == 2
    assert names(aged) == {"old-but-idle", "old-parent", "recent-child", "recent"}
    with client(url, keeper) as api:
        retention = api.get("/api/storage").json()["retention"]
    last = retention["last_removal"]
    assert (last["sessions"], last["events"], last["automatic"], last["error"]) == (
        2,
        4,
        False,
        None,
    )
    assert retention["automatic"] is True and retention["removable_sessions"] == 0
    # A second pass finds nothing, and says so rather than keeping the old numbers.
    assert keeper.keep(automatic=True).sessions == 0
    with aged() as db:
        assert saved.last_removal(db)["sessions"] == 0


def test_a_removal_that_fails_is_noted_by_its_kind_and_leaves_the_store_whole(aged, monkeypatch):
    with aged.begin() as db:
        saved.save_retention(db, 30)

    def broken(*_args, **_kwargs):
        raise RuntimeError("synthetic failure naming /a/private/path")

    monkeypatch.setattr(retention_module, "remove_in_steps", broken)
    with pytest.raises(RuntimeError):
        Keeper(aged).keep(automatic=True)
    with aged() as db:
        last = saved.last_removal(db)
    assert last["error"] == "RuntimeError" and "private" not in str(last)
    assert len(names(aged)) == 6


def test_asking_from_the_page_needs_a_keeper_and_a_limit(url, aged):
    with client(url) as api:
        assert api.post("/api/storage/retention/removal").status_code == 503
    keeper = Keeper(aged)
    with client(url, keeper) as api:
        assert api.post("/api/storage/retention/removal").status_code == 409
        assert not keeper.standing().running
        api.post("/api/storage/settings", json={"retention_days": 30})
        answer = api.post("/api/storage/retention/removal")
        assert answer.status_code == 202
        assert answer.json()["retention"]["running"] is True
        assert keeper._asked_before is None
        # The time the person was shown travels with the request, and must be a real one.
        shown = datetime.now(UTC) - timedelta(days=65)
        asked = api.post("/api/storage/retention/removal", json={"before": shown.isoformat()})
        assert asked.status_code == 202 and keeper._asked_before == shown
        for odd in ("soon", "2026-10-17T12:00:00"):
            refused = api.post("/api/storage/retention/removal", json={"before": odd})
            assert refused.status_code == 422, odd
        assert keeper._asked_before == shown
    assert len(names(aged)) == 6, "asking only asks; the keeper's own thread removes"
    # What the keeper then removes stops at the time shown.
    assert keeper.keep(automatic=False, before=shown).sessions == 1
    assert "old-too" in names(aged)


def test_the_running_keeper_removes_when_asked_and_stops_with_the_service(aged, monkeypatch):
    monkeypatch.setattr(retention_module, "LOOK_SECONDS", 0.02)
    with aged.begin() as db:
        saved.save_retention(db, 30)
    keeper = Keeper(aged)
    stop = threading.Event()
    threads = keeper.run(stop)
    try:
        # Unasked, nothing goes right after the service starts.
        time.sleep(0.2)
        assert len(names(aged)) == 6
        assert keeper.standing().next_at is not None
        keeper.ask()
        deadline = time.monotonic() + 10
        while len(names(aged)) != 4 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert names(aged) == {"old-but-idle", "old-parent", "recent-child", "recent"}
        while keeper.standing().running and time.monotonic() < deadline:
            time.sleep(0.02)
        with aged() as db:
            assert saved.last_removal(db)["automatic"] is False
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)


def test_the_daily_pass_is_a_day_after_the_last_and_never_right_at_start_up(aged):
    clock = {"now": NOW}
    keeper = Keeper(aged, clock=lambda: clock["now"])
    assert keeper._first() == NOW + SETTLE, "never run: soon after start, not at once"
    with aged.begin() as db:
        saved.note_removal(db, at=NOW - timedelta(hours=2), sessions=0, events=0, automatic=True)
    assert keeper._first() == NOW + timedelta(hours=22)
    with aged.begin() as db:
        saved.note_removal(db, at=NOW - timedelta(days=3), sessions=0, events=0, automatic=True)
    assert keeper._first() == NOW + SETTLE, "overdue: still not at start-up itself"
    assert DAILY == timedelta(days=1)


def test_a_saved_limit_leaves_an_hour_before_anything_goes_unasked(url, aged):
    clock = {"now": NOW}
    keeper = Keeper(aged, clock=lambda: clock["now"])
    keeper._next_at = NOW + timedelta(minutes=3)
    with client(url, keeper) as api:
        found = api.post("/api/storage/settings", json={"retention_days": 30}).json()
        assert datetime.fromisoformat(found["retention"]["next_removal_at"]) == NOW + GRACE
        # Saving something else moves nothing.
        keeper._next_at = NOW + timedelta(hours=5)
        api.post("/api/storage/settings", json={"keep_raw": False})
        assert keeper.standing().next_at == NOW + timedelta(hours=5)
        # With no limit there is no next removal to name.
        found = api.post("/api/storage/settings", json={"retention_days": None}).json()
        assert found["retention"]["next_removal_at"] is None


# -- the command ----------------------------------------------------------------------


def test_the_command_uses_the_limit_saved_on_the_page(url, aged, capsys, monkeypatch):
    monkeypatch.setenv("AGDASH_DATABASE_URL", url)
    get_settings.cache_clear()
    assert retention_module.main([]) == 0
    assert "Retention is off" in capsys.readouterr().out
    with aged.begin() as db:
        saved.save_retention(db, 30)
    assert retention_module.main([]) == 0
    assert "Would remove 2 sessions" in capsys.readouterr().out
    assert retention_module.main(["--days", "70"]) == 0
    assert "Would remove 1 sessions" in capsys.readouterr().out
    assert len(names(aged)) == 6


def test_only_days_that_make_sense_can_be_saved(store):
    with store() as db:
        for days in (0, -1, saved.MAX_DAYS + 1):
            with pytest.raises(ValueError):
                saved.save_retention(db, days)
        saved.save_retention(db, None)
        saved.save_retention(db, saved.MAX_DAYS)
