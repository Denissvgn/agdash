"""Initial schema.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("base_url", sa.String(length=1024), nullable=False),
        sa.Column("credential_ref", sa.String(length=255), nullable=True),
        sa.Column("run_credential_ref", sa.String(length=255), nullable=True),
        sa.Column("contract_seen", sa.JSON(), nullable=True),
        sa.Column("sync_state", sa.String(length=32), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("publish_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connections")),
        sa.UniqueConstraint("kind", "base_url", name=op.f("uq_connections_kind_base_url")),
    )
    op.create_table(
        "ingest_cursors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("device", sa.String(length=20), nullable=True),
        sa.Column("inode", sa.String(length=20), nullable=False),
        sa.Column("byte_offset", sa.BigInteger(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "byte_offset >= 0 AND size >= 0", name=op.f("ck_ingest_cursors_positions_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingest_cursors")),
        sa.UniqueConstraint("harness", "path", name=op.f("uq_ingest_cursors_harness_path")),
    )
    op.create_index(
        op.f("ix_ingest_cursors_device_inode"), "ingest_cursors", ["device", "inode"], unique=False
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("surface", sa.String(length=32), nullable=True),
        sa.Column("native_id", sa.String(length=255), nullable=False),
        sa.Column("cwd", sa.Text(), nullable=True),
        sa.Column("repo", sa.Text(), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("worktree", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("initial_task", sa.Text(), nullable=True),
        sa.Column("models", sa.JSON(), nullable=False),
        sa.Column("effort", sa.String(length=32), nullable=True),
        sa.Column("permission_mode", sa.String(length=64), nullable=True),
        sa.Column("auth_method", sa.String(length=64), nullable=True),
        sa.Column("account_label", sa.String(length=255), nullable=True),
        sa.Column("parent_session_id", sa.Integer(), nullable=True),
        sa.Column("parent_basis", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("state_evidence", sa.JSON(), nullable=False),
        sa.Column("state_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_session_id"],
            ["sessions.id"],
            name=op.f("fk_sessions_parent_session_id_sessions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("harness", "native_id", name=op.f("uq_sessions_harness_native_id")),
    )
    op.create_index(
        op.f("ix_sessions_parent_session_id"), "sessions", ["parent_session_id"], unique=False
    )
    op.create_index(op.f("ix_sessions_started_at"), "sessions", ["started_at"], unique=False)
    op.create_index(
        op.f("ix_sessions_state_last_activity_at"),
        "sessions",
        ["state", "last_activity_at"],
        unique=False,
    )

    op.create_table(
        "source_health",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("adapter", sa.String(length=32), nullable=False),
        sa.Column("format_version", sa.String(length=64), nullable=False),
        sa.Column("unknown_record_count", sa.BigInteger(), nullable=False),
        sa.Column("parse_error_count", sa.BigInteger(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "unknown_record_count >= 0 AND parse_error_count >= 0",
            name=op.f("ck_source_health_counts_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_health")),
        sa.UniqueConstraint(
            "adapter", "format_version", name=op.f("uq_source_health_adapter_format_version")
        ),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("thoughts_coverage", sa.String(length=32), nullable=True),
        sa.Column("source_file", sa.Text(), nullable=True),
        sa.Column("source_offset", sa.BigInteger(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 0", name=op.f("ck_events_sequence_non_negative")),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_events_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("session_id", "sequence", name=op.f("uq_events_session_id_sequence")),
    )
    op.create_index(op.f("ix_events_kind"), "events", ["kind"], unique=False)
    op.create_index(
        op.f("ix_events_session_id_occurred_at"),
        "events",
        ["session_id", "occurred_at"],
        unique=False,
    )

    op.create_table(
        "identity_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("actor_remote_id", sa.Integer(), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_identity_links_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_identity_links_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_links")),
        sa.UniqueConstraint(
            "session_id",
            "connection_id",
            "actor_remote_id",
            "basis",
            name=op.f("uq_identity_links_session_actor_basis"),
        ),
    )
    op.create_index(
        op.f("ix_identity_links_connection_id_actor_remote_id"),
        "identity_links",
        ["connection_id", "actor_remote_id"],
        unique=False,
    )

    op.create_table(
        "mapping_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=True),
        sa.Column("match_kind", sa.String(length=32), nullable=False),
        sa.Column("match_value", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_value", sa.String(length=1024), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(match_kind = 'session' AND session_id IS NOT NULL AND match_value IS NULL)"
            " OR (match_kind <> 'session' AND session_id IS NULL AND match_value IS NOT NULL)",
            name=op.f("ck_mapping_rules_match_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_mapping_rules_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_mapping_rules_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mapping_rules")),
    )
    op.create_index(
        op.f("ix_mapping_rules_connection_id"), "mapping_rules", ["connection_id"], unique=False
    )
    op.create_index(
        op.f("ix_mapping_rules_session_id"), "mapping_rules", ["session_id"], unique=False
    )

    op.create_table(
        "project_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("project_remote_id", sa.Integer(), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_project_links_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_project_links_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_links")),
        sa.UniqueConstraint(
            "session_id",
            "connection_id",
            "project_remote_id",
            "basis",
            name=op.f("uq_project_links_session_project_basis"),
        ),
    )
    op.create_index(
        op.f("ix_project_links_connection_id_project_remote_id"),
        "project_links",
        ["connection_id", "project_remote_id"],
        unique=False,
    )

    op.create_table(
        "run_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("run_remote_id", sa.Integer(), nullable=True),
        sa.Column("task_remote_id", sa.Integer(), nullable=True),
        sa.Column("assignment_remote_id", sa.Integer(), nullable=True),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_run_links_confidence_range")
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_run_links_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_run_links_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_links")),
    )
    op.create_index(
        op.f("ix_run_links_connection_id_run_remote_id"),
        "run_links",
        ["connection_id", "run_remote_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_run_links_connection_id_task_remote_id"),
        "run_links",
        ["connection_id", "task_remote_id"],
        unique=False,
    )
    op.create_index(op.f("ix_run_links_session_id"), "run_links", ["session_id"], unique=False)

    op.create_table(
        "session_processes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("process_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parent_pid", sa.Integer(), nullable=True),
        sa.Column("argv_redacted", sa.JSON(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_processes_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_processes")),
        sa.UniqueConstraint(
            "session_id",
            "pid",
            "process_started_at",
            name=op.f("uq_session_processes_session_id_pid_process_started_at"),
        ),
    )
    op.create_index(
        op.f("ix_session_processes_pid_process_started_at"),
        "session_processes",
        ["pid", "process_started_at"],
        unique=False,
    )

    op.create_table(
        "usage_snapshots",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_read_tokens", sa.BigInteger(), nullable=True),
        sa.Column("cache_write_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True),
        sa.Column("reported_cost", sa.Float(), nullable=True),
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "(input_tokens IS NULL OR input_tokens >= 0)"
            " AND (output_tokens IS NULL OR output_tokens >= 0)"
            " AND (cache_read_tokens IS NULL OR cache_read_tokens >= 0)"
            " AND (cache_write_tokens IS NULL OR cache_write_tokens >= 0)"
            " AND (reasoning_tokens IS NULL OR reasoning_tokens >= 0)",
            name=op.f("ck_usage_snapshots_tokens_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_usage_snapshots_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_snapshots")),
    )
    op.create_index(
        op.f("ix_usage_snapshots_session_id_observed_at"),
        "usage_snapshots",
        ["session_id", "observed_at"],
        unique=False,
    )

    op.create_table(
        "wc_actors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("remote_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=30), nullable=True),
        sa.Column("lifecycle_state", sa.String(length=50), nullable=True),
        sa.Column("runtime_ref", sa.String(length=1024), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_wc_actors_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wc_actors")),
        sa.UniqueConstraint(
            "connection_id", "remote_id", name=op.f("uq_wc_actors_connection_id_remote_id")
        ),
    )
    op.create_table(
        "wc_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("remote_id", sa.Integer(), nullable=False),
        sa.Column("actor_remote_id", sa.Integer(), nullable=True),
        sa.Column("task_remote_id", sa.Integer(), nullable=True),
        sa.Column("project_remote_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(length=30), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("runtime_ref", sa.String(length=1024), nullable=True),
        sa.Column("model_binding", sa.String(length=255), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_wc_assignments_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wc_assignments")),
        sa.UniqueConstraint(
            "connection_id", "remote_id", name=op.f("uq_wc_assignments_connection_id_remote_id")
        ),
    )
    op.create_index(
        op.f("ix_wc_assignments_connection_id_actor_remote_id"),
        "wc_assignments",
        ["connection_id", "actor_remote_id"],
        unique=False,
    )

    op.create_table(
        "wc_projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("remote_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("health", sa.String(length=50), nullable=True),
        sa.Column("initiative_name", sa.String(length=255), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_wc_projects_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wc_projects")),
        sa.UniqueConstraint(
            "connection_id", "remote_id", name=op.f("uq_wc_projects_connection_id_remote_id")
        ),
    )
    op.create_table(
        "wc_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("remote_id", sa.Integer(), nullable=False),
        sa.Column("task_remote_id", sa.Integer(), nullable=True),
        sa.Column("actor_remote_id", sa.Integer(), nullable=True),
        sa.Column("assignment_remote_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("trace_id", sa.String(length=255), nullable=True),
        sa.Column("configured_model_alias", sa.String(length=255), nullable=True),
        sa.Column("resolved_model_id", sa.String(length=255), nullable=True),
        sa.Column("run_metadata", sa.JSON(), nullable=True),
        sa.Column("declared_usage", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_wc_runs_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wc_runs")),
        sa.UniqueConstraint(
            "connection_id", "remote_id", name=op.f("uq_wc_runs_connection_id_remote_id")
        ),
    )
    op.create_index(
        op.f("ix_wc_runs_connection_id_task_remote_id"),
        "wc_runs",
        ["connection_id", "task_remote_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wc_runs_connection_id_trace_id"),
        "wc_runs",
        ["connection_id", "trace_id"],
        unique=False,
    )

    op.create_table(
        "wc_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("remote_id", sa.Integer(), nullable=False),
        sa.Column("project_remote_id", sa.Integer(), nullable=True),
        sa.Column("iteration_remote_id", sa.Integer(), nullable=True),
        sa.Column("iteration_name", sa.String(length=255), nullable=True),
        sa.Column("external_key", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("claim_holder_remote_id", sa.Integer(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_wc_tasks_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wc_tasks")),
        sa.UniqueConstraint(
            "connection_id", "remote_id", name=op.f("uq_wc_tasks_connection_id_remote_id")
        ),
    )
    op.create_index(
        op.f("ix_wc_tasks_connection_id_external_key"),
        "wc_tasks",
        ["connection_id", "external_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wc_tasks_connection_id_project_remote_id"),
        "wc_tasks",
        ["connection_id", "project_remote_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_wc_tasks_connection_id_project_remote_id"), table_name="wc_tasks")
    op.drop_index(op.f("ix_wc_tasks_connection_id_external_key"), table_name="wc_tasks")

    op.drop_table("wc_tasks")
    op.drop_index(op.f("ix_wc_runs_connection_id_trace_id"), table_name="wc_runs")
    op.drop_index(op.f("ix_wc_runs_connection_id_task_remote_id"), table_name="wc_runs")

    op.drop_table("wc_runs")
    op.drop_table("wc_projects")
    op.drop_index(
        op.f("ix_wc_assignments_connection_id_actor_remote_id"), table_name="wc_assignments"
    )

    op.drop_table("wc_assignments")
    op.drop_table("wc_actors")
    op.drop_index(op.f("ix_usage_snapshots_session_id_observed_at"), table_name="usage_snapshots")

    op.drop_table("usage_snapshots")
    op.drop_index(
        op.f("ix_session_processes_pid_process_started_at"), table_name="session_processes"
    )

    op.drop_table("session_processes")
    op.drop_index(op.f("ix_run_links_session_id"), table_name="run_links")
    op.drop_index(op.f("ix_run_links_connection_id_task_remote_id"), table_name="run_links")
    op.drop_index(op.f("ix_run_links_connection_id_run_remote_id"), table_name="run_links")

    op.drop_table("run_links")
    op.drop_index(
        op.f("ix_project_links_connection_id_project_remote_id"), table_name="project_links"
    )

    op.drop_table("project_links")
    op.drop_index(op.f("ix_mapping_rules_session_id"), table_name="mapping_rules")
    op.drop_index(op.f("ix_mapping_rules_connection_id"), table_name="mapping_rules")

    op.drop_table("mapping_rules")
    op.drop_index(
        op.f("ix_identity_links_connection_id_actor_remote_id"), table_name="identity_links"
    )

    op.drop_table("identity_links")
    op.drop_index(op.f("ix_events_session_id_occurred_at"), table_name="events")
    op.drop_index(op.f("ix_events_kind"), table_name="events")

    op.drop_table("events")
    op.drop_table("source_health")
    op.drop_index(op.f("ix_sessions_state_last_activity_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_started_at"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_parent_session_id"), table_name="sessions")

    op.drop_table("sessions")
    op.drop_index(op.f("ix_ingest_cursors_device_inode"), table_name="ingest_cursors")

    op.drop_table("ingest_cursors")
    op.drop_table("connections")
