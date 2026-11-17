"""Stop keeping a second copy of event text in the search index.

Revision ID: 0015
Revises: 0014

The FTS5 table kept every event's searchable text beside its index, and that
text is already in ``events.payload``: on a store of 1.7 million events the
copy was a quarter of the whole file. The index is rebuilt here as an FTS5
table with external content. It keeps only the index and reads the text, when
a snippet needs it, from a view over ``events`` that gives each event's text
as the index was given it.

An index with external content cannot look up what it once indexed, so the
triggers hand it the old text when an event is removed or changed.

Rebuilding reads every event once; on a large store that takes a few minutes,
once. The pages the copy held become free pages inside the file, which the
store fills again before it grows.

SQLite runs these statements one by one, not in one transaction. Every step
can be run again: an interrupted run leaves an index that the next run drops
and builds anew.
"""

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIELDS = ("text", "output", "summary", "detail", "name", "input", "prompt", "result")
TOKENIZE = "tokenize = 'unicode61 remove_diacritics 2'"
TRIGGERS = ("event_search_update", "event_search_delete", "event_search_insert")


def _body(row: str) -> str:
    parts = [f"coalesce(json_extract({row}.payload, '$.{field}'), '')" for field in FIELDS]
    return " || ' ' || ".join(parts)


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _drop_index() -> None:
    for trigger in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS event_search")


def _forget(row: str) -> str:
    """Take an event's text out of the index; it must be the text that was put in."""
    return (
        "INSERT INTO event_search (event_search, rowid, body) "
        f"VALUES ('delete', {row}.id, {_body(row)});"
    )


def upgrade() -> None:
    if not _is_sqlite():
        return
    logging.getLogger("alembic.runtime.migration").info(
        "Rebuilding the search index without its copy of the text;"
        " a large store takes a few minutes."
    )
    _drop_index()
    op.execute(
        "CREATE VIEW IF NOT EXISTS event_search_source AS "
        f"SELECT id, {_body('events')} AS body FROM events"
    )
    op.execute(
        "CREATE VIRTUAL TABLE event_search USING fts5("
        f"body, content = 'event_search_source', content_rowid = 'id', {TOKENIZE})"
    )
    op.execute("INSERT INTO event_search (event_search) VALUES ('rebuild')")
    op.execute(
        "CREATE TRIGGER event_search_insert AFTER INSERT ON events BEGIN "
        f"INSERT INTO event_search (rowid, body) VALUES (new.id, {_body('new')}); END"
    )
    op.execute(
        f"CREATE TRIGGER event_search_delete AFTER DELETE ON events BEGIN {_forget('old')} END"
    )
    op.execute(
        "CREATE TRIGGER event_search_update AFTER UPDATE OF payload ON events BEGIN "
        f"{_forget('old')} "
        f"INSERT INTO event_search (rowid, body) VALUES (new.id, {_body('new')}); END"
    )


def downgrade() -> None:
    if not _is_sqlite():
        return
    _drop_index()
    op.execute("DROP VIEW IF EXISTS event_search_source")
    op.execute(f"CREATE VIRTUAL TABLE event_search USING fts5(body, {TOKENIZE})")
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
