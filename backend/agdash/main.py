"""FastAPI application for the agdash service."""

from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agdash import __version__
from agdash.api import analytics, exports, series, stream
from agdash.api.routes import router
from agdash.api.security import LazyToken, LocalAccess, load_or_create_token
from agdash.api.store import Store
from agdash.config import database_url, get_settings
from agdash.platform import current_host


def default_database_url() -> str:
    return database_url()


def stored_token() -> str:
    return load_or_create_token(current_host().secret_store())


def create_app(
    database_url: str | None = None,
    *,
    token: str | Callable[[], str] | None = None,
    port: int | None = None,
) -> FastAPI:
    """The service's API. Nothing touches the database or the keyring until the first request."""
    settings = get_settings()
    store = Store(database_url or settings.database_url or default_database_url())

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        store.dispose()

    app = FastAPI(
        title="agdash",
        version=__version__,
        summary="Read-only API over the agentic coding sessions seen on this host.",
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )
    app.state.store = store
    app.include_router(router, prefix=settings.api_prefix)
    app.include_router(stream.router, prefix=settings.api_prefix)
    app.include_router(exports.router, prefix=settings.api_prefix)
    app.include_router(analytics.router, prefix=settings.api_prefix)
    app.include_router(series.router, prefix=settings.api_prefix)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

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
    )
    return app


app = create_app()
