"""The full-text index over event text.

On SQLite the index is an FTS5 table, filled and emptied by triggers on
``events`` (created by a migration), so nothing that stores events needs to
know about it. It is derived data: it is not one of the models, and the schema
checks leave it out. Other databases have no index and fall back to a
substring match.
"""

SEARCH_TABLE = "event_search"


def is_search_table(name: str | None) -> bool:
    """Whether a table belongs to the index: the FTS5 table or one of its shadow tables."""
    return bool(name) and (name == SEARCH_TABLE or str(name).startswith(f"{SEARCH_TABLE}_"))
