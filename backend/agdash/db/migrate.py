"""Run the bundled Alembic migrations against a database URL."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Migrations run on a blocking connection; map each async driver to its
# blocking counterpart.
_SYNC_DRIVERS = {
    "sqlite+aiosqlite": "sqlite",
    "postgresql+asyncpg": "postgresql+psycopg",
}


def sync_database_url(database_url: str) -> str:
    url = make_url(database_url)
    driver = _SYNC_DRIVERS.get(url.drivername, url.drivername)
    return url.set(drivername=driver).render_as_string(hide_password=False)


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Alembic reads options through configparser, where "%" starts an interpolation.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)
