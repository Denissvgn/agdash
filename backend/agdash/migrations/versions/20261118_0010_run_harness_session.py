"""Keep the harness session a WorkChord run declares.

Revision ID: 0010
Revises: 0009

A run's actor may declare, at begin, which harness session does the work. Kept
on the local copy of the run, it links that session to the run exactly.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wc_runs", sa.Column("harness_kind", sa.String(length=40), nullable=True))
    op.add_column("wc_runs", sa.Column("harness_session_id", sa.String(length=255), nullable=True))
    op.create_index(
        op.f("ix_wc_runs_connection_id_harness_session_id"),
        "wc_runs",
        ["connection_id", "harness_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_wc_runs_connection_id_harness_session_id"), table_name="wc_runs")
    with op.batch_alter_table("wc_runs") as batch:
        batch.drop_column("harness_session_id")
        batch.drop_column("harness_kind")
