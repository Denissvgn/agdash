"""Service settings, read from the environment with the ``AGDASH_`` prefix."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGDASH_", extra="ignore")

    # The service is local to this host; it listens on loopback only.
    host: str = "127.0.0.1"
    port: int = 8765
    api_prefix: str = "/api"
    # No default: where the store lives is decided per platform, not here.
    database_url: str | None = None
    # Origins allowed besides the service's own loopback address and the desktop app.
    extra_origins: list[str] = []
    # Days of history to keep; unset keeps everything. Sessions that may still run are kept.
    retention_days: int | None = None
    # Keep the original harness lines, redacted and compressed, besides the events.
    keep_raw: bool = True
    # A port for the WorkChord webhook receiver, on the same loopback address; unset is off.
    webhook_port: int | None = None
    # Where a published WorkChord link points; {session_id} is replaced. Loopback only.
    publish_link: str = "http://127.0.0.1:8765/#/history/sessions/{session_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def database_url() -> str:
    """The configured store, or agdash's default database on this host."""
    configured = get_settings().database_url
    if configured:
        return configured
    from agdash.platform import current_host

    return f"sqlite:///{current_host().default_database_path()}"
