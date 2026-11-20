"""Remember what a harness says a session is.

Revision ID: 0018
Revises: 0017

Codex checks what an agent is about to do in a thread of its own, and records it
as a subagent of the thread it checks. Stored like any subagent, those checks
stood beside the agents on every board and in every count of subagents. The
kind a session's records state is kept on the session, so the two can be told
apart. A session read before this has no kind until its header is read again.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_kind() -> bool:
    """Whether sessions carry a kind already; never, when only the SQL is written out."""
    if op.get_context().as_sql:
        return False
    return "kind" in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("sessions")
    }


def upgrade() -> None:
    # Can be run again: an upgrade that stopped before it was recorded has added it already.
    if not _has_kind():
        op.add_column("sessions", sa.Column("kind", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("kind")
