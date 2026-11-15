"""The API's connection to the store: opened lazily and only ever for reading.

Every connection the API makes is put in read-only mode by the database
itself, so a mistake in a handler cannot change collected data.
"""

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
        else:
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        cursor.close()

    return engine


class Store:
    """Where the API reads from. Nothing is opened until the first request."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._engine: Engine | None = None
        self._sessions: sessionmaker[DbSession] | None = None
        self._has_index: bool | None = None
        self._lock = Lock()

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
