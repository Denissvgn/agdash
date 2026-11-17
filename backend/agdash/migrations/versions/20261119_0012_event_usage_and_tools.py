"""Keep usage numbers and tool calls beside their events, in narrow tables.

Revision ID: 0012
Revises: 0011

Every kind of event shares one table, and payloads can be large: tool results
run to kilobytes each. Adding up usage, or counting tool calls, from the
payloads reads through all of it, and on a store of millions of events that
takes minutes. These two tables hold only what the reports add up, one row per
usage event and one per tool call or result, so a report reads megabytes
instead of gigabytes.

On SQLite, triggers keep both tables in step with ``events``, as they do the
search index, and the events already stored are copied here; on a large store
that copy takes a few minutes, once. Other databases get the tables without
the triggers.

SQLite runs these statements one by one, not in one transaction, and the copy
is long enough to be interrupted. So every step can be run again: what is
already there is left as it is, and the copy skips the rows it already made.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOKENS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
USAGE_COLUMNS = (
    "event_id",
    "session_id",
    "occurred_at",
    "scope",
    "message_key",
    "model",
    *TOKENS,
    "reported_cost",
)
TOOL_COLUMNS = ("event_id", "session_id", "occurred_at", "kind", "name", "call_id", "is_error")
TRIGGERS = ("event_facts_insert", "event_facts_update", "event_facts_delete")


def _text(row: str, path: str) -> str:
    return f"CAST(json_extract({row}.payload, '$.{path}') AS VARCHAR)"


def _usage(row: str) -> str:
    """The usage columns' values for one row of ``events``, in order."""
    values = [
        f"{row}.id",
        f"{row}.session_id",
        f"{row}.occurred_at",
        _text(row, "scope"),
        # Some harnesses repeat a message's usage; without a message id each event stands alone.
        f"coalesce({_text(row, 'extra.message_id')}, CAST({row}.id AS VARCHAR))",
        _text(row, "model"),
        *(f"CAST(json_extract({row}.payload, '$.{name}') AS INTEGER)" for name in TOKENS),
        f"CAST(json_extract({row}.payload, '$.reported_cost') AS FLOAT)",
    ]
    return ", ".join(values)


def _tool(row: str) -> str:
    values = [
        f"{row}.id",
        f"{row}.session_id",
        f"{row}.occurred_at",
        f"{row}.kind",
        _text(row, "name"),
        _text(row, "call_id"),
        f"json_extract({row}.payload, '$.is_error')",
    ]
    return ", ".join(values)


def _copy(row: str, only: str = "") -> str:
    """Statements that write the narrow rows of the events ``row`` names."""
    return (
        f"INSERT INTO event_usage ({', '.join(USAGE_COLUMNS)}) SELECT {_usage(row)} "
        f"FROM events AS {row} WHERE {row}.kind = 'usage'{only}; "
        f"INSERT INTO event_tools ({', '.join(TOOL_COLUMNS)}) SELECT {_tool(row)} "
        f"FROM events AS {row} WHERE {row}.kind IN ('tool_call', 'tool_result'){only};"
    )


def _is_sqlite() -> bool:
    return op.get_context().dialect.name == "sqlite"


def _missing(table: str) -> bool:
    """Whether ``table`` is still to be made; always, when only the SQL is being written out."""
    return op.get_context().as_sql or not sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    key = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    if _missing("event_usage"):
        _create_usage(key)
    if _missing("event_tools"):
        _create_tools(key)
    op.create_index(
        op.f("ix_event_usage_session_id_message_key"),
        "event_usage",
        ["session_id", "message_key"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        op.f("ix_event_tools_session_id_call_id"),
        "event_tools",
        ["session_id", "call_id"],
        unique=False,
        if_not_exists=True,
    )
    if not _is_sqlite():
        return
    # The triggers first, so nothing stored from here on is missed; then what is there already.
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS event_facts_insert AFTER INSERT ON events "
        "WHEN new.kind IN ('usage', 'tool_call', 'tool_result') BEGIN "
        f"{_copy('e', ' AND e.id = new.id')} END"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS event_facts_update "
        "AFTER UPDATE OF payload, kind, occurred_at, session_id ON events BEGIN "
        "DELETE FROM event_usage WHERE event_id = old.id; "
        "DELETE FROM event_tools WHERE event_id = old.id; "
        f"{_copy('e', ' AND e.id = new.id')} END"
    )
    op.execute(
        "CREATE TRIGGER IF NOT EXISTS event_facts_delete AFTER DELETE ON events BEGIN "
        "DELETE FROM event_usage WHERE event_id = old.id; "
        "DELETE FROM event_tools WHERE event_id = old.id; END"
    )
    logging.getLogger("alembic.runtime.migration").info(
        "Copying usage and tool events into their own tables; a large store takes a few minutes."
    )
    for statement in _copy("e").rstrip(";").split("; "):
        op.execute(statement.replace("INSERT INTO", "INSERT OR IGNORE INTO", 1))


def _create_usage(key) -> None:
    op.create_table(
        "event_usage",
        sa.Column("event_id", key, autoincrement=False, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.String(length=32), nullable=True),
        sa.Column("message_key", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=True),
        *(sa.Column(name, sa.BigInteger(), nullable=True) for name in TOKENS),
        sa.Column("reported_cost", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_usage_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_event_usage")),
    )


def _create_tools(key) -> None:
    op.create_table(
        "event_tools",
        sa.Column("event_id", key, autoincrement=False, nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("call_id", sa.String(length=255), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_tools_event_id_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_event_tools")),
    )


def downgrade() -> None:
    if _is_sqlite():
        for trigger in TRIGGERS:
            op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.drop_index(op.f("ix_event_tools_session_id_call_id"), table_name="event_tools")
    op.drop_table("event_tools")
    op.drop_index(op.f("ix_event_usage_session_id_message_key"), table_name="event_usage")
    op.drop_table("event_usage")
