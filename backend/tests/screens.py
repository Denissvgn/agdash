"""A synthetic store that fills every figure screen, and a way to serve it with the built UI.

Every name, folder and number here is made up. The store holds:

- two months of ended sessions in four harnesses, with prompts, tool calls and their
  results, and usage (Antigravity reports none, some Codex sessions none), for History
  and Analytics;
- a live board of nested sessions with recent events and state changes;
- one long session across a night, with every kind of event, linked to a WorkChord run,
  for a session's page;
- a WorkChord connection with two projects, their tasks, agents, assignments and runs,
  and folder rules, for Projects;
- files read for three of the four harnesses, a format that changed last week, and
  archived lines, for Settings. Served, the harness folders are looked for in a made-up
  home beside the store, where Pi has none, and a keeper runs as in the service, so a
  limit saved on the Data page can be applied there: it removes from this made-up store
  only. Set ``AGDASH_RETENTION_DAYS`` to start with a limit in effect.

Run from ``backend/`` to serve it on loopback with the built UI::

    ../.venv/bin/python tests/screens.py --port 8790 --ui ../frontend/dist

and the acceptance run in ``frontend/scripts/screens.mjs`` drives each screen. It
prints the long session's id, which the acceptance run reads from ``/api/sessions``.
"""

from __future__ import annotations

import argparse
import random
import tempfile
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from agdash.connectors.resolver import Resolver
from agdash.db.migrate import upgrade
from agdash.db.models import (
    Connection,
    Event,
    IngestCursor,
    MappingRule,
    RawChunk,
    RunLink,
    Session,
    SessionStateChange,
    SourceHealth,
    WcActor,
    WcAssignment,
    WcProject,
    WcRun,
    WcTask,
)
from agdash.ingest.compressed import Compressor
from agdash.vocab import (
    EventKind,
    ParentBasis,
    RuleMatchKind,
    RuleTargetKind,
    RunLinkBasis,
    SessionState,
    StateBasis,
    ThoughtsCoverage,
)

TOKEN = "screens-token"
LONG_TITLE = "Migrate the billing reports"
HARNESSES = {
    # models, sessions a weekday, share of sessions that report usage
    "claude_code": (["model-opus", "model-sonnet"], 2.0, 1.0),
    "codex": (["model-gpt", "model-mini"], 1.5, 0.6),
    "pi": (["model-local"], 0.8, 1.0),
    "antigravity": (["model-gemini"], 0.8, 0.0),
}
TOOLS = ["read_file", "read_file", "edit_file", "bash", "bash", "grep", "write_file", "web_fetch"]
ERROR_RATE = {"bash": 0.18, "edit_file": 0.08, "web_fetch": 0.25}
FOLDERS = ["/work/example/orchard", "/work/example/meadow", "/work/example/harbour"]
# Files read per harness, and where the made-up home keeps them. Pi has none.
FILES_READ = {"claude_code": 420, "codex": 96, "antigravity": 31}
HOME_FOLDERS = (".claude", ".codex", ".gemini/antigravity")


def build(
    url: str, now: datetime | None = None, *, days: int = 60, crowd: bool = False
) -> dict[str, int]:
    """Create the schema at ``url`` and fill it; return the ids the screens open.

    ``crowd`` adds a busy day to the live board: a dozen more live sessions, some left
    open for days, and some that just ended.
    """
    now = now or datetime.now(UTC)
    upgrade(url)
    engine = create_engine(url)
    sequence = count()
    try:
        with sessionmaker(engine)() as db:
            _history(db, now, days, sequence)
            helper = _live(db, now, sequence, crowd)
            long = _long_session(db, now, sequence)
            connection = _workchord(db, now, long.id, helper.id)
            _sources(db, now)
            db.flush()
            Resolver().run(db, [connection])
            db.commit()
            return {"long_session": long.id}
    finally:
        engine.dispose()


def _event(
    db: DbSession,
    session_id: int,
    sequence: count,
    when: datetime,
    kind: EventKind,
    payload: dict,
    **extra,
) -> None:
    db.add(
        Event(
            session_id=session_id,
            sequence=next(sequence),
            occurred_at=when,
            kind=kind,
            payload=payload,
            **extra,
        )
    )


def _history(db: DbSession, now: datetime, days: int, sequence: count) -> None:
    rng = random.Random(7)
    hour = now.replace(minute=0, second=0, microsecond=0)
    number = 0
    for day in range(days, 0, -1):
        weekend = (hour - timedelta(days=day)).weekday() >= 5
        for harness, (models, rate, reports) in HARNESSES.items():
            for _ in range(max(0, round(rate * (0.4 if weekend else 1) * (0.5 + rng.random())))):
                number += 1
                start = hour - timedelta(days=day, hours=rng.randint(1, 10))
                model = rng.choice(models)
                found = Session(
                    harness=harness,
                    native_id=f"past-{number}",
                    title=f"Synthetic session {number}",
                    cwd=rng.choice(FOLDERS),
                    models=[model],
                    state=SessionState.ENDED,
                    started_at=start,
                    last_activity_at=start + timedelta(minutes=rng.randint(20, 90)),
                )
                db.add(found)
                db.flush()
                reporting = rng.random() < reports
                _event(
                    db,
                    found.id,
                    sequence,
                    start,
                    EventKind.USER_PROMPT,
                    {"text": "A synthetic request."},
                )
                for step in range(rng.randint(2, 5)):
                    when = start + timedelta(minutes=6 * step + 1)
                    tool, call = rng.choice(TOOLS), f"{number}-{step}"
                    _event(
                        db,
                        found.id,
                        sequence,
                        when,
                        EventKind.ASSISTANT_TEXT,
                        {"text": "synthetic"},
                    )
                    _event(
                        db,
                        found.id,
                        sequence,
                        when,
                        EventKind.TOOL_CALL,
                        {"name": tool, "call_id": call},
                    )
                    # Pi's results are never matched to their calls, so its errors are unknown.
                    if harness != "pi":
                        failed = rng.random() < ERROR_RATE.get(tool, 0.03)
                        _event(
                            db,
                            found.id,
                            sequence,
                            when,
                            EventKind.TOOL_RESULT,
                            {"call_id": call, "is_error": failed},
                        )
                    if reporting:
                        numbers = {
                            "input_tokens": rng.randint(2_000, 40_000),
                            "output_tokens": rng.randint(200, 4_000),
                            "cache_read_tokens": rng.randint(0, 120_000),
                            "model": model,
                        }
                        if harness == "claude_code":
                            numbers["reported_cost"] = round(rng.uniform(0.01, 0.4), 4)
                        _event(
                            db,
                            found.id,
                            sequence,
                            when,
                            EventKind.USAGE,
                            {"scope": "message", **numbers},
                        )


def _live(db: DbSession, now: datetime, sequence: count, crowd: bool = False) -> Session:
    rng = random.Random(5)

    def live(title, harness, state, cwd, *, parent=None, basis=None, model="model-a"):
        found = Session(
            harness=harness,
            native_id=f"live-{title}",
            title=title,
            cwd=cwd,
            models=[model],
            state=state,
            parent_session_id=parent.id if parent else None,
            parent_basis=basis,
            started_at=now - timedelta(minutes=50),
            last_activity_at=now - timedelta(minutes=1),
            effort="high",
        )
        db.add(found)
        db.flush()
        _event(
            db,
            found.id,
            sequence,
            now - timedelta(minutes=45),
            EventKind.USER_PROMPT,
            {"text": f"Synthetic task: {title.lower()}"},
        )
        return found

    def busy(found, rate, quiet_from=0):
        for minute in range(30, quiet_from, -1):
            for _ in range(rng.randint(0, rate)):
                when = now - timedelta(minutes=minute, seconds=rng.randint(0, 59))
                _event(
                    db, found.id, sequence, when, EventKind.ASSISTANT_TEXT, {"text": "synthetic"}
                )

    def changes(found, steps):
        previous = None
        for state, minutes_ago in steps:
            db.add(
                SessionStateChange(
                    session_id=found.id,
                    state=state,
                    previous=previous,
                    basis=StateBasis.HARNESS_BUSY,
                    changed_at=now - timedelta(minutes=minutes_ago),
                )
            )
            previous = state

    lead = live("Refactor the billing module", "claude_code", SessionState.ACTIVE, FOLDERS[0])
    busy(lead, 6)
    changes(
        lead, [(SessionState.ACTIVE, 50), (SessionState.WAITING, 22), (SessionState.ACTIVE, 14)]
    )
    helper = live(
        "Review the billing tests",
        "codex",
        SessionState.ACTIVE,
        FOLDERS[0],
        parent=lead,
        basis=ParentBasis.PROCESS_TREE,
        model="model-gpt",
    )
    busy(helper, 4)
    changes(helper, [(SessionState.ACTIVE, 40), (SessionState.IDLE, 18), (SessionState.ACTIVE, 9)])
    sub = live(
        "Explore the invoice schema",
        "claude_code",
        SessionState.WAITING,
        FOLDERS[0],
        parent=lead,
        basis=ParentBasis.HARNESS_RECORD,
    )
    busy(sub, 3, quiet_from=12)
    changes(sub, [(SessionState.ACTIVE, 35), (SessionState.WAITING, 12)])
    notes = live(
        "Draft the release notes", "pi", SessionState.ACTIVE, FOLDERS[1], model="model-local"
    )
    busy(notes, 2)
    changes(
        notes, [(SessionState.ACTIVE, 26), (SessionState.UNKNOWN, 16), (SessionState.ACTIVE, 10)]
    )
    export = live(
        "Wait for the data export",
        "antigravity",
        SessionState.IDLE,
        FOLDERS[2],
        model="model-gemini",
    )
    export.last_activity_at = now - timedelta(minutes=44)
    changes(export, [(SessionState.ACTIVE, 50), (SessionState.IDLE, 44)])
    if crowd:
        _crowd(now, live, busy, changes)
    return helper


CROWD = [
    # title, harness, model, state, folder, minutes since it last wrote
    ("Migrate the settings page", "claude_code", "model-a", SessionState.ACTIVE, 1, 1),
    ("Fix the flaky export test", "codex", "model-mini", SessionState.WAITING, 0, 6),
    ("Tune the search index", "antigravity", "model-gemini", SessionState.IDLE, 1, 17),
    ("Profile the import job", "codex", "model-gpt", SessionState.IDLE, 2, 21),
    ("Write the upgrade guide", "claude_code", "model-a", SessionState.WAITING, 2, 2),
    ("Answer the review comments", "claude_code", "model-a", SessionState.UNKNOWN, 2, 9),
    ("Sort the changelog entries", "pi", "model-local", SessionState.ACTIVE, 1, 1),
    ("Rename the report columns", "codex", "model-gpt", SessionState.ACTIVE, 0, 1),
]
STARTED_BY_THE_FIRST = [
    ("Search for dead routes", SessionState.ACTIVE, 1),
    ("Check the translation keys", SessionState.IDLE, 12),
    ("List the unused styles", SessionState.WAITING, 3),
]
LEFT_OPEN = ["Compare two parsers", "Sketch the archive layout", "Read the old migration"]
JUST_ENDED = ["Summarise the test run", "Tidy the fixtures"]


def _crowd(now: datetime, live, busy, changes) -> None:
    first = None
    for title, harness, model, state, folder, quiet in CROWD:
        found = live(title, harness, state, FOLDERS[folder], model=model)
        found.last_activity_at = now - timedelta(minutes=quiet)
        if state is not SessionState.UNKNOWN:
            busy(found, 4 if state is SessionState.ACTIVE else 2, quiet_from=quiet)
            changes(found, [(SessionState.ACTIVE, 40), (state, max(quiet, 1))])
        first = first or found
    for title, state, quiet in STARTED_BY_THE_FIRST:
        found = live(
            title,
            "claude_code",
            state,
            first.cwd,
            parent=first,
            basis=ParentBasis.HARNESS_RECORD,
            model="model-b",
        )
        found.last_activity_at = now - timedelta(minutes=quiet)
        busy(found, 3, quiet_from=quiet)
        changes(found, [(SessionState.ACTIVE, 25), (state, max(quiet, 1))])
    for days, title in enumerate(LEFT_OPEN, start=2):
        found = live(title, "claude_code", SessionState.IDLE, FOLDERS[days % 3])
        found.started_at = now - timedelta(days=days, hours=3)
        found.last_activity_at = now - timedelta(days=days)
    # One that started seconds ago, which the board marks as new for its first minute.
    started = live("Start the nightly report", "claude_code", SessionState.ACTIVE, FOLDERS[1])
    started.started_at = now - timedelta(seconds=5)
    started.last_activity_at = now
    for minutes, title in enumerate(JUST_ENDED, start=4):
        found = live(title, "codex", SessionState.ENDED, FOLDERS[0], model="model-mini")
        found.last_activity_at = now - timedelta(minutes=minutes + 1)
        found.ended_at = now - timedelta(minutes=minutes)


def _long_session(db: DbSession, now: datetime, sequence: count) -> Session:
    """Two working stretches a night apart, with every kind of event in each turn."""
    start = (now - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    found = Session(
        harness="claude_code",
        native_id="long",
        title=LONG_TITLE,
        cwd=FOLDERS[0],
        models=["model-opus"],
        state=SessionState.ENDED,
        started_at=start,
        last_activity_at=start + timedelta(days=1, hours=3),
    )
    db.add(found)
    db.flush()
    child = Session(
        harness="claude_code",
        native_id="long-child",
        title="Explore the report builder",
        cwd=FOLDERS[0],
        parent_session_id=found.id,
        parent_basis=ParentBasis.HARNESS_RECORD,
        state=SessionState.ENDED,
        started_at=start + timedelta(minutes=5),
        last_activity_at=start + timedelta(minutes=40),
    )
    db.add(child)
    coverage = [
        ThoughtsCoverage.FULL,
        ThoughtsCoverage.SUMMARY,
        ThoughtsCoverage.ENCRYPTED,
        ThoughtsCoverage.ABSENT,
    ]
    for stretch in (start, start + timedelta(days=1)):
        for turn in range(30):
            when = stretch + timedelta(minutes=6 * turn)
            shown = coverage[turn % 4]
            thought = (
                {"text": "Synthetic reasoning about which columns change."}
                if shown in coverage[:2]
                else {}
            )
            _event(
                db,
                found.id,
                sequence,
                when,
                EventKind.USER_PROMPT,
                {"text": f"Synthetic prompt {turn}: move the next report."},
            )
            _event(
                db, found.id, sequence, when, EventKind.THINKING, thought, thoughts_coverage=shown
            )
            _event(
                db,
                found.id,
                sequence,
                when,
                EventKind.TOOL_CALL,
                {"name": "shell", "call_id": f"t{turn}", "input": {"command": "run report"}},
            )
            _event(
                db,
                found.id,
                sequence,
                when,
                EventKind.TOOL_RESULT,
                {
                    "name": "shell",
                    "call_id": f"t{turn}",
                    "is_error": turn % 9 == 4,
                    "output": "synthetic output",
                },
            )
            _event(
                db,
                found.id,
                sequence,
                when,
                EventKind.ASSISTANT_TEXT,
                {"text": "Synthetic reply: the report now matches."},
            )
            # Every third turn reports no usage, so the overview shows what was not reported.
            if turn % 3:
                _event(
                    db,
                    found.id,
                    sequence,
                    when,
                    EventKind.USAGE,
                    {"scope": "message", "input_tokens": 1_200 + 40 * turn, "output_tokens": 80},
                )
    db.flush()
    return found


def _workchord(db: DbSession, now: datetime, long_id: int, live_id: int) -> Connection:
    connection = Connection(kind="workchord", name="Team", base_url="http://127.0.0.1:8480")
    db.add(connection)
    db.flush()
    cid = connection.id
    db.add_all(
        [
            WcProject(
                connection_id=cid, remote_id=1, name="Orchard", health="on_track", fetched_at=now
            ),
            WcProject(connection_id=cid, remote_id=2, name="Meadow", fetched_at=now),
            WcTask(
                connection_id=cid,
                remote_id=101,
                project_remote_id=1,
                external_key="ORC-1",
                title="Prune the rows",
                status="in_progress",
                iteration_name="Sprint 7",
                claim_expires_at=now + timedelta(days=1),
                fetched_at=now,
            ),
            WcTask(
                connection_id=cid,
                remote_id=102,
                project_remote_id=1,
                external_key="ORC-2",
                title="Water the beds",
                status="in_progress",
                claim_expires_at=now + timedelta(days=1),
                fetched_at=now,
            ),
            WcActor(
                connection_id=cid,
                remote_id=31,
                name="robin",
                display_name="Robin",
                role="worker",
                fetched_at=now,
            ),
            WcActor(
                connection_id=cid,
                remote_id=32,
                name="wren",
                display_name="Wren",
                role="verifier",
                fetched_at=now,
            ),
            WcAssignment(
                connection_id=cid,
                remote_id=401,
                actor_remote_id=31,
                task_remote_id=101,
                project_remote_id=1,
                mode="execution",
                status="executing",
                fetched_at=now,
            ),
            WcAssignment(
                connection_id=cid,
                remote_id=402,
                actor_remote_id=32,
                task_remote_id=102,
                project_remote_id=1,
                mode="verification",
                status="queued",
                fetched_at=now,
            ),
            WcAssignment(
                connection_id=cid,
                remote_id=403,
                actor_remote_id=31,
                task_remote_id=102,
                project_remote_id=1,
                mode="execution",
                status="queued",
                fetched_at=now,
            ),
            WcRun(
                connection_id=cid,
                remote_id=501,
                task_remote_id=101,
                actor_remote_id=31,
                assignment_remote_id=401,
                status="completed",
                started_at=now - timedelta(days=2),
                ended_at=now - timedelta(days=1),
                fetched_at=now,
            ),
            WcRun(
                connection_id=cid,
                remote_id=502,
                task_remote_id=102,
                actor_remote_id=32,
                assignment_remote_id=402,
                status="running",
                started_at=now,
                fetched_at=now,
            ),
            MappingRule(
                connection_id=cid,
                match_kind=RuleMatchKind.FOLDER,
                match_value=FOLDERS[0],
                target_kind=RuleTargetKind.PROJECT,
                target_value="1",
            ),
            MappingRule(
                connection_id=cid,
                match_kind=RuleMatchKind.FOLDER,
                match_value=FOLDERS[1],
                target_kind=RuleTargetKind.PROJECT,
                target_value="2",
            ),
            # The long session ran ORC-1: its own run, recorded by the run's tool call.
            RunLink(
                session_id=long_id,
                connection_id=cid,
                run_remote_id=501,
                task_remote_id=101,
                assignment_remote_id=401,
                basis=RunLinkBasis.TOOL_CALL,
                confidence=1.0,
                evidence={},
            ),
            # The live review session runs Wren's verification of ORC-2.
            RunLink(
                session_id=live_id,
                connection_id=cid,
                run_remote_id=502,
                task_remote_id=102,
                assignment_remote_id=402,
                basis=RunLinkBasis.TOOL_CALL,
                confidence=1.0,
                evidence={},
            ),
        ]
    )
    return connection


def _sources(db: DbSession, now: datetime) -> None:
    """Files read, the formats they were in, and archived lines, for Settings."""
    rng = random.Random(7)
    for harness, files in FILES_READ.items():
        for number in range(files):
            size = rng.randrange(20_000, 2_400_000)
            db.add(
                IngestCursor(
                    harness=harness,
                    path=f"/home/example/{harness}/synthetic-{number}.jsonl",
                    device="1",
                    inode=f"{harness}-{number}",
                    byte_offset=size,
                    size=size,
                    updated_at=now - timedelta(minutes=rng.randrange(2, 60 * 24 * 40)),
                )
            )

    def seen(adapter, version, last_days, *, first_days, unknown=0, errors=0) -> SourceHealth:
        return SourceHealth(
            adapter=adapter,
            format_version=version,
            unknown_record_count=unknown,
            parse_error_count=errors,
            first_seen_at=now - timedelta(days=first_days),
            last_seen_at=now - timedelta(days=last_days),
        )

    db.add_all(
        [
            # A format that changed five days ago, and one whose trouble is long past.
            seen("claude_code", "2.4.0", 0, first_days=5, unknown=14, errors=2),
            seen("claude_code", "2.3.1", 6, first_days=58),
            seen("codex", "0.31", 0, first_days=40),
            seen("codex", "unknown", 70, first_days=80, unknown=3),
            seen("antigravity", "1.8", 1, first_days=30),
        ]
    )
    compressor = Compressor()
    for number in range(6):
        lines = [f'{{"synthetic": {number}, "line": {line}}}\n'.encode() for line in range(400)]
        db.add(
            RawChunk(
                harness="claude_code",
                source_file="/home/example/claude_code/synthetic-0.jsonl",
                first_offset=number * 10_000,
                end_offset=(number + 1) * 10_000,
                line_count=len(lines),
                data=compressor.frame(lines),
            )
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--ui", required=True, help="the built UI, frontend/dist")
    parser.add_argument(
        "--crowd", action="store_true", help="a busy live board: many sessions at once"
    )
    parser.add_argument(
        "--reading",
        metavar="DONE/TOTAL",
        help="answer as a service still on its start-up read, this many files in",
    )
    args = parser.parse_args()

    import threading

    import uvicorn

    from agdash.collector.run import Reading
    from agdash.main import create_app
    from agdash.platform import Host, current_host
    from agdash.retention import Keeper
    from agdash.runtime import writer_engine

    reading = None
    if args.reading:
        done, total = (int(part) for part in args.reading.split("/"))
        reading = lambda: Reading(done, total, finished=done >= total)  # noqa: E731

    folder = Path(tempfile.mkdtemp(prefix="agdash-screens-"))
    url = f"sqlite:///{folder / 'screens.db'}"
    ids = build(url, crowd=args.crowd)
    print(f"long session {ids['long_session']}", flush=True)
    # A home of its own, so the screens never show this machine's folders.
    home = folder / "home"
    for found in HOME_FOLDERS:
        (home / found).mkdir(parents=True)
    host = Host(current_host().os, home, {})
    keeper = Keeper(sessionmaker(writer_engine(url), expire_on_commit=False))
    keeper.run(threading.Event())
    app = create_app(
        url,
        token=TOKEN,
        port=args.port,
        ui_dir=args.ui,
        reading=reading,
        host=host,
        retention=keeper,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
