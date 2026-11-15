"""A database built by the migrations alone must be the database the models describe."""

import io
import re

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from agdash.db import Base
from agdash.db.migrate import alembic_config, downgrade, sync_database_url, upgrade
from agdash.db.search import SEARCH_TABLE, is_search_table


def _describe(engine) -> dict:
    """Everything about the schema that a table definition can get wrong."""
    inspector = inspect(engine)
    described = {}
    for table in sorted(inspector.get_table_names()):
        if table == "alembic_version" or is_search_table(table):
            continue
        described[table] = {
            "columns": [
                (column["name"], str(column["type"]), column["nullable"], column["primary_key"])
                for column in inspector.get_columns(table)
            ],
            "primary_key": inspector.get_pk_constraint(table)["name"],
            "foreign_keys": sorted(
                (
                    key["name"],
                    tuple(key["constrained_columns"]),
                    key["referred_table"],
                    tuple(key["referred_columns"]),
                    key["options"].get("ondelete"),
                )
                for key in inspector.get_foreign_keys(table)
            ),
            "indexes": sorted(
                (index["name"], tuple(index["column_names"]), bool(index["unique"]))
                for index in inspector.get_indexes(table)
            ),
            "unique": sorted(
                (constraint["name"], tuple(constraint["column_names"]))
                for constraint in inspector.get_unique_constraints(table)
            ),
            "checks": sorted(
                (constraint["name"], re.sub(r"\s+", " ", constraint["sqltext"]))
                for constraint in inspector.get_check_constraints(table)
            ),
        }
    return described


@pytest.fixture
def migrated(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    upgrade(url)
    engine = create_engine(url)
    yield engine
    engine.dispose()


def test_migrations_create_every_model_table(migrated):
    tables = {t for t in inspect(migrated).get_table_names() if not is_search_table(t)}
    assert tables == set(Base.metadata.tables) | {"alembic_version"}
    assert SEARCH_TABLE in inspect(migrated).get_table_names(), "the search index is built"


def test_alembic_sees_no_difference_from_the_models(migrated):
    with migrated.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "compare_type": True,
                "include_name": lambda name, type_, _: (
                    not (type_ == "table" and is_search_table(name))
                ),
            },
        )
        assert compare_metadata(context, Base.metadata) == []


def test_migrated_schema_equals_the_schema_the_models_create(migrated, tmp_path):
    from_models = create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(from_models)
    try:
        expected = _describe(from_models)
        actual = _describe(migrated)
    finally:
        from_models.dispose()

    assert set(actual) == set(expected)
    for table in expected:
        assert actual[table] == expected[table], table
    # Guard the comparison itself: it must be looking at real constraints.
    assert any(expected[table]["checks"] for table in expected)
    assert any(expected[table]["foreign_keys"] for table in expected)


def test_downgrade_removes_every_table(tmp_path):
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    upgrade(url)
    downgrade(url, "base")
    engine = create_engine(url)
    try:
        assert inspect(engine).get_table_names() == ["alembic_version"]  # index gone too
    finally:
        engine.dispose()
    upgrade(url)


def test_history_has_one_head():
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    assert len(script.get_heads()) == 1


def test_migrations_do_not_import_the_models():
    script = ScriptDirectory.from_config(alembic_config("sqlite://"))
    for revision in script.walk_revisions():
        with open(revision.path, encoding="utf-8") as source:
            text = source.read()
        assert "agdash" not in text, revision.path


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("sqlite+aiosqlite:///./agdash.db", "sqlite:///./agdash.db"),
        ("sqlite:///./agdash.db", "sqlite:///./agdash.db"),
        ("postgresql+asyncpg://u:p@db.test/agdash", "postgresql+psycopg://u:p@db.test/agdash"),
    ],
)
def test_async_urls_are_mapped_to_blocking_drivers(given, expected):
    assert sync_database_url(given) == expected


def test_an_async_url_can_be_migrated(tmp_path):
    path = tmp_path / "async-url.db"
    upgrade(f"sqlite+aiosqlite:///{path}")
    engine = create_engine(f"sqlite:///{path}")
    try:
        assert "sessions" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_the_migrations_render_for_postgresql():
    """Offline mode writes the DDL without needing a PostgreSQL server."""
    config = alembic_config("postgresql://agdash@db.test/agdash")
    config.output_buffer = io.StringIO()
    command.upgrade(config, "head", sql=True)
    ddl = config.output_buffer.getvalue()

    assert ddl.count("CREATE TABLE") == len(Base.metadata.tables) + 1
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert "TIMESTAMP WITHOUT TIME ZONE" not in ddl
    assert "DATETIME" not in ddl
    # The event key is a 64-bit identity on PostgreSQL, a rowid on SQLite.
    assert re.search(r"CREATE TABLE events \(\s+id BIGSERIAL NOT NULL", ddl)
