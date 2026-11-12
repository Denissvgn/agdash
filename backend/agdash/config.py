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


@lru_cache
def get_settings() -> Settings:
    return Settings()
