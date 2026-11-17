"""The full-text index over event text.

On SQLite the index is an FTS5 table, filled and emptied by triggers on
``events`` (created by a migration), so nothing that stores events needs to
know about it. It holds the index only: the text stays in ``events``, and the
index reads it through the view ``event_search_source`` when a snippet needs
it. It is derived data: it is not one of the models, and the schema checks
leave it out. Other databases have no index and fall back to a substring
match.
"""

SEARCH_TABLE = "event_search"


def is_search_table(name: str | None) -> bool:
    """Whether a name belongs to the index: the FTS5 table, a shadow table or its view."""
    return bool(name) and (name == SEARCH_TABLE or str(name).startswith(f"{SEARCH_TABLE}_"))
