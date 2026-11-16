"""Record each change of a session's state.

Revision ID: 0009
Revises: 0008

The session row holds only its current state. One row per change, with what
the new state rests on, lets a session's states be drawn over time.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "session_state_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("basis", sa.String(length=32), nullable=False),
        sa.Column("previous", sa.String(length=32), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_session_state_changes_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_session_state_changes")),
    )
    op.create_index(
        op.f("ix_session_state_changes_session_id_changed_at"),
        "session_state_changes",
        ["session_id", "changed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_session_state_changes_session_id_changed_at"), table_name="session_state_changes"
    )
    op.drop_table("session_state_changes")
