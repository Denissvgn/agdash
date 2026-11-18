"""A session's files are noted once, beside its events, and found without reading them."""

from datetime import UTC, datetime

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from agdash.adapters.store import ParsedEvent, find_or_create_session, store_events
from agdash.archive import source_files
from agdash.db.migrate import downgrade, upgrade
from agdash.db.models import Event, SessionFile
from agdash.vocab import EventKind

WHEN = datetime(2026, 11, 16, 12, tzinfo=UTC)
TRIGGERS = {
    "event_search_insert",
    "event_search_update",
    "event_search_delete",
    "event_facts_insert",
    "event_facts_update",
    "event_facts_delete",
}


def store(tmp_path, revision: str = "head"):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url, revision)
    engine = create_engine(url)
    return url, engine, sessionmaker(engine, expire_on_commit=False)


def prompt(words: str) -> list[ParsedEvent]:
    return [ParsedEvent(EventKind.USER_PROMPT, {"text": words}, WHEN)]


def test_storing_events_notes_their_file_once(tmp_path):
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        session = find_or_create_session(db, "pi", "synthetic")
        assert store_events(db, session, 0, prompt("plant the orchard"), "/files/a.jsonl") == 1
        assert store_events(db, session, 40, prompt("prune the hedge"), "/files/a.jsonl") == 1
        assert store_events(db, session, 0, prompt("dig the bed"), "/files/b.jsonl") == 0
        db.commit()
        # The same line read again, from a file the session's events did not come from,
        # adds no event and so no file.
        assert source_files(db, session.id) == ["/files/a.jsonl"]
        assert store_events(db, session, 80, prompt("dig the bed"), "/files/b.jsonl") == 1
        db.commit()
        assert source_files(db, session.id) == ["/files/a.jsonl", "/files/b.jsonl"]
        assert db.scalars(select(SessionFile.session_id)).all() == [session.id, session.id]
        assert "source_file" not in {c["name"] for c in inspect(engine).get_columns("events")}
    engine.dispose()


def old_store(tmp_path):
    """A store from before, whose events carry their file's path."""
    url, engine, _ = store(tmp_path, "0016")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO sessions (id, harness, native_id, models, state, state_evidence,"
                " created_at, updated_at) VALUES"
                " (1, 'pi', 'one', '[]', 'unknown', '{}', :at, :at),"
                " (2, 'pi', 'two', '[]', 'unknown', '{}', :at, :at)"
            ),
            {"at": "2026-11-16 12:00:00.000000"},
        )
        rows = [
            (1, 0, "user_prompt", '{"text": "plant the quince orchard"}', "/files/one.jsonl"),
            (1, 32, "tool_result", '{"call_id": "c", "output": "flint"}', "/files/one.jsonl"),
            (2, 0, "user_prompt", '{"text": "first half"}', "/files/two-a.jsonl"),
            (2, 32, "user_prompt", '{"text": "second half"}', "/files/two-b.jsonl"),
            (2, 64, "status", '{"status": "x"}', None),
        ]
        for session_id, sequence, kind, payload, path in rows:
            connection.execute(
                text(
                    "INSERT INTO events (session_id, sequence, kind, payload, source_file,"
                    " source_offset, recorded_at) VALUES (:s, :q, :k, :p, :f, :o, :at)"
                ),
                {
                    "s": session_id,
                    "q": sequence,
                    "k": kind,
                    "p": payload,
                    "f": path,
                    "o": sequence // 32,
                    "at": "2026-11-16 12:00:00.000000",
                },
            )
    engine.dispose()
    return url


def check(engine) -> None:
    with sessionmaker(engine)() as db:
        assert source_files(db, 1) == ["/files/one.jsonl"]
        assert source_files(db, 2) == ["/files/two-a.jsonl", "/files/two-b.jsonl"]
        assert db.scalar(text("SELECT count(*) FROM events")) == 5
        columns = [column["name"] for column in inspect(engine).get_columns("events")]
        assert "source_file" not in columns and "source_offset" in columns
        triggers = set(db.scalars(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")))
        assert TRIGGERS <= triggers, "the events keep their triggers"
        # And the triggers still work on the table as it now is.
        found = db.scalars(
            text("SELECT rowid FROM event_search WHERE event_search MATCH 'quince'")
        ).all()
        assert len(found) == 1
        assert db.scalar(text("SELECT count(*) FROM event_tools")) == 1
        db.execute(text("INSERT INTO event_search (event_search) VALUES ('integrity-check')"))
        db.add(
            Event(session_id=1, sequence=96, kind=EventKind.USER_PROMPT, payload={"text": "medlar"})
        )
        db.commit()
        assert (
            db.scalars(
                text("SELECT count(*) FROM event_search WHERE event_search MATCH 'medlar'")
            ).one()
            == 1
        )


def test_files_are_taken_from_the_events_that_carried_them(tmp_path):
    url = old_store(tmp_path)
    upgrade(url)
    engine = create_engine(url)
    check(engine)
    engine.dispose()


def test_an_interrupted_upgrade_can_be_run_again(tmp_path):
    url = old_store(tmp_path)
    # As if the process had stopped with the files noted in part and the path still there.
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE session_files (session_id INTEGER NOT NULL, path TEXT NOT NULL,"
                " CONSTRAINT pk_session_files PRIMARY KEY (session_id, path),"
                " CONSTRAINT fk_session_files_session_id_sessions FOREIGN KEY(session_id)"
                " REFERENCES sessions (id) ON DELETE CASCADE)"
            )
        )
        connection.execute(text("INSERT INTO session_files VALUES (1, '/files/one.jsonl')"))
    engine.dispose()
    upgrade(url)
    engine = create_engine(url)
    check(engine)
    engine.dispose()


def test_going_back_gives_events_their_path_where_it_can_be_told(tmp_path):
    url = old_store(tmp_path)
    upgrade(url)
    downgrade(url, "0016")
    engine = create_engine(url)
    with engine.connect() as connection:
        paths = connection.execute(
            text("SELECT session_id, source_file FROM events ORDER BY session_id, sequence")
        ).all()
        assert [tuple(row) for row in paths] == [
            (1, "/files/one.jsonl"),
            (1, "/files/one.jsonl"),
            (2, None),
            (2, None),
            (2, None),
        ]
        assert not inspect(engine).has_table("session_files")
    engine.dispose()
