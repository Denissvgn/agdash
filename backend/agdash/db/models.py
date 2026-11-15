"""Tables of the agdash store.

Three groups: what was observed on this host (sessions, their processes,
events and usage), the read cache of a connected WorkChord server (``wc_*``),
and the evidence-backed links between the two.

Links and rules point at WorkChord objects by ``connection_id`` plus the
server's own id, not by a foreign key into the cache. The cache can then be
dropped and refetched without losing a link or a pin the user made.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from agdash.db.base import (
    Base,
    BigIntegerKey,
    UTCDateTime,
    json_document,
    utcnow,
    vocabulary,
)
from agdash.vocab import (
    EventKind,
    IdentityLinkBasis,
    ParentBasis,
    ProjectLinkBasis,
    RuleMatchKind,
    RuleTargetKind,
    RunLinkBasis,
    SessionState,
    Surface,
    SyncState,
    ThoughtsCoverage,
    UsageScope,
)

HARNESS_LENGTH = 32
REMOTE_REF_LENGTH = 1024


class Session(Base):
    """One agent session of one harness, live or historical."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("harness", "native_id"),
        Index(None, "state", "last_activity_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    harness: Mapped[str] = mapped_column(String(HARNESS_LENGTH), nullable=False)
    surface: Mapped[Surface | None] = mapped_column(vocabulary(Surface), nullable=True)
    native_id: Mapped[str] = mapped_column(String(255), nullable=False)
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    repo: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    worktree: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    initial_task: Mapped[str | None] = mapped_column(Text, nullable=True)
    models: Mapped[list[str]] = mapped_column(json_document(), nullable=False, default=list)
    effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    permission_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auth_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_basis: Mapped[ParentBasis | None] = mapped_column(vocabulary(ParentBasis), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    state: Mapped[SessionState] = mapped_column(
        vocabulary(SessionState), nullable=False, default=SessionState.UNKNOWN
    )
    state_evidence: Mapped[dict[str, Any]] = mapped_column(
        json_document(), nullable=False, default=dict
    )
    state_observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )
    # The harness's objective for the session: objective, status, budget, usage.
    goal: Mapped[dict[str, Any] | None] = mapped_column(json_document(), nullable=True)


class SessionProcess(Base):
    """A process seen running a session. A pid is only unique with its start time."""

    __tablename__ = "session_processes"
    __table_args__ = (
        UniqueConstraint("session_id", "pid", "process_started_at"),
        Index(None, "pid", "process_started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    process_started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    parent_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    argv_redacted: Mapped[list[str]] = mapped_column(json_document(), nullable=False, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class Event(Base):
    """One normalised transcript record. ``kind`` and ``payload`` follow the event contract."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence"),
        Index(None, "session_id", "occurred_at"),
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
    )

    id: Mapped[int] = mapped_column(BigIntegerKey, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    kind: Mapped[EventKind] = mapped_column(vocabulary(EventKind), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(json_document(), nullable=False)
    thoughts_coverage: Mapped[ThoughtsCoverage | None] = mapped_column(
        vocabulary(ThoughtsCoverage), nullable=True
    )
    source_file: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_offset: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class UsageSnapshot(Base):
    """Token and cost numbers as a harness reported them. A missing number is NULL, not zero."""

    __tablename__ = "usage_snapshots"
    __table_args__ = (
        Index(None, "session_id", "observed_at"),
        CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0)"
            " AND (output_tokens IS NULL OR output_tokens >= 0)"
            " AND (cache_read_tokens IS NULL OR cache_read_tokens >= 0)"
            " AND (cache_write_tokens IS NULL OR cache_write_tokens >= 0)"
            " AND (reasoning_tokens IS NULL OR reasoning_tokens >= 0)",
            name="tokens_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(BigIntegerKey, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    scope: Mapped[UsageScope] = mapped_column(vocabulary(UsageScope), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reported_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)


class Connection(Base):
    """A connected tracker server. Holds the keyring entry names, never a credential."""

    __tablename__ = "connections"
    __table_args__ = (UniqueConstraint("kind", "base_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="workchord")
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_credential_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contract_seen: Mapped[dict[str, Any] | None] = mapped_column(json_document(), nullable=True)
    sync_state: Mapped[SyncState] = mapped_column(
        vocabulary(SyncState), nullable=False, default=SyncState.NEVER
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )


class WcProject(Base):
    __tablename__ = "wc_projects"
    __table_args__ = (UniqueConstraint("connection_id", "remote_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    health: Mapped[str | None] = mapped_column(String(50), nullable=True)
    initiative_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WcTask(Base):
    __tablename__ = "wc_tasks"
    __table_args__ = (
        UniqueConstraint("connection_id", "remote_id"),
        Index(None, "connection_id", "external_key"),
        Index(None, "connection_id", "project_remote_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    project_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iteration_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    iteration_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    claim_holder_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WcActor(Base):
    __tablename__ = "wc_actors"
    __table_args__ = (UniqueConstraint("connection_id", "remote_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    lifecycle_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    runtime_ref: Mapped[str | None] = mapped_column(String(REMOTE_REF_LENGTH), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WcAssignment(Base):
    __tablename__ = "wc_assignments"
    __table_args__ = (
        UniqueConstraint("connection_id", "remote_id"),
        Index(None, "connection_id", "actor_remote_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    runtime_ref: Mapped[str | None] = mapped_column(String(REMOTE_REF_LENGTH), nullable=True)
    model_binding: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WcRun(Base):
    __tablename__ = "wc_runs"
    __table_args__ = (
        UniqueConstraint("connection_id", "remote_id"),
        Index(None, "connection_id", "trace_id"),
        Index(None, "connection_id", "task_remote_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    task_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignment_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    configured_model_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_metadata: Mapped[dict[str, Any] | None] = mapped_column(json_document(), nullable=True)
    declared_usage: Mapped[dict[str, Any] | None] = mapped_column(json_document(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class RunLink(Base):
    """A session tied to a WorkChord run, task or assignment, with the evidence for it.

    An ``mcp_config`` link names only the server, so all three ids may be NULL.
    """

    __tablename__ = "run_links"
    __table_args__ = (
        Index(None, "connection_id", "run_remote_id"),
        Index(None, "connection_id", "task_remote_id"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    run_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignment_remote_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    basis: Mapped[RunLinkBasis] = mapped_column(vocabulary(RunLinkBasis), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(json_document(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class IdentityLink(Base):
    """A session tied to a WorkChord actor, with the evidence for it."""

    __tablename__ = "identity_links"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "connection_id",
            "actor_remote_id",
            "basis",
            name="uq_identity_links_session_actor_basis",
        ),
        Index(None, "connection_id", "actor_remote_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    actor_remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    basis: Mapped[IdentityLinkBasis] = mapped_column(vocabulary(IdentityLinkBasis), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(json_document(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class ProjectLink(Base):
    """A session tied to a WorkChord project, with the evidence for it."""

    __tablename__ = "project_links"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "connection_id",
            "project_remote_id",
            "basis",
            name="uq_project_links_session_project_basis",
        ),
        Index(None, "connection_id", "project_remote_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False
    )
    project_remote_id: Mapped[int] = mapped_column(Integer, nullable=False)
    basis: Mapped[ProjectLinkBasis] = mapped_column(vocabulary(ProjectLinkBasis), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(json_document(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class MappingRule(Base):
    """A rule or pin the user made: sessions that match resolve to a WorkChord target.

    A pin (``match_kind = session``) names one session by ``session_id``; every
    other kind matches by ``match_value``, optionally limited to one harness.
    """

    __tablename__ = "mapping_rules"
    __table_args__ = (
        CheckConstraint(
            "(match_kind = 'session' AND session_id IS NOT NULL AND match_value IS NULL)"
            " OR (match_kind <> 'session' AND session_id IS NULL AND match_value IS NOT NULL)",
            name="match_shape",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    harness: Mapped[str | None] = mapped_column(String(HARNESS_LENGTH), nullable=True)
    match_kind: Mapped[RuleMatchKind] = mapped_column(vocabulary(RuleMatchKind), nullable=False)
    match_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_kind: Mapped[RuleTargetKind] = mapped_column(vocabulary(RuleTargetKind), nullable=False)
    target_value: Mapped[str] = mapped_column(String(REMOTE_REF_LENGTH), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )


class IngestCursor(Base):
    """How far a harness file has been read.

    ``device`` and ``inode`` are decimal text because they are unsigned 64-bit
    values on some file systems and would not fit a signed integer column.
    """

    __tablename__ = "ingest_cursors"
    __table_args__ = (
        UniqueConstraint("harness", "path"),
        Index(None, "device", "inode"),
        CheckConstraint("byte_offset >= 0 AND size >= 0", name="positions_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    harness: Mapped[str] = mapped_column(String(HARNESS_LENGTH), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    device: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inode: Mapped[str] = mapped_column(String(20), nullable=False)
    byte_offset: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow
    )


class SourceHealth(Base):
    """Per adapter and format version: how much of what it read it could not understand."""

    __tablename__ = "source_health"
    __table_args__ = (
        UniqueConstraint("adapter", "format_version"),
        CheckConstraint(
            "unknown_record_count >= 0 AND parse_error_count >= 0", name="counts_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    adapter: Mapped[str] = mapped_column(String(HARNESS_LENGTH), nullable=False)
    format_version: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    unknown_record_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    parse_error_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class RawChunk(Base):
    """Original harness lines from one read of one file, redacted and zstd-compressed.

    Harnesses prune and archive their own files, and normalised events keep only
    what the contract names, so the lines themselves are kept here. A chunk is
    stored in the same transaction as the ingest cursor that read it.
    """

    __tablename__ = "raw_chunks"
    __table_args__ = (
        Index(None, "source_file", "first_offset"),
        CheckConstraint("end_offset >= first_offset", name="offsets_ordered"),
    )

    id: Mapped[int] = mapped_column(BigIntegerKey, primary_key=True)
    harness: Mapped[str] = mapped_column(String(HARNESS_LENGTH), nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    first_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
