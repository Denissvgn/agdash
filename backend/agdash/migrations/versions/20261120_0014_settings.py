"""Keep the choices made on the Settings pages.

Revision ID: 0014
Revises: 0013

Retention and the archive of original lines were set only where the service
starts. A row per setting holds what a person chose on the Settings pages
instead, and one more notes when sessions were last removed.

An upgrade that stopped before it was recorded is run again, so the table is
made only where it is not there yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_settings")),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table("settings", if_exists=True)
