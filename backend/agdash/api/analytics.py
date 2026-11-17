"""Analytics: usage, duration and tool mix, grouped, for the sessions the filters select.

Numbers are what the harnesses reported. A number nobody reported is null, never
zero, and every row says how many of its sessions reported anything, so a total
built from part of the sessions reads as partial. Usage totals come from the same
queries as ``/api/stats``, so the two always agree for the same filters.
"""

from collections import defaultdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import aliased

from agdash.api.routes import Db, Filters, _one_per_message, _usage, _usage_columns
from agdash.api.schemas import UsageTotals
from agdash.db.models import EventTool, EventUsage, Session
from agdash.vocab import EventKind

router = APIRouter()

Group = Literal["harness", "model", "folder"]
MAX_TOOLS = 100


METRICS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "reported_cost",
)


def _reporting_columns():
    """Per metric, how many sessions reported it at least once."""
    return [
        func.count(
            func.distinct(case((getattr(EventUsage, metric).is_not(None), EventUsage.session_id)))
        )
        for metric in METRICS
    ]


def _reported_by(counts) -> dict[str, int]:
    return dict(zip(METRICS, counts, strict=True))


class UsageRow(UsageTotals):
    key: str | None = Field(description="The harness, model or folder; null when not recorded.")
    sessions: int
    sessions_with_usage: int = Field(description="Sessions in the row that reported any usage.")
    duration_seconds: float | None = Field(
        description="Summed time from start to last activity; null when not attributable."
    )
    sessions_with_duration: int | None
    reported_by: dict[str, int] = Field(
        description="Per number, how many sessions reported it; fewer than sessions_with_usage "
        "means the sum covers only those sessions."
    )


class UsageReport(BaseModel):
    group: Group
    rows: list[UsageRow]
    total: UsageRow
    sessions_without_usage: int


class ToolRow(BaseModel):
    name: str
    calls: int
    sessions: int
    outcomes_known: int = Field(description="Calls whose result agdash could match to them.")
    errors: int = Field(description="Matched results that reported an error.")


class ToolReport(BaseModel):
    rows: list[ToolRow]
    total_calls: int
    total_sessions: int = Field(description="Sessions with at least one tool call.")
    truncated: bool = Field(description="Whether tools beyond the first hundred were left out.")
    outcomes_known: int
    errors: int


def _durations(db, scope, key_column):
    """Per key: sessions, summed known duration, sessions with a known duration."""
    found: dict[Any, list] = defaultdict(lambda: [0, 0.0, 0])
    for key, started, last in db.execute(
        select(key_column, Session.started_at, Session.last_activity_at).where(
            Session.id.in_(scope)
        )
    ):
        row = found[key]
        row[0] += 1
        if started and last and last >= started:
            row[1] += (last - started).total_seconds()
            row[2] += 1
    return found


@router.get("/analytics/usage", response_model=UsageReport, summary="Usage and duration, grouped")
def usage_report(
    db: Db,
    filters: Filters,
    group: Annotated[Group, Query(description="What to group by.")] = "harness",
) -> dict[str, Any]:
    scope = filters.apply(select(Session.id))
    usage_ids = _one_per_message().where(EventUsage.session_id.in_(scope))
    in_usage = EventUsage.event_id.in_(usage_ids)

    total_usage = db.execute(select(*_usage_columns(), *_reporting_columns()).where(in_usage)).one()
    with_usage = (
        db.scalar(select(func.count(func.distinct(EventUsage.session_id))).where(in_usage)) or 0
    )
    overall = _durations(db, scope, Session.harness)
    sessions = sum(row[0] for row in overall.values())
    known = sum(row[2] for row in overall.values())
    total = UsageRow(
        key=None,
        sessions=sessions,
        sessions_with_usage=with_usage,
        duration_seconds=sum(row[1] for row in overall.values()) if known else None,
        sessions_with_duration=known,
        reported_by=_reported_by(total_usage[7:]),
        **_usage(total_usage[:7]),
    )

    rows: list[UsageRow] = []
    if group == "model":
        model = EventUsage.model
        for key, reporting, *numbers in db.execute(
            select(
                model,
                func.count(func.distinct(EventUsage.session_id)),
                *_usage_columns(),
                *_reporting_columns(),
            )
            .where(in_usage)
            .group_by(model)
        ):
            # A session can use several models, so its duration belongs to none of them.
            rows.append(
                UsageRow(
                    key=key,
                    sessions=reporting,
                    sessions_with_usage=reporting,
                    duration_seconds=None,
                    sessions_with_duration=None,
                    reported_by=_reported_by(numbers[7:]),
                    **_usage(numbers[:7]),
                )
            )
    else:
        key_column = Session.harness if group == "harness" else Session.cwd
        usage_by_key = {
            key: (reporting, numbers)
            for key, reporting, *numbers in db.execute(
                select(
                    key_column,
                    func.count(func.distinct(EventUsage.session_id)),
                    *_usage_columns(),
                    *_reporting_columns(),
                )
                .join(Session, Session.id == EventUsage.session_id)
                .where(in_usage)
                .group_by(key_column)
            )
        }
        for key, (count, seconds, timed) in _durations(db, scope, key_column).items():
            reporting, numbers = usage_by_key.get(key, (0, [0] + [None] * 6 + [0] * 6))
            rows.append(
                UsageRow(
                    key=key,
                    sessions=count,
                    sessions_with_usage=reporting,
                    duration_seconds=seconds if timed else None,
                    sessions_with_duration=timed,
                    reported_by=_reported_by(numbers[7:]),
                    **_usage(numbers[:7]),
                )
            )
    rows.sort(key=lambda row: (-(row.output_tokens or 0), -row.sessions, row.key or ""))
    return {
        "group": group,
        "rows": rows,
        "total": total,
        "sessions_without_usage": sessions - with_usage,
    }


@router.get("/analytics/tools", response_model=ToolReport, summary="Tool mix")
def tool_report(db: Db, filters: Filters) -> dict[str, Any]:
    """Tool calls by tool, with errors where a call's result could be matched to it.

    Read from ``EventTool``, the calls and results kept beside the events, for the reason
    usage is read from ``EventUsage``.
    """
    scope = filters.apply(select(Session.id))
    call = EventTool
    name = call.name
    is_call = and_(call.kind == EventKind.TOOL_CALL, call.session_id.in_(scope))
    calls = (
        select(
            name.label("name"),
            func.count().label("calls"),
            func.count(func.distinct(call.session_id)),
        )
        .where(is_call)
        .group_by(name)
        .order_by(func.count().desc(), name)
        .limit(MAX_TOOLS)
    )
    result = aliased(EventTool)
    outcomes = {
        key: (matched, errors or 0)
        for key, matched, errors in db.execute(
            select(
                name,
                func.count(result.event_id),
                func.sum(case((result.is_error.is_(True), 1), else_=0)),
            )
            .join(
                result,
                and_(
                    result.session_id == call.session_id,
                    result.kind == EventKind.TOOL_RESULT,
                    result.call_id == call.call_id,
                ),
            )
            .where(is_call)
            .group_by(name)
        )
    }
    rows = [
        ToolRow(
            name=key or "",
            calls=count,
            sessions=in_sessions,
            outcomes_known=outcomes.get(key, (0, 0))[0],
            errors=outcomes.get(key, (0, 0))[1],
        )
        for key, count, in_sessions in db.execute(calls)
        if key
    ]
    total_calls, total_sessions = db.execute(
        select(func.count(), func.count(func.distinct(call.session_id))).where(is_call)
    ).one()
    distinct_tools = db.scalar(select(func.count(func.distinct(name))).where(is_call))
    return {
        "rows": rows,
        "total_calls": total_calls,
        "total_sessions": total_sessions,
        "truncated": (distinct_tools or 0) > MAX_TOOLS,
        "outcomes_known": sum(matched for matched, _ in outcomes.values()),
        "errors": sum(errors for _, errors in outcomes.values()),
    }
