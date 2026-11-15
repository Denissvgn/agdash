"""Running agdash on this host: its private store, the collector and the API in one process.

The data directory is created readable by its owner only (0700) and the SQLite
database likewise (0600). The store runs in write-ahead mode, so the API reads
while the collector writes. The service refuses any address that is not
loopback before anything listens.
"""

import contextlib
import logging
import os
import signal
import stat
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from agdash.api.security import NotLoopback, ensure_loopback
from agdash.collector.run import Collector
from agdash.config import database_url, get_settings
from agdash.db.migrate import sync_database_url, upgrade
from agdash.main import create_app
from agdash.platform import Host, current_host

log = logging.getLogger("agdash")


class _Server(uvicorn.Server):
    """Shuts down on SIGTERM or SIGINT without re-raising the signal afterwards.

    Recent uvicorn versions re-raise a captured signal once shut down, which would
    end the process before the collector stops, and with a status that tells the
    service manager the service failed.
    """

    @contextlib.contextmanager
    def capture_signals(self):
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        handled = (signal.SIGINT, signal.SIGTERM)
        previous = {sig: signal.signal(sig, self.handle_exit) for sig in handled}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


PRIVATE_DIR = 0o700
PRIVATE_FILE = 0o600


@dataclass(frozen=True)
class Store:
    url: str
    data_dir: Path
    database_file: Path | None


def sqlite_file(url: str) -> Path | None:
    parsed = make_url(sync_database_url(url))
    if (
        parsed.get_backend_name() != "sqlite"
        or not parsed.database
        or parsed.database == ":memory:"
    ):
        return None
    return Path(parsed.database)


def prepare(host: Host | None = None) -> Store:
    """Create the private data directory, bring the store up to date and lock it down."""
    host = host or current_host()
    data_dir = host.data_dir()
    data_dir.mkdir(mode=PRIVATE_DIR, parents=True, exist_ok=True)
    data_dir.chmod(PRIVATE_DIR)
    url = database_url()
    path = sqlite_file(url)
    if path is not None:
        path.parent.mkdir(mode=PRIVATE_DIR, parents=True, exist_ok=True)
    upgrade(url)
    if path is not None and path.exists():
        path.chmod(PRIVATE_FILE)
    (data_dir / "snapshots").mkdir(mode=PRIVATE_DIR, exist_ok=True)
    return Store(url=url, data_dir=data_dir, database_file=path)


def writer_engine(url: str) -> Engine:
    """The collector's connection: write-ahead log, a wait instead of a lock error."""
    url = sync_database_url(url)
    sqlite = url.startswith("sqlite")
    engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {})
    if sqlite:

        @event.listens_for(engine, "connect")
        def tune(connection, _record) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
            cursor.execute("PRAGMA busy_timeout = 5000")
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.close()

    return engine


def collector_for(store: Store, host: Host | None = None) -> tuple[Collector, Engine]:
    engine = writer_engine(store.url)
    collector = Collector(
        sessionmaker(engine, expire_on_commit=False),
        host or current_host(),
        snapshot_dir=store.data_dir / "snapshots",
        keep_raw=get_settings().keep_raw,
    )
    return collector, engine


def serve() -> int:
    """The service: the collector and the read API, until the process is told to stop."""
    settings = get_settings()
    try:
        ensure_loopback(settings.host)
    except NotLoopback as error:
        print(f"agdash: refusing to start: {error}", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    store = prepare()
    collector, engine = collector_for(store)
    stop = threading.Event()
    threads = collector.run(stop)
    log.info("collecting; serving on %s:%d", settings.host, settings.port)
    try:
        config = uvicorn.Config(
            create_app(store.url, port=settings.port),
            host=settings.host,
            port=settings.port,
            access_log=False,
            server_header=False,
            proxy_headers=False,
            timeout_graceful_shutdown=2,
            log_level="warning",
        )
        _Server(config).run()
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=10)
        engine.dispose()
    return 0


def private(path: Path) -> bool:
    """Whether only the owner can read ``path``."""
    try:
        return stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0
    except OSError:
        return False
