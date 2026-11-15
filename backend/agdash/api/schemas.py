"""What the read API returns. These models are the OpenAPI document's schemas."""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from agdash.vocab import EventKind, ParentBasis, SessionState, Surface, ThoughtsCoverage

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = Field(
        default=None, description="Pass as `cursor` to get the next page; null on the last page."
    )


class SessionSummary(BaseModel):
    id: int
    harness: str
    native_id: str
    surface: Surface | None
    title: str | None
    cwd: str | None
    branch: str | None
    state: SessionState
    started_at: datetime | None
    last_activity_at: datetime | None
    ended_at: datetime | None
    models: list[str]
    effort: str | None
    parent_session_id: int | None
    initial_task: str | None = Field(description="The first prompt, cut to 300 characters.")
    goal: dict[str, Any] | None
    event_count: int
    child_count: int


class SessionProcessOut(BaseModel):
    pid: int
    process_started_at: datetime
    parent_pid: int | None
    argv_redacted: list[str]
    first_seen_at: datetime
    last_seen_at: datetime


class UsageTotals(BaseModel):
    """Summed over a session's or a group's messages. A count nobody reported is null."""

    messages: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    reported_cost: float | None = None


class SessionDetail(SessionSummary):
    initial_task: str | None = Field(description="The first prompt, in full.")
    permission_mode: str | None
    auth_method: str | None
    account_label: str | None
    parent_basis: ParentBasis | None
    state_evidence: dict[str, Any]
    state_observed_at: datetime | None
    children: list[int]
    processes: list[SessionProcessOut]
    events_by_kind: dict[str, int]
    usage: UsageTotals


class EventOut(BaseModel):
    id: int
    session_id: int
    sequence: int
    occurred_at: datetime | None
    kind: EventKind
    payload: dict[str, Any]
    thoughts_coverage: ThoughtsCoverage | None


class SnippetPart(BaseModel):
    text: str
    match: bool = Field(description="Whether this part matched the query.")


class SearchHit(BaseModel):
    event_id: int
    session_id: int
    sequence: int
    kind: EventKind
    occurred_at: datetime | None
    harness: str
    session_title: str | None
    snippet: list[SnippetPart]


class UsageGroup(UsageTotals):
    harness: str
    model: str | None


class HealthRow(BaseModel):
    adapter: str
    format_version: str
    unknown_record_count: int
    parse_error_count: int
    last_seen_at: datetime | None


class Stats(BaseModel):
    since: datetime | None
    until: datetime | None
    sessions: int
    sessions_by_harness: dict[str, int]
    sessions_by_state: dict[str, int]
    sessions_by_surface: dict[str, int]
    events: int
    events_by_kind: dict[str, int]
    usage: list[UsageGroup]
    source_health: list[HealthRow]


class FacetValue(BaseModel):
    value: str
    count: int


class Facets(BaseModel):
    """The values session filters can take, with how many sessions have each."""

    harnesses: list[FacetValue]
    surfaces: list[FacetValue]
    states: list[FacetValue]
    models: list[FacetValue]
    folders: list[FacetValue] = Field(description="The most used folders, at most 100.")
    auth_methods: list[FacetValue]
    first_started_at: datetime | None
    last_started_at: datetime | None


class Excerpt(BaseModel):
    event_id: int
    sequence: int
    kind: EventKind
    occurred_at: datetime | None
    snippet: list[SnippetPart]


class SessionHit(SessionSummary):
    hits: int = Field(description="How many of the session's events match.")
    excerpts: list[Excerpt] = Field(description="The best matching events, at most two.")
