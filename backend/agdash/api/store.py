"""The API's connections to the store, opened lazily.

Every reading connection is put in read-only mode by the database itself, so a
mistake in a handler cannot change collected data. The settings routes have one
more connection, for writing, which on SQLite the database lets change only
settings: the WorkChord connector's own tables (connections, mapping rules and
pins, and the mappings worked out from them) and agdash's own ``settings``.
Sessions, events and everything else collected stay out of its reach; removing
sessions past the retention limit is the service's keeper's work, not the API's.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.db.migrate import sync_database_url
from agdash.db.search import SEARCH_TABLE


def read_only_engine(database_url: str) -> Engine:
    url = sync_database_url(database_url)
    sqlite = url.startswith("sqlite")
    # Handlers run in a thread pool, so a connection may serve more than one thread.
    engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {})

    @event.listens_for(engine, "connect")
    def read_only(connection, _record) -> None:
        cursor = connection.cursor()
        if sqlite:
            cursor.execute("PRAGMA query_only = ON")
            cursor.execute("PRAGMA busy_timeout = 5000")
        else:
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        cursor.close()

    return engine


# What the API's writer may change; every other table is read-only to it.
WRITABLE = frozenset(
    {"connections", "mapping_rules", "identity_links", "project_links", "settings"}
)
_WRITES = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
_SCHEMA = {
    action
    for name, action in vars(sqlite3).items()
    if name.startswith(("SQLITE_CREATE", "SQLITE_DROP", "SQLITE_ALTER")) and isinstance(action, int)
}


def _authorize(action: int, first, _second, _database, _source) -> int:
    if action in _SCHEMA:
        return sqlite3.SQLITE_DENY
    if action in _WRITES and first not in WRITABLE:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def settings_engine(database_url: str) -> Engine:
    """A connection that may write settings, the connector's and agdash's own, and nothing else."""
    url = sync_database_url(database_url)
    sqlite = url.startswith("sqlite")
    engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {})
    if sqlite:

        @event.listens_for(engine, "connect")
        def guard(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()
            connection.set_authorizer(_authorize)

    return engine


class Store:
    """Where the API reads from. Nothing is opened until the first request."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._engine: Engine | None = None
        self._sessions: sessionmaker[DbSession] | None = None
        self._has_index: bool | None = None
        self._lock = Lock()
        self._writer: sessionmaker[DbSession] | None = None

    @property
    def engine(self) -> Engine:
        with self._lock:
            if self._engine is None:
                self._engine = read_only_engine(self.database_url)
                self._sessions = sessionmaker(self._engine, expire_on_commit=False)
            return self._engine

    @contextmanager
    def reading(self) -> Iterator[DbSession]:
        self.engine  # noqa: B018 - opens the engine on first use
        assert self._sessions is not None
        with self._sessions() as db:
            yield db

    @contextmanager
    def writing(self) -> Iterator[DbSession]:
        """For the settings routes only; see ``settings_engine``."""
        with self._lock:
            if self._writer is None:
                self._writer = sessionmaker(
                    settings_engine(self.database_url), expire_on_commit=False
                )
        with self._writer() as db:
            yield db

    def session(self) -> Iterator[DbSession]:
        """For FastAPI's dependency injection."""
        with self.reading() as db:
            yield db

    def has_search_index(self, db: DbSession) -> bool:
        if self._has_index is None:
            self._has_index = (
                db.bind is not None
                and db.bind.dialect.name == "sqlite"
                and bool(
                    db.execute(
                        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"),
                        {"name": SEARCH_TABLE},
                    ).first()
                )
            )
        return self._has_index

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
        if self._writer is not None:
            self._writer.kw["bind"].dispose()
