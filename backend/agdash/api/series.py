"""Analytics over time: usage and activity per hour, day or week, in the reader's time zone.

Two endpoints, for the sessions the filters select:

* ``/api/analytics/series``: per bucket and per harness, model or folder, the
  tokens and cost the harnesses reported, the sessions active, and their active
  minutes. A number nobody reported in a bucket is null, never zero, and each
  point says how many of its sessions reported each number.
* ``/api/analytics/activity``: an hour-of-week grid of session-hours, and the
  number of sessions active at once per bucket, by harness.

A session is *active* in a stretch of time when it recorded at least one event
in it; its *active minutes* are the minutes in which it did. This counts what
the harness wrote down, not whether a person or an agent was busy.

Buckets follow the reader's clock: a day is a local day, so one with a daylight
saving change lasts 23 or 25 hours, and the hour repeated when clocks go back is
two hourly buckets. The store keeps UTC; events are first reduced in the
database to distinct session-minutes, then placed in local buckets here.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from agdash.api.analytics import METRICS
from agdash.api.routes import Db, Filters, _one_per_message
from agdash.db.models import Event, EventUsage, Session

router = APIRouter()

Bucket = Literal["hour", "day", "week"]
ParallelBucket = Literal["quarter-hour", "hour", "day"]
Group = Literal["harness", "model", "folder"]
MAX_BUCKETS = 1000
# Buckets shorter than an hour, by their length in minutes.
MINUTES = {"minute": 1, "5-minute": 5, "quarter-hour": 15}
COUNTS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


class Point(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    reported_cost: float | None = None
    messages: int = Field(0, description="Usage reports in the bucket.")
    sessions: int | None = Field(
        None, description="Sessions active in the bucket; null when grouping by model."
    )
    active_minutes: int | None = Field(
        None, description="Minutes in which a session recorded an event; null by model."
    )
    sessions_with_usage: int = Field(0, description="Sessions that reported any usage here.")
    reported_by: dict[str, int] = Field(
        default_factory=dict, description="Per number, how many sessions reported it here."
    )


class Line(BaseModel):
    key: str | None = Field(description="The harness, model or folder; null when not recorded.")
    points: list[Point] = Field(description="One per bucket, in bucket order.")
    total: Point


class Span(BaseModel):
    start: datetime
    end: datetime


class SeriesReport(BaseModel):
    bucket: Bucket
    group: Group
    time_zone: str
    buckets: list[Span]
    series: list[Line]
    total: Line
    undated_usage: int = Field(description="Usage reports with no time, left out of the buckets.")


class Cell(BaseModel):
    weekday: int = Field(description="0 is Monday.")
    hour: int
    session_hours: int = Field(description="Session and hour pairs with at least one event.")


class Lane(BaseModel):
    key: str | None = Field(description="The harness; null when not recorded.")
    sessions: list[int] = Field(description="Sessions active in each bucket.")


class StartedDay(BaseModel):
    day: date = Field(description="A local calendar day.")
    sessions: int = Field(description="Sessions that started on it.")


class StartedReport(BaseModel):
    time_zone: str
    days: list[StartedDay] = Field(
        description="Every day from the first start to the last, in order; zero where none started."
    )
    undated: int = Field(description="Sessions with no recorded start, left out of the days.")


class ActivityReport(BaseModel):
    time_zone: str
    bucket: ParallelBucket
    hour_of_week: list[Cell]
    buckets: list[Span]
    parallel: list[Lane]
    total: Lane


# -- time ---------------------------------------------------------------------------


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail=f"unknown time zone: {name[:64]}") from None


def _floor(moment: datetime, bucket: str, zone: ZoneInfo) -> datetime:
    """The UTC start of the local bucket that ``moment`` falls in."""
    local = moment.astimezone(zone)
    if bucket in MINUTES:
        width = MINUTES[bucket]
        start = local.replace(minute=local.minute - local.minute % width, second=0, microsecond=0)
    elif bucket == "hour":
        start = local.replace(minute=0, second=0, microsecond=0)
    else:
        start = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
        if bucket == "week":
            start -= timedelta(days=start.weekday())
        # A local midnight is a wall time; let the zone say which instant it is.
        start = start.replace(tzinfo=None).replace(tzinfo=zone)
    return start.astimezone(UTC)


def _next(start: datetime, bucket: str, zone: ZoneInfo) -> datetime:
    if bucket in MINUTES:
        return start + timedelta(minutes=MINUTES[bucket])
    if bucket == "hour":
        return start + timedelta(hours=1)
    local = start.astimezone(zone).replace(tzinfo=None)
    step = local + timedelta(days=7 if bucket == "week" else 1)
    return step.replace(tzinfo=zone).astimezone(UTC)


def _spans(first: datetime, last: datetime, bucket: str, zone: ZoneInfo) -> list[datetime]:
    starts = []
    at = _floor(first, bucket, zone)
    while at <= last:
        starts.append(at)
        if len(starts) > MAX_BUCKETS:
            raise HTTPException(
                status_code=422,
                detail=f"more than {MAX_BUCKETS} buckets: use a longer bucket or a shorter range",
            )
        following = _next(at, bucket, zone)
        at = following if following > at else at + timedelta(hours=1)
    return starts


def _window(since: datetime | None, until: datetime | None, found: list[datetime]):
    first = since.astimezone(UTC) if since else (min(found) if found else None)
    last = (
        (until.astimezone(UTC) - timedelta(microseconds=1))
        if until
        else (max(found) if found else None)
    )
    return first, last


# -- reading ------------------------------------------------------------------------


def _minute(db) -> Any:
    """Each event's minute as text, in whichever form this database can compute."""
    if db.get_bind().dialect.name == "postgresql":
        return func.to_char(func.date_trunc("minute", Event.occurred_at), "YYYY-MM-DD HH24:MI")
    return func.substr(Event.occurred_at, 1, 16)


def _session_minutes(db, scope, since, until) -> list[tuple[int, datetime]]:
    """Distinct (session, minute) pairs with an event, as UTC instants."""
    minute = _minute(db)
    query = select(Event.session_id, minute).where(
        Event.session_id.in_(scope), Event.occurred_at.is_not(None)
    )
    if since is not None:
        query = query.where(Event.occurred_at >= since)
    if until is not None:
        query = query.where(Event.occurred_at < until)
    return [
        (session_id, datetime.fromisoformat(text).replace(tzinfo=UTC))
        for session_id, text in db.execute(query.group_by(Event.session_id, minute))
    ]


def _keys(db, scope, group: str) -> dict[int, str | None]:
    column = Session.cwd if group == "folder" else Session.harness
    return dict(db.execute(select(Session.id, column).where(Session.id.in_(scope))).all())


def _add(point: dict, values: dict) -> None:
    point["messages"] += 1
    for metric in METRICS:
        value = values.get(metric)
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        point[metric] = (point.get(metric) or 0) + value
        point["reporters"][metric].add(values["session"])
    point["users"].add(values["session"])


def _blank() -> dict:
    return {
        "messages": 0,
        "reporters": defaultdict(set),
        "users": set(),
        "active": set(),
        "minutes": 0,
    }


def _point(raw: dict, by_model: bool) -> Point:
    values = {metric: raw.get(metric) for metric in METRICS}
    for metric in COUNTS:
        if values[metric] is not None:
            values[metric] = int(values[metric])
    return Point(
        **values,
        messages=raw["messages"],
        sessions=None if by_model else len(raw["active"]),
        active_minutes=None if by_model else raw["minutes"],
        sessions_with_usage=len(raw["users"]),
        reported_by={metric: len(raw["reporters"].get(metric, ())) for metric in METRICS},
    )


@router.get(
    "/analytics/series", response_model=SeriesReport, summary="Usage and activity over time"
)
def series_report(
    db: Db,
    filters: Filters,
    time_zone: Annotated[
        str, Query(max_length=64, description="An IANA zone, such as Europe/Berlin.")
    ] = "UTC",
    bucket: Annotated[
        Bucket, Query(description="Local hours, days or weeks (from Monday).")
    ] = "day",
    group: Annotated[Group, Query(description="What each line is.")] = "harness",
    since: Annotated[datetime | None, Query(description="Events at or after this instant.")] = None,
    until: Annotated[datetime | None, Query(description="Events before this instant.")] = None,
) -> dict[str, Any]:
    zone = _zone(time_zone)
    scope = filters.apply(select(Session.id))
    by_model = group == "model"
    keys = {} if by_model else _keys(db, scope, group)

    numbers = [getattr(EventUsage, metric) for metric in METRICS]
    usage = select(EventUsage.session_id, EventUsage.occurred_at, EventUsage.model, *numbers).where(
        EventUsage.event_id.in_(_one_per_message().where(EventUsage.session_id.in_(scope)))
    )
    if since is not None:
        usage = usage.where(EventUsage.occurred_at >= since)
    if until is not None:
        usage = usage.where(EventUsage.occurred_at < until)
    reports, undated = [], 0
    for session_id, occurred, model, *values in db.execute(usage):
        if occurred is None:
            undated += 1
            continue
        payload = {"model": model, **dict(zip(METRICS, values, strict=True))}
        reports.append((session_id, occurred, payload))
    minutes = [] if by_model else _session_minutes(db, scope, since, until)

    found = [moment for _, moment, _ in reports] + [moment for _, moment in minutes]
    first, last = _window(since, until, found)
    starts = _spans(first, last, bucket, zone) if first is not None and last is not None else []
    index = {start: position for position, start in enumerate(starts)}

    lines: dict[Any, list[dict]] = defaultdict(lambda: [_blank() for _ in starts])
    totals = [_blank() for _ in starts]
    for session_id, occurred, payload in reports:
        position = index.get(_floor(occurred, bucket, zone))
        if position is None:
            continue
        key = payload.get("model") if by_model else keys.get(session_id)
        values = {**payload, "session": session_id}
        _add(lines[key][position], values)
        _add(totals[position], values)
    for session_id, moment in minutes:
        position = index.get(_floor(moment, bucket, zone))
        if position is None:
            continue
        for raw in (lines[keys.get(session_id)][position], totals[position]):
            raw["active"].add(session_id)
            raw["minutes"] += 1

    def summed(raws: list[dict]) -> Point:
        whole = _blank()
        for raw in raws:
            whole["messages"] += raw["messages"]
            whole["minutes"] += raw["minutes"]
            whole["users"] |= raw["users"]
            whole["active"] |= raw["active"]
            for metric in METRICS:
                if raw.get(metric) is not None:
                    whole[metric] = (whole.get(metric) or 0) + raw[metric]
                whole["reporters"][metric] |= raw["reporters"].get(metric, set())
        return _point(whole, by_model)

    series = [
        Line(key=key, points=[_point(raw, by_model) for raw in raws], total=summed(raws))
        for key, raws in lines.items()
    ]
    series.sort(key=lambda line: (-(line.total.output_tokens or 0), line.key or ""))
    return {
        "bucket": bucket,
        "group": group,
        "time_zone": time_zone,
        "buckets": [Span(start=s, end=_next(s, bucket, zone)) for s in starts],
        "series": series,
        "total": Line(
            key=None, points=[_point(raw, by_model) for raw in totals], total=summed(totals)
        ),
        "undated_usage": undated,
    }


@router.get(
    "/analytics/activity", response_model=ActivityReport, summary="When sessions are active"
)
def activity_report(
    db: Db,
    filters: Filters,
    time_zone: Annotated[
        str, Query(max_length=64, description="An IANA zone, such as Europe/Berlin.")
    ] = "UTC",
    bucket: Annotated[
        ParallelBucket, Query(description="The width of each step of the parallel count.")
    ] = "hour",
    since: Annotated[datetime | None, Query(description="Events at or after this instant.")] = None,
    until: Annotated[datetime | None, Query(description="Events before this instant.")] = None,
) -> dict[str, Any]:
    zone = _zone(time_zone)
    scope = filters.apply(select(Session.id))
    keys = _keys(db, scope, "harness")
    minutes = _session_minutes(db, scope, since, until)

    hours: set[tuple[int, datetime]] = set()
    for session_id, moment in minutes:
        hours.add((session_id, _floor(moment, "hour", zone)))
    grid: dict[tuple[int, int], int] = defaultdict(int)
    for _, start in hours:
        local = start.astimezone(zone)
        grid[(local.weekday(), local.hour)] += 1

    first, last = _window(since, until, [moment for _, moment in minutes])
    starts = _spans(first, last, bucket, zone) if first is not None and last is not None else []
    index = {start: position for position, start in enumerate(starts)}
    present: dict[Any, list[set[int]]] = defaultdict(lambda: [set() for _ in starts])
    overall = [set() for _ in starts]
    for session_id, moment in minutes:
        position = index.get(_floor(moment, bucket, zone))
        if position is None:
            continue
        present[keys.get(session_id)][position].add(session_id)
        overall[position].add(session_id)
    lanes = [
        Lane(key=key, sessions=[len(found) for found in sets]) for key, sets in present.items()
    ]
    lanes.sort(key=lambda lane: (-sum(lane.sessions), lane.key or ""))
    return {
        "time_zone": time_zone,
        "bucket": bucket,
        "hour_of_week": [
            Cell(weekday=day, hour=hour, session_hours=grid.get((day, hour), 0))
            for day in range(7)
            for hour in range(24)
        ],
        "buckets": [Span(start=s, end=_next(s, bucket, zone)) for s in starts],
        "parallel": lanes,
        "total": Lane(key=None, sessions=[len(found) for found in overall]),
    }


MAX_DAYS = 20_000


@router.get("/analytics/started", response_model=StartedReport, summary="Sessions started per day")
def started_report(
    db: Db,
    filters: Filters,
    time_zone: Annotated[
        str, Query(max_length=64, description="An IANA zone, such as Europe/Berlin.")
    ] = "UTC",
) -> dict[str, Any]:
    """How many of the selected sessions started on each local day, for picking a date range."""
    zone = _zone(time_zone)
    starts = list(db.scalars(filters.apply(select(Session.started_at))))
    counts: dict[date, int] = defaultdict(int)
    undated = 0
    for started in starts:
        if started is None:
            undated += 1
            continue
        counts[started.astimezone(zone).date()] += 1
    days: list[dict[str, Any]] = []
    if counts:
        first, last = min(counts), max(counts)
        span = (last - first).days + 1
        if span > MAX_DAYS:
            raise HTTPException(status_code=422, detail=f"more than {MAX_DAYS} days of sessions")
        days = [
            {
                "day": first + timedelta(days=offset),
                "sessions": counts.get(first + timedelta(days=offset), 0),
            }
            for offset in range(span)
        ]
    return {"time_zone": time_zone, "days": days, "undated": undated}
