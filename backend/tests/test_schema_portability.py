"""Column types must mean the same on SQLite and PostgreSQL, and every instant is UTC."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
    event,
    insert,
    select,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.schema import CreateIndex, CreateTable

from agdash.db import Base, UTCDateTime
from agdash.db.models import Event, RunLink, Session
from agdash.vocab import EventKind, SessionState

# LargeBinary is BLOB on SQLite and BYTEA on PostgreSQL.
PORTABLE_TYPES = (Integer, BigInteger, String, Text, Boolean, Float, JSON, LargeBinary, UTCDateTime)
POSTGRESQL_IDENTIFIER_LIMIT = 63
INSTANT_SUFFIX = "_at"

ALL_COLUMNS = [column for table in Base.metadata.sorted_tables for column in table.columns]


def _column_id(column) -> str:
    return f"{column.table.name}.{column.name}"


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.mark.parametrize("column", ALL_COLUMNS, ids=_column_id)
def test_column_type_is_portable(column):
    assert isinstance(column.type, PORTABLE_TYPES), type(column.type).__name__
    if isinstance(column.type, Enum):
        # A native enum is a PostgreSQL type that SQLite does not have.
        assert column.type.native_enum is False


@pytest.mark.parametrize("column", ALL_COLUMNS, ids=_column_id)
def test_instants_are_utc(column):
    holds_instant = isinstance(column.type, (DateTime, UTCDateTime))
    assert holds_instant == column.name.endswith(INSTANT_SUFFIX)
    if holds_instant:
        assert isinstance(column.type, UTCDateTime)


@pytest.mark.parametrize("table", Base.metadata.sorted_tables, ids=lambda table: table.name)
def test_table_compiles_for_postgresql(table):
    dialect = postgresql.dialect()
    ddl = str(CreateTable(table).compile(dialect=dialect))
    for index in table.indexes:
        ddl += str(CreateIndex(index).compile(dialect=dialect))

    assert "DATETIME" not in ddl
    assert "TIMESTAMP WITHOUT TIME ZONE" not in ddl
    instants = [column for column in table.columns if isinstance(column.type, UTCDateTime)]
    assert ddl.count("TIMESTAMP WITH TIME ZONE") == len(instants)


def test_identifiers_fit_postgresql():
    names = []
    for table in Base.metadata.sorted_tables:
        names.append(table.name)
        names.extend(column.name for column in table.columns)
        names.extend(index.name for index in table.indexes)
        names.extend(constraint.name for constraint in table.constraints if constraint.name)
    too_long = [name for name in names if len(str(name)) > POSTGRESQL_IDENTIFIER_LIMIT]
    assert too_long == []


def test_naive_instants_are_refused(engine):
    with OrmSession(engine) as store:
        store.add(Session(harness="pi", native_id="s-naive", started_at=datetime(2026, 1, 2, 3, 4)))
        with pytest.raises(StatementError, match="naive datetime refused"):
            store.flush()


def test_instants_come_back_in_utc(engine):
    local = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=3)))
    with OrmSession(engine) as store:
        store.add(Session(harness="pi", native_id="s-offset", started_at=local))
        store.commit()

    with engine.connect() as connection:
        stored_text = connection.exec_driver_sql("SELECT started_at FROM sessions").scalar_one()
    assert stored_text.startswith("2026-01-02 00:04:05")

    with OrmSession(engine) as store:
        session = store.scalars(select(Session)).one()
        assert session.started_at == local
        assert session.started_at.utcoffset() == timedelta(0)
        assert session.created_at.tzinfo is UTC


def test_a_new_session_is_unknown_not_ended(engine):
    with OrmSession(engine) as store:
        store.add(Session(harness="codex", native_id="s-new"))
        store.commit()
        assert store.scalars(select(Session.state)).one() is SessionState.UNKNOWN


def test_vocabulary_columns_refuse_unknown_values(engine):
    with OrmSession(engine) as store:
        session = Session(harness="codex", native_id="s-vocab")
        store.add(session)
        store.flush()
        with pytest.raises(StatementError):
            store.execute(
                insert(Event).values(session_id=session.id, sequence=0, kind="note", payload={})
            )


def test_vocabulary_columns_are_stored_as_plain_text(engine):
    with OrmSession(engine) as store:
        session = Session(harness="codex", native_id="s-text")
        store.add(session)
        store.flush()
        store.add(
            Event(
                session_id=session.id,
                sequence=0,
                kind=EventKind.USER_PROMPT,
                payload={"text": "x"},
            )
        )
        store.commit()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT kind FROM events").scalar_one() == "user_prompt"
        assert connection.exec_driver_sql("SELECT state FROM sessions").scalar_one() == "unknown"


def test_deleting_a_session_removes_what_was_observed_under_it(engine):
    with OrmSession(engine) as store:
        session = Session(harness="codex", native_id="s-cascade")
        store.add(session)
        store.flush()
        store.add(
            Event(session_id=session.id, sequence=0, kind=EventKind.STATUS, payload={"status": "x"})
        )
        store.commit()
        store.delete(session)
        store.commit()
        assert store.scalars(select(Event)).all() == []


def test_confidence_outside_zero_to_one_is_refused(engine):
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO connections (id, kind, base_url, sync_state, publish_enabled,"
            " created_at, updated_at) VALUES (1, 'workchord', 'http://tracker.test', 'never', 0,"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        )
    with OrmSession(engine) as store:
        session = Session(harness="codex", native_id="s-link")
        store.add(session)
        store.flush()
        store.add(
            RunLink(session_id=session.id, connection_id=1, basis="heuristic", confidence=1.5)
        )
        with pytest.raises(IntegrityError):
            store.flush()
