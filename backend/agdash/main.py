"""FastAPI application for the agdash service."""

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agdash import __version__
from agdash.api import (
    analytics,
    exports,
    mapping,
    projects,
    series,
    service,
    sources,
    storage,
    stream,
    timeline,
    workchord,
)
from agdash.api.routes import router
from agdash.api.security import LazyToken, LocalAccess, load_or_create_token
from agdash.api.store import Store
from agdash.api.ui import mount_ui, ui_directory
from agdash.config import database_url, get_settings
from agdash.platform import Host, current_host


def default_database_url() -> str:
    return database_url()


def stored_token() -> str:
    return load_or_create_token(current_host().secret_store())


def create_app(
    database_url: str | None = None,
    *,
    token: str | Callable[[], str] | None = None,
    port: int | None = None,
    ui_dir: str | None = None,
    reading: Callable[[], object] | None = None,
    host: Host | None = None,
    retention: object | None = None,
    usage: object | None = None,
) -> FastAPI:
    """The service's API. Nothing touches the database or the keyring until the first request.

    ``reading`` reports how far the collector's start-up read has got, when the
    collector runs in this process. ``host`` is the machine whose harness folders are
    reported; left out, it is this one. ``retention`` is the service's keeper, which
    removes sessions past the limit; without one this process only reports. ``usage`` is
    the service's sampler of its own process; without one only the rows it kept are served.
    """
    settings = get_settings()
    store = Store(database_url or settings.database_url or default_database_url())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        store.dispose()

    app = FastAPI(
        title="agdash",
        version=__version__,
        summary="Read API over the agentic coding sessions seen on this host, with the "
        "WorkChord connector's settings and agdash's own.",
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.reading = reading
    app.state.host = host
    app.state.retention = retention
    app.state.service_usage = usage
    app.include_router(router, prefix=settings.api_prefix)
    app.include_router(stream.router, prefix=settings.api_prefix)
    app.include_router(exports.router, prefix=settings.api_prefix)
    app.include_router(analytics.router, prefix=settings.api_prefix)
    app.include_router(series.router, prefix=settings.api_prefix)
    app.include_router(timeline.router, prefix=settings.api_prefix)
    app.include_router(mapping.router, prefix=settings.api_prefix)
    app.include_router(workchord.router, prefix=settings.api_prefix)
    app.include_router(projects.router, prefix=settings.api_prefix)
    app.include_router(sources.router, prefix=settings.api_prefix)
    app.include_router(storage.router, prefix=settings.api_prefix)
    app.include_router(service.router, prefix=settings.api_prefix)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    # Last, so the API and /health come first.
    directory = ui_directory(ui_dir if ui_dir is not None else settings.ui_dir)
    open_paths, open_prefixes = mount_ui(app, directory) if directory else ((), ())

    if isinstance(token, str):
        fixed = token
        source: Callable[[], str] = lambda: fixed  # noqa: E731
    else:
        source = token or stored_token
    app.add_middleware(
        LocalAccess,
        token=LazyToken(source),
        port=port or settings.port,
        extra_origins=settings.extra_origins,
        open_paths=open_paths,
        open_prefixes=open_prefixes,
    )
    return app


app = create_app()
