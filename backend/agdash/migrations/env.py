"""Alembic environment for the agdash store."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from agdash.config import get_settings
from agdash.db import Base
from agdash.db.migrate import sync_database_url
from agdash.db.search import is_search_table

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(name, type_, parent_names) -> bool:
    """Leave the full-text index out of comparisons: it is derived data, not a model."""
    return not (type_ == "table" and is_search_table(name))


def database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or get_settings().database_url
    if not url:
        raise RuntimeError(
            "No database URL: pass one to agdash.db.migrate or set AGDASH_DATABASE_URL."
        )
    return sync_database_url(url)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot alter most things in place; batch mode rebuilds the table.
            render_as_batch=connection.dialect.name == "sqlite",
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
