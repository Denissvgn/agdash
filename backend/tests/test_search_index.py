"""The search index holds no text of its own and stays in step with the events."""

from datetime import UTC, datetime

from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.orm import sessionmaker

from agdash.db.migrate import downgrade, upgrade
from agdash.db.models import Event, Session
from agdash.vocab import EventKind

WHEN = datetime(2026, 11, 16, 12, tzinfo=UTC)
TRIGGERS = {"event_search_insert", "event_search_update", "event_search_delete"}


def store(tmp_path, revision: str = "head"):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url, revision)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return url, engine, sessionmaker(engine, expire_on_commit=False)


def fill(db) -> Session:
    """One session: a prompt, a tool call with structured input, its result, and a reply."""
    # Stored through the table, with these columns only: a store at an earlier revision
    # does not have the columns added to sessions since.
    row = {"harness": "codex", "native_id": "synthetic", "models": []}
    made = db.execute(insert(Session.__table__).values(**row))
    session = Session(id=made.inserted_primary_key[0], **row)
    payloads = [
        (EventKind.USER_PROMPT, {"text": "please plant the quince orchard"}),
        (
            EventKind.TOOL_CALL,
            {"name": "dig_hole", "call_id": "call-1", "input": {"depth": "kneedeep"}},
        ),
        (EventKind.TOOL_RESULT, {"call_id": "call-1", "output": "the spade struck flint"}),
        (EventKind.ASSISTANT_TEXT, {"text": "Vérité: the orchard is planted"}),
    ]
    for sequence, (kind, payload) in enumerate(payloads):
        db.add(
            Event(
                session_id=session.id,
                sequence=sequence,
                kind=kind,
                payload=payload,
                occurred_at=WHEN,
            )
        )
    db.commit()
    return session


def found(db, query: str) -> list[int]:
    """The sequences of the events the index gives for ``query``."""
    return list(
        db.scalars(
            text(
                "SELECT e.sequence FROM event_search JOIN events e ON e.id = event_search.rowid"
                " WHERE event_search MATCH :query ORDER BY e.sequence"
            ),
            {"query": query},
        )
    )


def names(db, kind: str) -> set[str]:
    return set(
        db.scalars(text("SELECT name FROM sqlite_master WHERE type = :kind"), {"kind": kind})
    )


def consistent(db) -> None:
    """The index holds exactly the words of the events' text; SQLite raises if it does not."""
    db.execute(text("INSERT INTO event_search (event_search) VALUES ('integrity-check')"))


def check(db) -> None:
    assert found(db, "quince") == [0]
    assert found(db, "orchard") == [0, 3]
    assert found(db, "kneedeep") == [1], "structured input is searched too"
    assert found(db, "dig_hole") == [1]
    assert found(db, "flint") == [2]
    assert found(db, "verite") == [3], "accents are folded"
    assert found(db, "absent") == []
    consistent(db)


def test_the_index_keeps_no_copy_of_the_text(tmp_path):
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        fill(db)
        check(db)
        tables = names(db, "table")
        assert "event_search" in tables
        assert "event_search_content" not in tables
        assert "event_search_source" in names(db, "view")
        marked = db.scalar(
            text(
                "SELECT snippet(event_search, 0, '[', ']', '…', 16) FROM event_search"
                " WHERE event_search MATCH 'flint'"
            )
        )
        assert marked.strip() == "the spade struck [flint]", "a snippet reads the event's text"
    engine.dispose()


def test_a_changed_or_removed_event_leaves_the_index(tmp_path):
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        session = fill(db)
        prompt = db.scalars(select(Event).where(Event.sequence == 0)).one()
        prompt.payload = {"text": "please prune the medlar hedge"}
        db.commit()
        assert found(db, "quince") == []
        assert found(db, "medlar") == [0]
        assert found(db, "orchard") == [3]
        consistent(db)

        db.delete(db.scalars(select(Event).where(Event.sequence == 2)).one())
        db.commit()
        assert found(db, "flint") == []
        consistent(db)

        # Removing a session takes its events along, and their words with them.
        db.delete(db.get(Session, session.id))
        db.commit()
        assert db.scalar(text("SELECT count(*) FROM events")) == 0
        assert found(db, "orchard") == []
        assert db.scalar(text("SELECT count(*) FROM event_search_docsize")) == 0
        consistent(db)
    engine.dispose()


def test_events_stored_before_are_indexed_again(tmp_path):
    url, engine, sessions = store(tmp_path, "0014")
    with sessions() as db:
        fill(db)
        assert "event_search_content" in names(db, "table")
    engine.dispose()
    upgrade(url)
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        check(db)
        assert "event_search_content" not in names(db, "table")
    engine.dispose()


def test_an_interrupted_upgrade_can_be_run_again(tmp_path):
    url, engine, sessions = store(tmp_path, "0014")
    with sessions() as db:
        fill(db)
    engine.dispose()
    upgrade(url)
    # As if the process had stopped while the index was being filled: the new index is
    # there but empty, nothing keeps it in step, and the upgrade is not recorded.
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = '0014'"))
        for trigger in TRIGGERS:
            connection.execute(text(f"DROP TRIGGER {trigger}"))
        connection.execute(text("INSERT INTO event_search (event_search) VALUES ('delete-all')"))
    engine.dispose()
    upgrade(url)
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        check(db)
        assert TRIGGERS <= names(db, "trigger")
    engine.dispose()


def test_going_back_gives_the_index_its_own_text_again(tmp_path):
    url, engine, sessions = store(tmp_path)
    with sessions() as db:
        fill(db)
    engine.dispose()
    downgrade(url, "0014")
    engine = create_engine(url)
    with sessionmaker(engine)() as db:
        assert found(db, "quince") == [0]
        assert found(db, "kneedeep") == [1]
        assert "event_search_content" in names(db, "table")
        assert "event_search_source" not in names(db, "view")
        db.execute(text("DELETE FROM events WHERE sequence = 0"))
        assert found(db, "quince") == []
    engine.dispose()
