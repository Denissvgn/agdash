"""What the service itself costs this machine.

* ``GET /api/service/usage``: the service's own CPU, memory and storage traffic,
  and its store's size, now and over the last hour, day or ninety days, with the
  CPU each part of the service took.

It reads what the service's sampler measured of its own process: the last hour
from memory, anything older from the rows it kept. A process with no sampler, a
test or a second copy of the API, still serves the rows.
"""

import math
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from agdash.api.routes import Db
from agdash.db.models import ServiceUsage
from agdash.service_usage import HOUR, MINUTE, Sampler

router = APIRouter()

Range = Literal["hour", "day", "quarter"]
# Each range as a step in seconds and how many steps it holds.
STEPS: dict[str, tuple[int, int]] = {
    "hour": (10, 360),
    "day": (300, 288),
    "quarter": (21600, 360),
}
# The last hour from kept rows, when no sampler runs here: a minute is their finest step.
HOUR_FROM_ROWS = (60, 60)


class ServiceNow(BaseModel):
    at: datetime = Field(description="When this was measured.")
    cpu_percent: float | None = Field(description="Of one core, over the last sample.")
    footprint_bytes: int | None = Field(
        description="Memory the process is charged with, compressed and swapped included."
    )
    resident_bytes: int | None
    read_bytes_per_second: float | None = Field(description="Read from storage.")
    written_bytes_per_second: float | None = Field(description="Written to storage.")
    store_bytes: int | None = Field(description="The store's file and its write-ahead log.")
    free_bytes: int | None = Field(description="Room left on the store's volume.")


class ServicePoint(BaseModel):
    at: datetime = Field(description="The start of the step.")
    cpu_percent: float | None = Field(description="Null where nothing was measured.")
    footprint_bytes: int | None = Field(description="The largest within the step.")
    read_bytes_per_second: float | None
    written_bytes_per_second: float | None
    store_bytes: int | None = Field(description="As the step ended.")


class ServicePart(BaseModel):
    part: str = Field(description="ingest, probe, watch, mirror, retention, api, monitor, other.")
    cpu_seconds: float
    cpu_percent: float = Field(description="Of one core, over the time measured.")


class ServiceUsageReport(BaseModel):
    known: bool = Field(description="Whether a sampler runs in this process.")
    since: datetime | None = Field(description="When this process began sampling.")
    cpus: int | None = Field(description="Cores this machine has, to read the percentages by.")
    range: Range
    step_seconds: int
    measured_seconds: float = Field(description="How much of the range was measured.")
    now: ServiceNow | None
    points: list[ServicePoint]
    parts: list[ServicePart]


def _rate(amount: float | None, seconds: float) -> float | None:
    return None if amount is None or seconds <= 0 else amount / seconds


def _rows(db, period: str, start: datetime) -> list[ServiceUsage]:
    return list(
        db.scalars(
            select(ServiceUsage)
            .where(ServiceUsage.period == period, ServiceUsage.started_at >= start)
            .order_by(ServiceUsage.started_at)
        )
    )


def _pieces(db, sampler: Sampler | None, chosen: str, start: datetime) -> list[Any]:
    """Samples or rows covering the range; both carry the same measures."""
    if chosen == "hour" and sampler is not None:
        return sampler.recent()
    if chosen != "quarter":
        return _rows(db, MINUTE, start)
    hours = _rows(db, HOUR, start)
    # The hours not folded yet are still minutes.
    later = hours[-1].started_at + timedelta(hours=1) if hours else start
    return [*hours, *_rows(db, MINUTE, later)]


def _points(pieces: Iterable[Any], start: datetime, step: int, count: int) -> list[dict[str, Any]]:
    buckets: list[list[Any]] = [[] for _ in range(count)]
    for piece in pieces:
        index = int((piece.started_at - start).total_seconds() // step)
        if 0 <= index < count:
            buckets[index].append(piece)
    points = []
    for index, held in enumerate(buckets):
        seconds = sum(piece.seconds for piece in held)
        footprints = [p.footprint_bytes for p in held if p.footprint_bytes is not None]
        stores = [p.store_bytes for p in held if p.store_bytes is not None]
        reads = [p.read_bytes for p in held if p.read_bytes is not None]
        writes = [p.written_bytes for p in held if p.written_bytes is not None]
        points.append(
            {
                "at": start + timedelta(seconds=index * step),
                "cpu_percent": _rate(100 * sum(p.cpu_seconds for p in held), seconds)
                if held
                else None,
                "footprint_bytes": max(footprints) if footprints else None,
                "read_bytes_per_second": _rate(sum(reads), seconds) if reads else None,
                "written_bytes_per_second": _rate(sum(writes), seconds) if writes else None,
                "store_bytes": stores[-1] if stores else None,
            }
        )
    return points


def _parts(pieces: Iterable[Any], seconds: float) -> list[dict[str, Any]]:
    spent: dict[str, float] = {}
    for piece in pieces:
        for part, cpu in piece.parts.items():
            spent[part] = spent.get(part, 0.0) + cpu
    return [
        {"part": part, "cpu_seconds": cpu, "cpu_percent": _rate(100 * cpu, seconds) or 0.0}
        for part, cpu in sorted(spent.items(), key=lambda item: (-item[1], item[0]))
    ]


def _now(piece: Any | None) -> dict[str, Any] | None:
    if piece is None:
        return None
    return {
        "at": piece.started_at + timedelta(seconds=piece.seconds),
        "cpu_percent": _rate(100 * piece.cpu_seconds, piece.seconds),
        "footprint_bytes": piece.footprint_bytes,
        "resident_bytes": piece.resident_bytes,
        "read_bytes_per_second": _rate(piece.read_bytes, piece.seconds),
        "written_bytes_per_second": _rate(piece.written_bytes, piece.seconds),
        "store_bytes": piece.store_bytes,
        "free_bytes": piece.free_bytes,
    }


@router.get(
    "/service/usage",
    response_model=ServiceUsageReport,
    summary="What the service itself uses",
)
def service_usage(request: Request, db: Db, range: Range = "hour") -> dict[str, Any]:  # noqa: A002
    """The service's own CPU, memory, storage traffic and store size, now and over a range.

    A step in which nothing was measured, because the service was not running, has
    nulls, never zeros.
    """
    return usage_report(db, getattr(request.app.state, "service_usage", None), range)


def usage_report(
    db,
    sampler: Sampler | None,
    chosen: Range = "hour",
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """What ``/service/usage`` answers. With no sampler it is made of the kept rows
    alone, which is also how the ``agdash usage`` command reads it, from the store."""
    step, count = STEPS[chosen] if sampler is not None or chosen != "hour" else HOUR_FROM_ROWS
    now = now or datetime.now(UTC)
    end = datetime.fromtimestamp(math.ceil(now.timestamp() / step) * step, UTC)
    start = end - timedelta(seconds=step * count)
    pieces = [piece for piece in _pieces(db, sampler, chosen, start) if piece.started_at >= start]
    recent = sampler.recent() if sampler is not None else []
    latest = recent[-1] if recent else None
    if sampler is None:
        latest = db.scalars(
            select(ServiceUsage)
            .where(ServiceUsage.period == MINUTE)
            .order_by(ServiceUsage.started_at.desc())
            .limit(1)
        ).first()
    measured = sum(piece.seconds for piece in pieces)
    return {
        "known": sampler is not None,
        "since": sampler.since if sampler is not None else None,
        "cpus": os.cpu_count(),
        "range": chosen,
        "step_seconds": step,
        "measured_seconds": measured,
        "now": _now(latest),
        "points": _points(pieces, start, step, count),
        "parts": _parts(pieces, measured),
    }
