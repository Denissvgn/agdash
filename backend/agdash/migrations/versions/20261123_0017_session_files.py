"""Find a session's files without reading through its events.

Revision ID: 0017
Revises: 0016

Every event carried the path of the file it was read from, and nothing else said
which files a session came from. Finding a session's archived lines, or the
archived files no session uses any more, read through the events: on a store of
1.7 million events, through gigabytes. The path on every event also took a fifth
of a gigabyte there.

``session_files`` holds each session's files once. It is filled from the events,
and the path is then dropped from them. Both read every event, and dropping the
column writes every event again: on a large store this takes some minutes and
room for the events once over, once.

Every step can be run again: what is there already is left as it is.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PATHS = "SELECT DISTINCT session_id, source_file FROM events WHERE source_file IS NOT NULL"


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _offline() -> bool:
    return op.get_context().as_sql


def _missing(table: str) -> bool:
    """Whether ``table`` is still to be made; always, when only the SQL is being written out."""
    return _offline() or not sa.inspect(op.get_bind()).has_table(table)


def _has_path() -> bool:
    """Whether events still carry their file's path; always, when only the SQL is written out."""
    if _offline():
        return True
    return "source_file" in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("events")
    }


def upgrade() -> None:
    if _missing("session_files"):
        op.create_table(
            "session_files",
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("path", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"],
                ["sessions.id"],
                name=op.f("fk_session_files_session_id_sessions"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("session_id", "path", name=op.f("pk_session_files")),
        )
    op.create_index(
        op.f("ix_session_files_path"), "session_files", ["path"], unique=False, if_not_exists=True
    )
    if not _has_path():
        return
    logging.getLogger("alembic.runtime.migration").info(
        "Noting each session's files and taking the path off its events;"
        " a large store takes some minutes."
    )
    if _is_sqlite():
        op.execute(f"INSERT OR IGNORE INTO session_files (session_id, path) {PATHS}")
        # Not through a batch: that would make the table anew and lose its triggers.
        op.execute("ALTER TABLE events DROP COLUMN source_file")
    else:
        op.execute(f"INSERT INTO session_files (session_id, path) {PATHS} ON CONFLICT DO NOTHING")
        op.drop_column("events", "source_file")


def downgrade() -> None:
    if _offline() or not _has_path():
        op.execute("ALTER TABLE events ADD COLUMN source_file TEXT")
    # A session read from one file gets it back on its events; one read from several
    # cannot be told apart event by event, and stays without.
    op.execute(
        "UPDATE events SET source_file = ("
        "SELECT min(path) FROM session_files WHERE session_files.session_id = events.session_id"
        " HAVING count(*) = 1)"
    )
    op.drop_index(op.f("ix_session_files_path"), table_name="session_files", if_exists=True)
    op.drop_table("session_files", if_exists=True)
