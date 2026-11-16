"""Record the links added on WorkChord tasks in publish mode.

Revision ID: 0011
Revises: 0010

Publish mode, off by default, adds one link per observed session on the task it
worked on. A row per session and task keeps it from being sent twice.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "published_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("task_remote_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("remote_link_id", sa.Integer(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_published_links_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_published_links_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_published_links")),
        sa.UniqueConstraint(
            "connection_id",
            "session_id",
            "task_remote_id",
            name=op.f("uq_published_links_connection_id_session_id_task_remote_id"),
        ),
    )
    op.create_index(
        op.f("ix_published_links_session_id"), "published_links", ["session_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_published_links_session_id"), table_name="published_links")
    op.drop_table("published_links")
