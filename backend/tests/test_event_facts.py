"""Usage numbers and tool calls are kept beside their events, in step with them."""

from datetime import UTC, datetime

from sqlalchemy import create_engine, event, insert, select, text
from sqlalchemy.orm import sessionmaker

from agdash.db.migrate import upgrade
from agdash.db.models import Event, EventTool, EventUsage, Session
from agdash.vocab import EventKind

WHEN = datetime(2026, 11, 16, 12, tzinfo=UTC)


def store(tmp_path, revision: str = "head"):
    url = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(url, revision)
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return url, engine, sessionmaker(engine, expire_on_commit=False)


def fill(db) -> Session:
    """One session with two usage events, a tool call, its failed result, and a reply."""
    # Stored through the table, with these columns only: a store at an earlier revision
    # does not have the columns added to sessions since.
    row = {"harness": "codex", "native_id": "synthetic", "models": []}
    made = db.execute(insert(Session.__table__).values(**row))
    session = Session(id=made.inserted_primary_key[0], **row)
    payloads = [
        (
            EventKind.USAGE,
            {
                "scope": "message",
                "model": "model-a",
                "input_tokens": 120,
                "output_tokens": 30,
                "reported_cost": 0.25,
                "extra": {"message_id": "message-1"},
            },
        ),
        (EventKind.USAGE, {"scope": "session", "output_tokens": 7}),
        (EventKind.TOOL_CALL, {"name": "read_file", "call_id": "call-1", "input": {"path": "x"}}),
        (EventKind.TOOL_RESULT, {"call_id": "call-1", "is_error": True, "output": "synthetic"}),
        (EventKind.ASSISTANT_TEXT, {"text": "synthetic"}),
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


def facts(db):
    usage = db.scalars(select(EventUsage).order_by(EventUsage.event_id)).all()
    tools = db.scalars(select(EventTool).order_by(EventTool.event_id)).all()
    return usage, tools


def check(db, session: Session) -> None:
    events = db.scalars(select(Event).order_by(Event.sequence)).all()
    usage, tools = facts(db)
    assert [row.event_id for row in usage] == [events[0].id, events[1].id]
    first, second = usage
    assert (first.session_id, first.occurred_at, first.scope) == (session.id, WHEN, "message")
    assert (first.message_key, first.model) == ("message-1", "model-a")
    assert (first.input_tokens, first.output_tokens, first.reported_cost) == (120, 30, 0.25)
    # A number nobody reported stays unknown, never zero.
    assert first.cache_read_tokens is None and first.reasoning_tokens is None
    # With no message id, the event stands for itself.
    assert (second.scope, second.message_key) == ("session", str(events[1].id))
    assert second.input_tokens is None and second.output_tokens == 7
    assert [(row.kind, row.name, row.call_id, row.is_error) for row in tools] == [
        (EventKind.TOOL_CALL, "read_file", "call-1", None),
        (EventKind.TOOL_RESULT, None, "call-1", True),
    ]
    assert all(row.session_id == session.id and row.occurred_at == WHEN for row in tools)


def test_usage_and_tool_events_are_copied_as_they_are_stored(tmp_path):
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        check(db, fill(db))
    engine.dispose()


def test_a_changed_or_removed_event_takes_its_copy_along(tmp_path):
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        session = fill(db)
        events = db.scalars(select(Event).order_by(Event.sequence)).all()
        events[0].payload = {**events[0].payload, "output_tokens": 45, "model": "model-b"}
        events[2].payload = {"name": "edit_file", "call_id": "call-9"}
        db.delete(events[1])
        db.commit()
        usage, tools = facts(db)
        assert [(row.output_tokens, row.model) for row in usage] == [(45, "model-b")]
        assert [(row.name, row.call_id) for row in tools] == [
            ("edit_file", "call-9"),
            (None, "call-1"),
        ]
        # Removing the session removes its events, and their copies with them.
        db.delete(db.get(Session, session.id))
        db.commit()
        assert facts(db) == ([], [])
    engine.dispose()


def test_events_stored_before_the_tables_existed_are_copied_once(tmp_path):
    url, engine, sessions = store(tmp_path, "0011")
    with sessions() as db:
        session = fill(db)
    engine.dispose()
    upgrade(url)
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        check(db, session)
    engine.dispose()


def test_an_interrupted_upgrade_can_be_run_again(tmp_path):
    url, engine, sessions = store(tmp_path, "0011")
    with sessions() as db:
        session = fill(db)
    engine.dispose()
    upgrade(url)
    # As if the process had stopped before the upgrade was recorded, with half the copy made.
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = '0011'"))
        connection.execute(text("DELETE FROM event_tools"))
        connection.execute(text("DROP TRIGGER event_facts_update"))
    engine.dispose()
    upgrade(url)
    _, engine, sessions = store(tmp_path)
    with sessions() as db:
        check(db, session)
        triggers = db.scalars(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")).all()
        assert {"event_facts_insert", "event_facts_update", "event_facts_delete"} <= set(triggers)
    engine.dispose()
