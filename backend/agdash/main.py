"""FastAPI application for the agdash service."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from agdash import __version__
from agdash.api.routes import router
from agdash.api.store import Store
from agdash.config import get_settings
from agdash.platform import current_host


def default_database_url() -> str:
    return f"sqlite:///{current_host().default_database_path()}"


def create_app(database_url: str | None = None) -> FastAPI:
    """The service's API. Nothing touches the database until the first request."""
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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
