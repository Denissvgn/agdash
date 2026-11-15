"""Add a full-text index over event text, on SQLite.

Revision ID: 0004
Revises: 0003

An FTS5 table holds the searchable text of each event, keyed by the event id.
Triggers keep it in step with ``events``, and existing events are indexed
here. The text comes from the payload fields that carry it, depending on the
kind of event. Other databases get no index; search falls back to a substring
match there.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIELDS = ("text", "output", "summary", "detail", "name", "input", "prompt", "result")


def _body(row: str) -> str:
    parts = [f"coalesce(json_extract({row}.payload, '$.{field}'), '')" for field in FIELDS]
    return " || ' ' || ".join(parts)


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def upgrade() -> None:
    if not _is_sqlite():
        return
    op.execute(
        "CREATE VIRTUAL TABLE event_search USING fts5("
        "body, tokenize = 'unicode61 remove_diacritics 2')"
    )
    op.execute(f"INSERT INTO event_search (rowid, body) SELECT id, {_body('events')} FROM events")
    op.execute(
        "CREATE TRIGGER event_search_insert AFTER INSERT ON events BEGIN "
        f"INSERT INTO event_search (rowid, body) VALUES (new.id, {_body('new')}); END"
    )
    op.execute(
        "CREATE TRIGGER event_search_delete AFTER DELETE ON events BEGIN "
        "DELETE FROM event_search WHERE rowid = old.id; END"
    )
    op.execute(
        "CREATE TRIGGER event_search_update AFTER UPDATE OF payload ON events BEGIN "
        f"UPDATE event_search SET body = {_body('new')} WHERE rowid = new.id; END"
    )


def downgrade() -> None:
    if not _is_sqlite():
        return
    for trigger in ("event_search_update", "event_search_delete", "event_search_insert"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS event_search")
