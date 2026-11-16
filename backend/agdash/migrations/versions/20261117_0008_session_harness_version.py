"""Remember the harness version a session's records state.

Revision ID: 0008
Revises: 0007

A harness states its version in a file's header or on each record. Kept on the
session, it lets a later read of the same file count unknown records and
unreadable lines under the right version.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("harness_version", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("harness_version")
