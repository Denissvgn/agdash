"""Add the archive of raw harness lines.

Revision ID: 0005
Revises: 0004

Each row holds the lines of one read of one harness file, redacted and
zstd-compressed, so history survives when a harness prunes its own files.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_chunks",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("harness", sa.String(length=32), nullable=False),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("first_offset", sa.BigInteger(), nullable=False),
        sa.Column("end_offset", sa.BigInteger(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "end_offset >= first_offset", name=op.f("ck_raw_chunks_offsets_ordered")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raw_chunks")),
    )
    op.create_index(
        op.f("ix_raw_chunks_source_file_first_offset"),
        "raw_chunks",
        ["source_file", "first_offset"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_raw_chunks_source_file_first_offset"), table_name="raw_chunks")
    op.drop_table("raw_chunks")
