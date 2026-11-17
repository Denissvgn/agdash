"""Keep what the service itself uses, minute by minute.

Revision ID: 0016
Revises: 0015

The service samples its own process: CPU, memory, and what it reads from and
writes to storage. A row holds one minute or one hour of that, so a person can
see what the service cost overnight or last week and how fast its store grows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "service_usage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("cpu_seconds", sa.Float(), nullable=False),
        sa.Column("parts", sa.JSON(), nullable=False),
        sa.Column("footprint_bytes", sa.BigInteger(), nullable=True),
        sa.Column("resident_bytes", sa.BigInteger(), nullable=True),
        sa.Column("read_bytes", sa.BigInteger(), nullable=True),
        sa.Column("written_bytes", sa.BigInteger(), nullable=True),
        sa.Column("store_bytes", sa.BigInteger(), nullable=True),
        sa.Column("free_bytes", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_usage")),
        sa.UniqueConstraint(
            "period", "started_at", name=op.f("uq_service_usage_period_started_at")
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("service_usage", if_exists=True)
