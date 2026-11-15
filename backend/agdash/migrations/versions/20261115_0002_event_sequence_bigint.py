"""Widen events.sequence to 64 bits.

Revision ID: 0002
Revises: 0001

A sequence is built from a byte offset in the source file, which passes the
32-bit range for large transcripts.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.alter_column(
            "sequence", existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.alter_column(
            "sequence", existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False
        )
