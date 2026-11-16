"""One session over time, its recorded states, and the last half hour of live sessions.

Every store here is built by hand, so each figure can be checked against a
count worked out from the records below.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from test_api import local_client

from agdash.api.stream import changes, start
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session, SessionStateChange
from agdash.retention import apply_retention
from agdash.vocab import (
    EventKind,
    ParentBasis,
    SessionState,
    StateBasis,
    ThoughtsCoverage,
)

DAY = datetime(2026, 3, 1, tzinfo=UTC)


def at(clock: str) -> datetime:
    hours, minutes, seconds = (int(part) for part in clock.split(":"))
    return DAY.replace(hour=hours, minute=minutes, second=seconds)


@pytest.fixture
def url(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    return url


def writer(url: str):
    engine = create_engine(url)
    return engine, sessionmaker(engine)


def add_events(db, session: Session, records) -> None:
    for sequence, (when, kind, payload, *coverage) in enumerate(records):
        db.add(
            Event(
                session_id=session.id,
                sequence=sequence,
                occurred_at=at(when) if when else None,
                kind=kind,
                payload=payload,
                thoughts_coverage=coverage[0] if coverage else None,
            )
        )


def usage(message: str, **numbers) -> dict:
    return {"scope": "message", **numbers, "extra": {"message_id": message}}


TEXT = {"text": "synthetic"}


@pytest.fixture
def worked(url) -> dict[str, int]:
    """A session of three turns, two subagents, and one that reported before any prompt."""
    engine, store = writer(url)
    with store() as db:
        main = Session(harness="codex", native_id="main", state=SessionState.ACTIVE)
        early = Session(harness="pi", native_id="early", state=SessionState.ENDED)
        db.add_all([main, early])
        db.flush()
        add_events(
            db,
            main,
            [
                ("10:00:10", EventKind.USER_PROMPT, TEXT),
                ("10:00:20", EventKind.THINKING, TEXT, ThoughtsCoverage.FULL),
                ("10:00:30", EventKind.USAGE, usage("m1", input_tokens=100, output_tokens=10)),
                ("10:00:40", EventKind.TOOL_CALL, {"name": "shell", "call_id": "c1"}),
                ("10:01:05", EventKind.TOOL_RESULT, {"call_id": "c1", "is_error": True}),
                ("10:01:10", EventKind.TOOL_RESULT, {"call_id": "c2", "is_error": False}),
                ("10:01:20", EventKind.THINKING, {}, ThoughtsCoverage.ENCRYPTED),
                ("10:01:30", EventKind.ASSISTANT_TEXT, TEXT),
                ("10:01:31", EventKind.USAGE, usage("m1", input_tokens=100, output_tokens=10)),
                ("10:01:35", EventKind.USAGE, usage("m2", output_tokens=5)),
                (
                    "10:02:00",
                    EventKind.MODEL_CHANGE,
                    {"model": "model-b", "previous_model": "model-a"},
                ),
                ("10:03:00", EventKind.USER_PROMPT, TEXT),
                ("10:03:10", EventKind.THINKING, TEXT),
                (
                    "10:04:00",
                    EventKind.COMPACTION,
                    {"trigger": "auto", "tokens_before": 900, "tokens_after": 100},
                ),
                ("10:05:00", EventKind.USER_PROMPT, TEXT),
                ("10:05:30", EventKind.USAGE, usage("m3", reported_cost=0.0)),
                (None, EventKind.ASSISTANT_TEXT, TEXT),
            ],
        )
        add_events(
            db,
            early,
            [
                ("09:00:00", EventKind.USAGE, usage("e1", input_tokens=7)),
                ("09:00:10", EventKind.USER_PROMPT, TEXT),
            ],
        )
        finished = Session(
            harness="codex",
            native_id="child-1",
            title="Look around",
            parent_session_id=main.id,
            parent_basis=ParentBasis.HARNESS_RECORD,
            state=SessionState.ENDED,
            started_at=at("10:00:45"),
            last_activity_at=at("10:02:20"),
            ended_at=at("10:02:30"),
        )
        running = Session(
            harness="claude_code",
            native_id="child-2",
            parent_session_id=main.id,
            parent_basis=ParentBasis.PROCESS_TREE,
            state=SessionState.ACTIVE,
            started_at=at("10:04:00"),
            last_activity_at=at("10:04:50"),
        )
        db.add_all([running, finished])
        db.commit()
        ids = {"main": main.id, "early": early.id, "finished": finished.id, "running": running.id}
    engine.dispose()
    return ids


def get(url: str, path: str, **params):
    with local_client(url) as client:
        response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_overview_counts_each_minute_by_kind(url, worked):
    report = get(url, f"/api/sessions/{worked['main']}/overview")
    assert report["bucket"] == "minute"
    assert [span["start"][11:16] for span in report["buckets"]] == [
        "10:00",
        "10:01",
        "10:02",
        "10:03",
        "10:04",
        "10:05",
    ]
    quiet = {
        "prompts": 0,
        "replies": 0,
        "tool_calls": 0,
        "tool_errors": 0,
        "thoughts": {"full": 0, "summary": 0, "encrypted": 0, "absent": 0, "not_recorded": 0},
    }

    def minute(**counts):
        thoughts = {**quiet["thoughts"], **counts.pop("thoughts", {})}
        return {**quiet, **counts, "thoughts": thoughts}

    assert report["activity"] == [
        minute(prompts=1, tool_calls=1, thoughts={"full": 1}),
        minute(replies=1, tool_errors=1, thoughts={"encrypted": 1}),
        minute(),
        minute(prompts=1, thoughts={"not_recorded": 1}),
        minute(),
        minute(prompts=1),
    ]
    assert report["undated_events"] == 1


def test_buckets_follow_the_time_zone(url, worked):
    report = get(
        url, f"/api/sessions/{worked['main']}/overview", bucket="hour", time_zone="Asia/Kolkata"
    )
    # 10:00 UTC is 15:30 in Kolkata, so the whole session sits in the 15:00 local hour.
    assert report["buckets"] == [{"start": "2026-03-01T09:30:00Z", "end": "2026-03-01T10:30:00Z"}]
    (whole,) = report["activity"]
    assert (whole["prompts"], whole["replies"], whole["tool_calls"]) == (3, 1, 1)


def test_model_changes_and_compactions_are_points(url, worked):
    report = get(url, f"/api/sessions/{worked['main']}/overview")
    assert report["markers"] == [
        {
            "kind": "model_change",
            "sequence": 10,
            "at": "2026-03-01T10:02:00Z",
            "model": "model-b",
            "previous_model": "model-a",
            "effort": None,
            "trigger": None,
            "tokens_before": None,
            "tokens_after": None,
        },
        {
            "kind": "compaction",
            "sequence": 13,
            "at": "2026-03-01T10:04:00Z",
            "model": None,
            "previous_model": None,
            "effort": None,
            "trigger": "auto",
            "tokens_before": 900,
            "tokens_after": 100,
        },
    ]
    assert report["markers_omitted"] == 0


def test_subagents_are_spans_with_their_harness_and_basis(url, worked):
    report = get(url, f"/api/sessions/{worked['main']}/overview")
    assert report["subagents"] == [
        {
            "session_id": worked["finished"],
            "harness": "codex",
            "parent_basis": "harness_record",
            "title": "Look around",
            "state": "ended",
            "start": "2026-03-01T10:00:45Z",
            "end": "2026-03-01T10:02:30Z",
            "ended": True,
        },
        {
            "session_id": worked["running"],
            "harness": "claude_code",
            "parent_basis": "process_tree",
            "title": None,
            "state": "active",
            "start": "2026-03-01T10:04:00Z",
            "end": "2026-03-01T10:04:50Z",
            "ended": False,
        },
    ]


def test_each_turn_has_the_usage_it_reported_and_null_where_none(url, worked):
    turns = get(url, f"/api/sessions/{worked['main']}/overview")["turns"]
    first, second, third = turns
    # The repeated report of message m1 counts once.
    assert (first["index"], first["prompt_sequence"], first["messages"]) == (1, 0, 2)
    assert (first["input_tokens"], first["output_tokens"], first["reported_cost"]) == (
        100,
        15,
        None,
    )
    assert first["reported_by"]["input_tokens"] == 1 and first["reported_by"]["output_tokens"] == 2
    assert first["started_at"] == "2026-03-01T10:00:10Z"
    assert second["messages"] == 0
    assert all(second[metric] is None for metric in ("input_tokens", "reported_cost"))
    assert set(second["reported_by"].values()) == {0}
    assert third["reported_cost"] == 0.0 and third["input_tokens"] is None
    assert third["reported_by"]["reported_cost"] == 1


def test_usage_before_the_first_prompt_is_turn_zero(url, worked):
    zero, first = get(url, f"/api/sessions/{worked['early']}/overview")["turns"]
    assert (zero["index"], zero["prompt_sequence"], zero["input_tokens"]) == (0, None, 7)
    assert (first["index"], first["messages"], first["input_tokens"]) == (1, 0, None)


def test_a_session_with_nothing_counted_has_no_buckets(url, worked):
    report = get(url, f"/api/sessions/{worked['running']}/overview")
    assert report["buckets"] == [] and report["activity"] == [] and report["turns"] == []


def test_unknown_sessions_and_too_many_buckets_are_refused(url, worked):
    with local_client(url) as client:
        assert client.get("/api/sessions/999/overview").status_code == 404
        assert client.get("/api/sessions/999/states").status_code == 404
        assert client.get(f"/api/sessions/{worked['early']}/states").status_code == 200
    engine, store = writer(url)
    with store() as db:
        long = Session(harness="pi", native_id="long", state=SessionState.ENDED)
        db.add(long)
        db.flush()
        add_events(
            db,
            long,
            [("00:00:00", EventKind.USER_PROMPT, TEXT), ("23:59:00", EventKind.USER_PROMPT, TEXT)],
        )
        db.commit()
        long_id = long.id
    engine.dispose()
    with local_client(url) as client:
        refused = client.get(f"/api/sessions/{long_id}/overview", params={"bucket": "minute"})
        assert refused.status_code == 422 and "buckets" in refused.json()["detail"]
        assert client.get(f"/api/sessions/{long_id}/overview").json()["bucket"] == "quarter-hour"


# -- states -------------------------------------------------------------------------


def test_the_state_history_lists_each_change_oldest_first(url, worked):
    engine, store = writer(url)
    with store() as db:
        db.add_all(
            [
                SessionStateChange(
                    session_id=worked["main"],
                    state=SessionState.WAITING,
                    basis=StateBasis.TURN_ENDED,
                    previous=SessionState.ACTIVE,
                    changed_at=at("10:02:00"),
                ),
                SessionStateChange(
                    session_id=worked["main"],
                    state=SessionState.ACTIVE,
                    basis=StateBasis.MID_TURN,
                    previous=None,
                    changed_at=at("10:00:10"),
                ),
            ]
        )
        db.commit()
    engine.dispose()
    history = get(url, f"/api/sessions/{worked['main']}/states")
    assert history == {
        "session_id": worked["main"],
        "state": "active",
        "changes": [
            {
                "state": "active",
                "basis": "mid_turn",
                "previous": None,
                "changed_at": "2026-03-01T10:00:10Z",
            },
            {
                "state": "waiting",
                "basis": "turn_ended",
                "previous": "active",
                "changed_at": "2026-03-01T10:02:00Z",
            },
        ],
        "earlier_omitted": 0,
    }


def test_retention_removes_state_changes_with_their_session(url):
    engine, store = writer(url)
    with store() as db:
        old = Session(
            harness="pi",
            native_id="old",
            state=SessionState.ENDED,
            last_activity_at=DAY - timedelta(days=40),
        )
        kept = Session(
            harness="pi", native_id="kept", state=SessionState.ENDED, last_activity_at=DAY
        )
        db.add_all([old, kept])
        db.flush()
        for session in (old, kept):
            db.add(
                SessionStateChange(
                    session_id=session.id,
                    state=SessionState.ENDED,
                    basis=StateBasis.PROCESS_EXITED,
                    changed_at=session.last_activity_at,
                )
            )
        db.commit()
        outcome = apply_retention(db, 30, now=DAY)
        db.commit()
        assert outcome.sessions == 1
        left = db.scalars(select(SessionStateChange.session_id)).all()
        assert left == [kept.id]
    engine.dispose()


# -- live ---------------------------------------------------------------------------


@pytest.fixture
def live(url) -> dict[str, int]:
    engine, store = writer(url)
    with store() as db:
        working = Session(
            harness="codex",
            native_id="w",
            state=SessionState.ACTIVE,
            last_activity_at=at("12:00:20"),
        )
        waiting = Session(
            harness="pi", native_id="q", state=SessionState.WAITING, last_activity_at=at("11:45:30")
        )
        over = Session(harness="pi", native_id="o", state=SessionState.ENDED)
        db.add_all([working, waiting, over])
        db.flush()
        add_events(
            db,
            working,
            [
                ("11:30:59", EventKind.STATUS, {"status": "line"}),  # before the window
                ("11:31:00", EventKind.USER_PROMPT, TEXT),
                ("12:00:10", EventKind.ASSISTANT_TEXT, TEXT),
                ("12:00:20", EventKind.USAGE, usage("m", output_tokens=1)),
                ("12:01:00", EventKind.STATUS, {"status": "line"}),  # after it
            ],
        )
        add_events(db, waiting, [("11:45:30", EventKind.USER_PROMPT, TEXT)])
        add_events(db, over, [("12:00:00", EventKind.USER_PROMPT, TEXT)])
        db.commit()
        ids = {"working": working.id, "waiting": waiting.id, "over": over.id}
    engine.dispose()
    return ids


def test_live_activity_counts_every_live_session_per_minute_in_one_request(url, live):
    report = get(url, "/api/live/activity", until="2026-03-01T12:00:30Z")
    assert report["minutes"][0] == "2026-03-01T11:31:00Z"
    assert report["minutes"][-1] == "2026-03-01T12:00:00Z"
    assert len(report["minutes"]) == 30 and report["until"] == "2026-03-01T12:01:00Z"
    sessions = {s["id"]: s for s in report["sessions"]}
    assert set(sessions) == {live["working"], live["waiting"]}
    working = sessions[live["working"]]["events"]
    assert (working[0], working[-1], sum(working)) == (1, 2, 3)
    waiting = sessions[live["waiting"]]["events"]
    assert waiting[14] == 1 and sum(waiting) == 1
    assert sessions[live["waiting"]]["state"] == "waiting"
    assert report["omitted"] == 0


def test_the_stream_announces_when_live_activity_changes(url, live):
    engine, store = writer(url)
    now = at("12:00:30")
    with store() as db:
        mark = start(db, now)
        assert changes(db, mark, now) == []
        db.add(
            Event(
                session_id=live["working"],
                sequence=99,
                occurred_at=now,
                kind=EventKind.ASSISTANT_TEXT,
                payload=TEXT,
            )
        )
        db.commit()
        found = dict(changes(db, mark, now))
        assert found["activity"] == {
            "minute": "2026-03-01T12:00:00+00:00",
            "sessions": [live["working"]],
        }
        assert changes(db, mark, now) == []
        # A new minute moves the window even when nothing was written.
        later = dict(changes(db, mark, now + timedelta(minutes=1)))
        assert later == {"activity": {"minute": "2026-03-01T12:01:00+00:00", "sessions": []}}
        assert db.scalar(select(func.count()).select_from(SessionStateChange)) == 0
    engine.dispose()
