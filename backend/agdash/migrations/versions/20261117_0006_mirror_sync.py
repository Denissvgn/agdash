"""Add the WorkChord mirror's sync status and each task's pipeline stage.

Revision ID: 0006
Revises: 0005

One row per connection and kind of object says how its last sync went, so an
object the credentials cannot read, or no longer parse, is reported instead of
silently going stale.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wc_sync_objects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name=op.f("fk_wc_sync_objects_connection_id_connections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wc_sync_objects")),
        sa.UniqueConstraint(
            "connection_id", "kind", name=op.f("uq_wc_sync_objects_connection_id_kind")
        ),
    )
    op.add_column("wc_tasks", sa.Column("pipeline_stage", sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("wc_tasks") as batch:
        batch.drop_column("pipeline_stage")
    op.drop_table("wc_sync_objects")
