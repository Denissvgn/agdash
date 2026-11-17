"""Index sessions by when they last changed.

Revision ID: 0013
Revises: 0012

The live stream asks twice a second, for every open window, which sessions
changed since its last look. Without an index that read every session each
time.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_sessions_updated_at"), "sessions", ["updated_at"], unique=False, if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_sessions_updated_at"), table_name="sessions")
