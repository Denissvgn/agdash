"""Declarative base and the column types every table is built from.

Only types that mean the same thing on SQLite and PostgreSQL are used, so the
store can move to PostgreSQL without a data conversion.
"""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, BigInteger, DateTime, Enum, Integer, MetaData
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator[datetime]):
    """An instant, always stored and returned in UTC.

    Naive datetimes are refused rather than guessed at. SQLite keeps no offset,
    so the value is normalised to UTC before it is written and UTC is attached
    again when it is read.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetime refused: instants must carry a UTC offset")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def vocabulary(members: type[StrEnum], length: int = 32) -> Enum:
    """A string column limited to the values of ``members``.

    Stored as plain text with no database constraint, so adding a member never
    rebuilds a table. Unknown values are refused when a statement is bound.
    """
    return Enum(
        members,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        values_callable=lambda enum: [member.value for member in enum],
        length=length,
    )


def json_document() -> JSON:
    """A JSON value. Python ``None`` is stored as SQL NULL, not as JSON ``null``."""
    return JSON(none_as_null=True)


# SQLite only auto-assigns a rowid to a column declared exactly INTEGER.
BigIntegerKey = BigInteger().with_variant(Integer(), "sqlite")
