"""Usage and activity over time: buckets in the reader's time zone, honest about gaps.

The first store is every synthetic harness fixture, ingested as the collector
would. The second is built by hand around the night Berlin's clocks go back
(25 October 2026, 03:00 local becomes 02:00), so every bucket can be checked
against figures worked out by hand.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_api import fill, local_client

from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session
from agdash.vocab import EventKind, SessionState

METRICS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "reported_cost",
)


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory) -> str:
    folder = tmp_path_factory.mktemp("series")
    (folder / "snapshots").mkdir()
    url = f"sqlite:///{folder / 'agdash.db'}"
    upgrade(url)
    fill(url, folder / "snapshots")
    return url


@pytest.mark.parametrize("group", ["harness", "model", "folder"])
@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Kolkata"])
def test_series_totals_equal_the_usage_report_and_the_stats(fixtures, group, zone):
    with local_client(fixtures) as client:
        usage = client.get("/api/analytics/usage", params={"group": group}).json()
        stats = client.get("/api/stats").json()
        report = client.get(
            "/api/analytics/series", params={"group": group, "time_zone": zone, "bucket": "day"}
        )
    assert report.status_code == 200, report.text
    series = report.json()
    assert series["undated_usage"] == 0
    whole = series["total"]["total"]
    for metric in METRICS:
        expected = usage["total"][metric]
        assert (
            whole[metric] == pytest.approx(expected)
            if expected is not None
            else whole[metric] is None
        )
        assert sum(point[metric] or 0 for point in series["total"]["points"]) == pytest.approx(
            expected or 0
        )
    assert whole["messages"] == usage["total"]["messages"]
    assert whole["messages"] == sum(group["messages"] for group in stats["usage"])
    assert whole["output_tokens"] == sum(group["output_tokens"] or 0 for group in stats["usage"])
    by_key = {line["key"]: line["total"] for line in series["series"]}
    for row in usage["rows"]:
        assert by_key[row["key"]]["output_tokens"] == row["output_tokens"]
        assert by_key[row["key"]]["sessions_with_usage"] == row["sessions_with_usage"]
    assert all(len(line["points"]) == len(series["buckets"]) for line in series["series"])


def _filter_cases(client) -> list[dict]:
    """Filters the page can send: a harness, a model, a folder, and a start-date window."""
    facets = client.get("/api/sessions/facets").json()
    first = datetime.fromisoformat(facets["first_started_at"])
    return [
        {"harness": facets["harnesses"][0]["value"]},
        {"model": facets["models"][0]["value"]},
        {"folder": facets["folders"][0]["value"]},
        {"started_from": (first + timedelta(seconds=1)).isoformat()},
        {"started_before": (first + timedelta(days=1)).isoformat()},
    ]


@pytest.mark.parametrize("group", ["harness", "model", "folder"])
def test_with_filters_the_series_and_the_ledger_count_the_same_sessions(fixtures, group):
    """The usage figure sends the ledger's own filters, so the two always agree."""
    with local_client(fixtures) as client:
        for filters in _filter_cases(client):
            usage = client.get("/api/analytics/usage", params={**filters, "group": group}).json()
            series = client.get(
                "/api/analytics/series", params={**filters, "group": group, "bucket": "day"}
            ).json()
            whole = series["total"]["total"]
            for metric in METRICS:
                expected = usage["total"][metric]
                assert (
                    whole[metric] == pytest.approx(expected)
                    if expected is not None
                    else whole[metric] is None
                ), (filters, metric)
            assert whole["messages"] == usage["total"]["messages"], filters
            assert {line["key"] for line in series["series"]} <= {
                row["key"] for row in usage["rows"]
            }


def test_by_model_only_usage_is_attributed(fixtures):
    with local_client(fixtures) as client:
        series = client.get("/api/analytics/series", params={"group": "model"}).json()
    for line in series["series"]:
        assert all(
            point["sessions"] is None and point["active_minutes"] is None
            for point in line["points"]
        )


# -- by hand, around a daylight saving change -----------------------------------------

ZONE = "Europe/Berlin"


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


@pytest.fixture
def berlin(tmp_path) -> str:
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    with sessionmaker(engine)() as db:
        codex = Session(
            harness="codex", native_id="c", cwd="/work/orchard", state=SessionState.ENDED
        )
        claude = Session(
            harness="claude_code", native_id="k", cwd="/work/meadow", state=SessionState.ENDED
        )
        db.add_all([codex, claude])
        db.flush()
        sequence = iter(range(10_000))

        def event(session, when, kind, payload):
            db.add(
                Event(
                    session_id=session.id,
                    sequence=next(sequence),
                    occurred_at=at(when),
                    kind=kind,
                    payload=payload,
                )
            )

        def usage(session, when, message, **numbers):
            event(
                session,
                when,
                EventKind.USAGE,
                {"scope": "message", **numbers, "extra": {"message_id": message}},
            )

        # Saturday evening, local time, then across midnight into the long Sunday.
        event(codex, "2026-10-24T23:50:00+02:00", EventKind.USER_PROMPT, {"text": "synthetic"})
        usage(codex, "2026-10-24T23:55:00+02:00", "m1", input_tokens=100, output_tokens=10)
        usage(
            codex,
            "2026-10-25T00:05:00+02:00",
            "m2",
            input_tokens=200,
            output_tokens=20,
            reported_cost=0.0,
        )
        usage(
            codex, "2026-10-25T00:05:30+02:00", "m2", input_tokens=200, output_tokens=20
        )  # repeat
        # 02:30 happens twice: first in summer time, then again an hour later in winter time.
        usage(codex, "2026-10-25T02:30:00+02:00", "m3", input_tokens=1, output_tokens=1)
        usage(claude, "2026-10-25T02:30:00+01:00", "m4", reported_cost=0.25)
        event(claude, "2026-10-25T02:31:00+01:00", EventKind.ASSISTANT_TEXT, {"text": "synthetic"})
        # Monday, the next local day.
        usage(claude, "2026-10-26T09:00:00+01:00", "m5", input_tokens=5, output_tokens=0)
        db.commit()
    engine.dispose()
    return url


def series(url: str, **params):
    with local_client(url) as client:
        response = client.get("/api/analytics/series", params={"time_zone": ZONE, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_days_follow_the_local_clock_and_the_long_sunday_lasts_25_hours(berlin):
    report = series(berlin, bucket="day")
    spans = [(span["start"], span["end"]) for span in report["buckets"]]
    assert [
        datetime.fromisoformat(end) - datetime.fromisoformat(start) for start, end in spans
    ] == [timedelta(hours=24), timedelta(hours=25), timedelta(hours=24)]
    saturday, sunday, monday = report["total"]["points"]
    assert saturday["input_tokens"] == 100 and saturday["messages"] == 1
    # The repeated report of message m2 counts once.
    assert sunday["input_tokens"] == 201 and sunday["messages"] == 3
    assert monday["input_tokens"] == 5 and monday["output_tokens"] == 0


def test_the_repeated_hour_is_two_buckets(berlin):
    report = series(
        berlin, bucket="hour", since="2026-10-25T00:00:00Z", until="2026-10-25T02:00:00Z"
    )
    starts = [span["start"] for span in report["buckets"]]
    assert starts == ["2026-10-25T00:00:00Z", "2026-10-25T01:00:00Z"]
    first, second = report["total"]["points"]
    assert (first["input_tokens"], first["reported_cost"]) == (1, None)  # 02:30 summer time
    assert (second["input_tokens"], second["reported_cost"]) == (None, 0.25)  # 02:30 winter time


def test_unreported_is_null_and_a_reported_zero_stays_zero(berlin):
    report = series(berlin, bucket="day", group="harness")
    lines = {line["key"]: line for line in report["series"]}
    saturday = lines["codex"]["points"][0]
    assert saturday["reported_cost"] is None and saturday["reported_by"]["reported_cost"] == 0
    sunday = lines["codex"]["points"][1]
    assert sunday["reported_cost"] == 0.0 and sunday["reported_by"]["reported_cost"] == 1
    assert lines["claude_code"]["points"][0]["sessions"] == 0
    assert lines["claude_code"]["points"][1]["input_tokens"] is None
    assert lines["claude_code"]["points"][1]["reported_by"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "reported_cost": 1,
    }
    assert lines["claude_code"]["points"][2]["output_tokens"] == 0


def test_sessions_and_active_minutes_count_what_was_written(berlin):
    report = series(berlin, bucket="day")
    saturday, sunday, monday = report["total"]["points"]
    # Saturday: the codex session wrote at 23:50 and 23:55.
    assert (saturday["sessions"], saturday["active_minutes"]) == (1, 2)
    # Sunday: codex at 00:05, 00:05:30 (the same minute) and 02:30; claude at 02:30 and 02:31.
    assert (sunday["sessions"], sunday["active_minutes"]) == (2, 4)
    assert (monday["sessions"], monday["active_minutes"]) == (1, 1)


def test_activity_by_hour_of_week_and_sessions_at_once(berlin):
    with local_client(berlin) as client:
        report = client.get(
            "/api/analytics/activity", params={"time_zone": ZONE, "bucket": "hour"}
        ).json()
    cells = {
        (cell["weekday"], cell["hour"]): cell["session_hours"] for cell in report["hour_of_week"]
    }
    assert len(cells) == 168
    assert cells[(5, 23)] == 1  # Saturday 23:00, codex
    assert cells[(6, 0)] == 1  # Sunday 00:00, codex
    # Sunday 02:00 happens twice: codex in the first, claude in the second.
    assert cells[(6, 2)] == 2
    assert cells[(0, 9)] == 1  # Monday 09:00, claude
    assert sum(cells.values()) == 5
    total = report["total"]["sessions"]
    assert max(total) == 1 and sum(total) == 5
    lanes = {lane["key"]: lane["sessions"] for lane in report["parallel"]}
    assert sum(lanes["codex"]) == 3 and sum(lanes["claude_code"]) == 2


def test_the_bucket_cap_and_unknown_zones_are_refused(berlin):
    with local_client(berlin) as client:
        too_many = client.get(
            "/api/analytics/series",
            params={
                "bucket": "hour",
                "since": "2026-01-01T00:00:00Z",
                "until": "2026-03-01T00:00:00Z",
            },
        )
        nowhere = client.get("/api/analytics/series", params={"time_zone": "Mars/Olympus_Mons"})
        schema = client.get("/api/openapi.json").json()
    assert too_many.status_code == 422 and "buckets" in too_many.json()["detail"]
    assert nowhere.status_code == 422 and "time zone" in nowhere.json()["detail"]
    assert {"/api/analytics/series", "/api/analytics/activity"} <= set(schema["paths"])


def test_an_empty_selection_has_no_buckets(berlin):
    report = series(berlin, harness="pi")
    assert report["buckets"] == [] and report["series"] == []
    assert report["total"]["total"]["messages"] == 0


@pytest.mark.parametrize("bucket", ["hour", "day", "week"])
def test_totals_across_the_clock_change_equal_the_usage_report_and_the_stats(berlin, bucket):
    with local_client(berlin) as client:
        usage = client.get("/api/analytics/usage", params={"group": "harness"}).json()
        stats = client.get("/api/stats").json()
    whole = series(berlin, bucket=bucket, group="harness")["total"]
    for metric in METRICS:
        assert whole["total"][metric] == usage["total"][metric]
        assert sum(point[metric] or 0 for point in whole["points"]) == pytest.approx(
            usage["total"][metric] or 0
        )
    assert whole["total"]["messages"] == usage["total"]["messages"] == 5
    assert whole["total"]["messages"] == sum(group["messages"] for group in stats["usage"])
    assert whole["total"]["input_tokens"] == sum(
        group["input_tokens"] or 0 for group in stats["usage"]
    )
