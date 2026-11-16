"""The read API, over a store filled from the synthetic fixtures by the real adapters."""

import hashlib
import sqlite3
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, insert, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from agdash.access import ReadOnlyAccess
from agdash.adapters import antigravity, claude_code, codex, pi
from agdash.api.routes import match_expression, router
from agdash.api.workchord import WRITE_ROUTES
from agdash.db.migrate import upgrade
from agdash.db.models import Event, Session
from agdash.ingest import Ingestor, discover_files
from agdash.ingest.ledger import SqlLedger
from agdash.main import create_app
from agdash.vocab import EventKind

FIXTURES = Path(__file__).parent / "fixtures" / "harness"
TOKEN = "test-token-for-the-api"


def local_client(url: str) -> TestClient:
    """A client as agdash's own app is: on loopback, with the local token."""
    return TestClient(
        create_app(url, token=TOKEN, port=8765),
        base_url="http://127.0.0.1:8765",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


DATA_METHODS = {"GET", "HEAD"}


def fill(url: str, snapshots: Path) -> None:
    """Ingest every fixture harness with its own adapter, as the collector will."""
    engine = create_engine(url)
    store = sessionmaker(engine, expire_on_commit=False)
    for module, root in (
        (claude_code, FIXTURES / "claude"),
        (codex, FIXTURES / "codex"),
        (pi, FIXTURES / "pi" / "agent"),
    ):
        access = ReadOnlyAccess([root], snapshot_dir=snapshots)
        Ingestor(access, SqlLedger(store, module.Writer()), harness=module.HARNESS).scan(
            discover_files(access, [root], module.PATTERNS)
        )
    bases = {
        FIXTURES / "antigravity" / "app": antigravity.SURFACES["antigravity"],
        FIXTURES / "antigravity" / "cli": antigravity.SURFACES["antigravity-cli"],
    }
    access = ReadOnlyAccess(list(bases), snapshot_dir=snapshots)
    Ingestor(access, SqlLedger(store, antigravity.Writer()), harness=antigravity.HARNESS).scan(
        discover_files(access, list(bases), antigravity.PATTERNS)
    )
    with store() as db:
        for base, surface in bases.items():
            antigravity.store_summaries(db, antigravity.read_summaries(access, base), surface)
        db.commit()
    engine.dispose()


@pytest.fixture(scope="module")
def filled(tmp_path_factory) -> str:
    folder = tmp_path_factory.mktemp("api")
    (folder / "snapshots").mkdir()
    url = f"sqlite:///{folder / 'agdash.db'}"
    upgrade(url)
    fill(url, folder / "snapshots")
    return url


@pytest.fixture(scope="module")
def client(filled):
    with local_client(filled) as test_client:
        yield test_client


def every(client, path: str, **params) -> list[dict]:
    """All items of a paged list, following cursors."""
    items, cursor = [], None
    while True:
        response = client.get(path, params={**params, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        body = response.json()
        items += body["items"]
        cursor = body["next_cursor"]
        if not cursor:
            return items


def direct(url: str):
    engine = create_engine(url)
    return engine, sessionmaker(engine)


# -- read only ----------------------------------------------------------------------


def test_only_get_is_routed_for_data(client):
    assert len(router.routes) == 7
    for route in router.routes:
        assert isinstance(route, APIRoute) and route.methods <= DATA_METHODS, route.path
    session_id = client.get("/api/sessions").json()["items"][0]["id"]
    for path in (
        "/api/sessions",
        f"/api/sessions/{session_id}",
        f"/api/sessions/{session_id}/events",
        "/api/search",
        "/api/search/sessions",
        "/api/sessions/facets",
        "/api/stats",
    ):
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)(path).status_code == 405, (method, path)


def test_the_api_connection_refuses_to_write(client):
    engine = client.app.state.store.engine
    with engine.connect() as connection, pytest.raises(OperationalError, match="readonly"):
        connection.execute(text("UPDATE sessions SET title = 'changed'"))


def test_reading_every_endpoint_leaves_the_store_unchanged(filled):
    path = Path(filled.removeprefix("sqlite:///"))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with local_client(filled) as fresh:
        session_id = fresh.get("/api/sessions").json()["items"][0]["id"]
        for url in (
            "/api/sessions",
            f"/api/sessions/{session_id}",
            f"/api/sessions/{session_id}/events",
            "/api/search?q=work",
            "/api/stats",
        ):
            assert fresh.get(url).status_code == 200, url
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_the_openapi_document_describes_every_endpoint(client):
    document = client.get("/api/openapi.json").json()
    assert set(document["paths"]) == {
        "/health",
        "/api/stream",
        "/api/sessions/{session_id}/export",
        "/api/sessions",
        "/api/sessions/{session_id}",
        "/api/sessions/{session_id}/events",
        "/api/search",
        "/api/search/sessions",
        "/api/sessions/facets",
        "/api/stats",
        "/api/analytics/usage",
        "/api/analytics/tools",
        "/api/analytics/series",
        "/api/analytics/activity",
        "/api/sessions/{session_id}/overview",
        "/api/sessions/{session_id}/states",
        "/api/live/activity",
        "/api/sessions/{session_id}/mapping",
        "/api/mapping/unmapped",
        "/api/workchord/connections",
        "/api/workchord/pairings",
        "/api/workchord/pairings/{pairing_id}/finish",
        "/api/workchord/connections/{connection_id}/projects",
        "/api/workchord/connections/{connection_id}/rules",
        "/api/workchord/connections/{connection_id}/pins",
        "/api/workchord/rules/{rule_id}",
        "/api/projects/board",
    }
    # Only the WorkChord settings routes change anything; every other route reads.
    writes = {
        (method.upper(), path.removeprefix("/api"))
        for path, operations in document["paths"].items()
        for method in operations
        if method != "get"
    }
    assert writes == WRITE_ROUTES
    for schema in (
        "SessionSummary",
        "SessionDetail",
        "EventOut",
        "SearchHit",
        "Stats",
        "SeriesReport",
        "ActivityReport",
        "Overview",
        "StateHistory",
        "LiveActivity",
    ):
        assert schema in document["components"]["schemas"]


# -- sessions -----------------------------------------------------------------------


def test_sessions_page_through_every_session_most_recent_first(client, filled):
    engine, store = direct(filled)
    with store() as db:
        total = db.scalar(select(func.count()).select_from(Session))
    engine.dispose()
    sessions = every(client, "/api/sessions", limit=7)
    assert len(sessions) == total and len({s["id"] for s in sessions}) == total
    moments = [s["last_activity_at"] for s in sessions if s["last_activity_at"]]
    assert moments == sorted(moments, reverse=True)
    # A session known only from another's reference has no activity and comes last.
    silent = [i for i, s in enumerate(sessions) if not (s["last_activity_at"] or s["started_at"])]
    assert silent and silent == list(range(len(sessions) - len(silent), len(sessions)))


def test_sessions_filter_by_harness_family_and_text(client):
    pi_sessions = every(client, "/api/sessions", harness="pi")
    assert pi_sessions and {s["harness"] for s in pi_sessions} == {"pi"}
    roots = every(client, "/api/sessions", roots_only=True)
    assert all(s["parent_session_id"] is None for s in roots)
    parent = next(s for s in roots if s["child_count"])
    children = every(client, "/api/sessions", parent=parent["id"])
    assert len(children) == parent["child_count"]
    word = pi_sessions[0]["cwd"].rsplit("/", 1)[-1]
    assert any(s["id"] == pi_sessions[0]["id"] for s in every(client, "/api/sessions", q=word))


def test_a_session_in_detail(client, filled):
    claude = every(client, "/api/sessions", harness="claude_code", roots_only=True)[0]
    detail = client.get(f"/api/sessions/{claude['id']}").json()
    assert detail["event_count"] == sum(detail["events_by_kind"].values())
    assert detail["children"] == sorted(detail["children"])
    engine, store = direct(filled)
    with store() as db:
        usages = db.scalars(
            select(Event.payload).where(
                Event.session_id == claude["id"], Event.kind == EventKind.USAGE
            )
        ).all()
    engine.dispose()
    by_message = {u.get("extra", {}).get("message_id") or id(u): u for u in usages}
    assert detail["usage"]["messages"] == len(by_message), "repeated usage counted once"
    assert detail["usage"]["output_tokens"] == sum(
        u.get("output_tokens", 0) for u in by_message.values()
    )
    assert client.get("/api/sessions/999999").status_code == 404


# -- events -------------------------------------------------------------------------


def test_events_page_in_order_both_ways(client):
    session = every(client, "/api/sessions", harness="pi")[0]
    path = f"/api/sessions/{session['id']}/events"
    forward = every(client, path, limit=50)
    assert len(forward) == session["event_count"]
    sequences = [e["sequence"] for e in forward]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    backward = every(client, path, limit=37, order="desc")
    assert [e["sequence"] for e in backward] == sequences[::-1]
    thinking = every(client, path, kind="thinking")
    assert thinking and {e["kind"] for e in thinking} == {"thinking"}


def test_bad_cursors_and_unknown_sessions_are_refused(client):
    session = every(client, "/api/sessions", harness="pi")[0]
    path = f"/api/sessions/{session['id']}/events"
    assert client.get(path, params={"cursor": "not-a-cursor"}).status_code == 400
    assert client.get("/api/sessions", params={"cursor": "e30"}).status_code == 400
    assert client.get("/api/sessions/999999/events").status_code == 404
    assert client.get(path, params={"limit": 501}).status_code == 422


# -- search -------------------------------------------------------------------------


def a_prompt_word(filled) -> tuple[str, int]:
    engine, store = direct(filled)
    with store() as db:
        event_row = db.execute(
            select(Event.payload, Event.session_id)
            .where(Event.kind == EventKind.USER_PROMPT)
            .order_by(Event.id)
            .limit(1)
        ).one()
    engine.dispose()
    word = max(event_row.payload["text"].split(), key=len).strip(".,:;!?\"'()[]")
    return word, event_row.session_id


def test_search_finds_text_and_marks_the_match(client, filled):
    word, session_id = a_prompt_word(filled)
    hits = every(client, "/api/search", q=word, limit=5)
    assert any(hit["session_id"] == session_id for hit in hits)
    for hit in hits:
        matched = "".join(part["text"] for part in hit["snippet"] if part["match"])
        assert word.lower() in matched.lower()
    assert len({h["event_id"] for h in hits}) == len(hits), "pages do not overlap"
    prefix = every(client, "/api/search", q=word[:4] + "*", limit=50)
    assert len(prefix) >= len(hits)


def test_search_filters_and_odd_queries(client, filled):
    word, session_id = a_prompt_word(filled)
    scoped = every(client, "/api/search", q=word, session_id=session_id)
    assert scoped and {h["session_id"] for h in scoped} == {session_id}
    assert every(client, "/api/search", q=word, harness="no-such-harness") == []
    assert client.get("/api/search", params={"q": '"unbalanced'}).status_code == 200
    assert client.get("/api/search", params={"q": "***"}).status_code == 422
    assert client.get("/api/search").status_code == 422


def test_without_the_index_search_falls_back_to_a_substring_match(filled, tmp_path):
    copy = tmp_path / "copy.db"
    copy.write_bytes(Path(filled.removeprefix("sqlite:///")).read_bytes())
    with sqlite3.connect(copy) as db:
        for trigger in ("event_search_insert", "event_search_delete", "event_search_update"):
            db.execute(f"DROP TRIGGER {trigger}")
        db.execute("DROP TABLE event_search")
    db.close()
    word, session_id = a_prompt_word(filled)
    with local_client(f"sqlite:///{copy}") as fallback:
        hits = every(fallback, "/api/search", q=word)
    assert any(h["session_id"] == session_id for h in hits)
    assert all(
        any(part["match"] for part in h["snippet"]) for h in hits if h["kind"] == "user_prompt"
    )


def test_match_expressions_quote_every_word():
    assert match_expression("fix  bug") == '"fix" "bug"'
    assert match_expression('say "hi"') == '"say" """hi"""'
    assert match_expression("pars*") == '"pars"*'
    assert match_expression("* **") == ""


# -- stats --------------------------------------------------------------------------


def test_stats_count_sessions_events_and_usage(client, filled):
    stats = client.get("/api/stats").json()
    engine, store = direct(filled)
    with store() as db:
        assert stats["sessions"] == db.scalar(select(func.count()).select_from(Session))
        assert stats["events"] == db.scalar(select(func.count()).select_from(Event))
    engine.dispose()
    assert set(stats["sessions_by_harness"]) == {"claude_code", "codex", "pi", "antigravity"}
    assert sum(stats["sessions_by_state"].values()) == stats["sessions"]
    harnesses = {group["harness"] for group in stats["usage"]}
    assert {"claude_code", "codex", "pi", "antigravity"} <= harnesses
    assert all(group["messages"] > 0 for group in stats["usage"])
    assert any(
        group["reported_cost"] is not None for group in stats["usage"] if group["harness"] == "pi"
    )
    assert {row["adapter"] for row in stats["source_health"]} >= {"codex", "pi"}


def test_stats_follow_the_time_window(client):
    future = (datetime.now(UTC) + timedelta(days=3650)).isoformat()
    empty = client.get("/api/stats", params={"since": future}).json()
    assert empty["sessions"] == 0 and empty["events"] == 0 and empty["usage"] == []


# -- a long session -----------------------------------------------------------------

LONG = 50_000


@pytest.fixture(scope="module")
def long_session(tmp_path_factory) -> tuple[str, int]:
    folder = tmp_path_factory.mktemp("long")
    url = f"sqlite:///{folder / 'agdash.db'}"
    upgrade(url)
    engine = create_engine(url)
    with sessionmaker(engine)() as db:
        owner = Session(harness="pi", native_id="long-session")
        db.add(owner)
        db.flush()
        start = datetime(2024, 12, 1, tzinfo=UTC)
        for chunk in range(0, LONG, 5_000):
            db.execute(
                insert(Event),
                [
                    {
                        "session_id": owner.id,
                        "sequence": n * 32,
                        "occurred_at": start + timedelta(seconds=n),
                        "kind": EventKind.TOOL_RESULT,
                        "payload": {"output": f"line {n} " + "x" * 200, "call_id": f"c{n}"},
                    }
                    for n in range(chunk, chunk + 5_000)
                ],
            )
        db.commit()
        session_id = owner.id
    engine.dispose()
    return url, session_id


def test_paging_a_50000_event_session_reads_one_page_at_a_time(long_session):
    url, session_id = long_session
    with local_client(url) as long_client:
        statements: list[tuple[str, dict]] = []

        @event.listens_for(long_client.app.state.store.engine, "before_cursor_execute")
        def record(_conn, _cursor, statement, parameters, _context, _many):
            statements.append((statement, parameters))

        path = f"/api/sessions/{session_id}/events"
        long_client.get(path, params={"limit": 500})  # warm up
        statements.clear()
        seen, cursor, peaks = 0, None, []
        last = -1
        while True:
            tracemalloc.start()
            body = long_client.get(
                path, params={"limit": 500, **({"cursor": cursor} if cursor else {})}
            ).json()
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
            assert len(body["items"]) <= 500
            assert body["items"][0]["sequence"] > last
            last = body["items"][-1]["sequence"]
            seen += len(body["items"])
            cursor = body["next_cursor"]
            if not cursor:
                break
    assert seen == LONG
    event_queries = [(s, p) for s, p in statements if "FROM events" in s]
    assert len(event_queries) == LONG // 500
    for statement, parameters in event_queries:
        assert "LIMIT" in statement, statement
        assert 501 in tuple(parameters.values() if isinstance(parameters, dict) else parameters)
    # One page of 500 events, not 50,000: well under what the whole session would take.
    assert max(peaks) < 12 * 1024 * 1024, max(peaks)


# -- filters, facets and session search ---------------------------------------------


def test_facets_describe_every_session(client, filled):
    facets = client.get("/api/sessions/facets").json()
    engine, store = direct(filled)
    with store() as db:
        total = db.scalar(select(func.count()).select_from(Session))
        models = {m for (names,) in db.execute(select(Session.models)) for m in names or []}
    engine.dispose()
    assert sum(f["count"] for f in facets["harnesses"]) == total
    assert {f["value"] for f in facets["models"]} == models
    assert facets["folders"] and all(f["value"].startswith("/work/") for f in facets["folders"])
    counts = [f["count"] for f in facets["folders"]]
    assert counts == sorted(counts, reverse=True)
    assert facets["first_started_at"] <= facets["last_started_at"]


def test_filters_combine(client):
    facets = client.get("/api/sessions/facets").json()
    model = facets["models"][0]["value"]
    with_model = every(client, "/api/sessions", model=model)
    assert with_model and all(model in s["models"] for s in with_model)
    folder = facets["folders"][0]["value"]
    parent = folder.rsplit("/", 1)[0]
    in_folder = every(client, "/api/sessions", folder=folder)
    assert in_folder and {s["cwd"] for s in in_folder} == {folder}
    under_parent = every(client, "/api/sessions", folder=parent)
    assert {s["id"] for s in in_folder} <= {s["id"] for s in under_parent}
    harness = in_folder[0]["harness"]
    both = every(client, "/api/sessions", folder=folder, harness=harness)
    assert {s["id"] for s in both} == {s["id"] for s in in_folder if s["harness"] == harness}
    started = sorted(s["started_at"] for s in every(client, "/api/sessions") if s["started_at"])
    middle = started[len(started) // 2]
    later = every(client, "/api/sessions", started_from=middle)
    earlier = every(client, "/api/sessions", started_before=middle)
    assert all(s["started_at"] >= middle for s in later)
    assert all(s["started_at"] < middle for s in earlier)
    assert len(later) + len(earlier) == len(started)
    assert every(client, "/api/sessions", model="no-such-model") == []


def test_session_search_returns_sessions_with_highlighted_excerpts(client, filled):
    word, session_id = a_prompt_word(filled)
    found = every(client, "/api/search/sessions", q=word, limit=3)
    assert any(s["id"] == session_id for s in found)
    assert len({s["id"] for s in found}) == len(found), "a session appears once"
    for session in found:
        assert session["hits"] >= len(session["excerpts"]) >= 1
        assert len(session["excerpts"]) <= 2
        for excerpt in session["excerpts"]:
            marked = "".join(p["text"] for p in excerpt["snippet"] if p["match"])
            assert word.lower() in marked.lower()


def test_session_search_honours_the_filters(client, filled):
    word, session_id = a_prompt_word(filled)
    harness = client.get(f"/api/sessions/{session_id}").json()["harness"]
    scoped = every(client, "/api/search/sessions", q=word, harness=harness)
    assert scoped and {s["harness"] for s in scoped} == {harness}
    other = [
        h["value"]
        for h in client.get("/api/sessions/facets").json()["harnesses"]
        if h["value"] != harness
    ]
    elsewhere = every(client, "/api/search/sessions", q=word, harness=other)
    assert session_id not in {s["id"] for s in elsewhere}
    assert client.get("/api/search/sessions", params={"q": "***"}).status_code == 422


def test_session_search_without_the_index(filled, tmp_path):
    copy = tmp_path / "copy.db"
    copy.write_bytes(Path(filled.removeprefix("sqlite:///")).read_bytes())
    with sqlite3.connect(copy) as db:
        for trigger in ("event_search_insert", "event_search_delete", "event_search_update"):
            db.execute(f"DROP TRIGGER {trigger}")
        db.execute("DROP TABLE event_search")
    db.close()
    word, session_id = a_prompt_word(filled)
    with local_client(f"sqlite:///{copy}") as fallback:
        found = every(fallback, "/api/search/sessions", q=word)
    assert any(s["id"] == session_id for s in found)


# -- analytics ----------------------------------------------------------------------

METRICS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "reported_cost",
)


def summed(groups, metric):
    values = [g[metric] for g in groups if g[metric] is not None]
    return round(sum(values), 6) if values else None


@pytest.mark.parametrize("group", ["harness", "model", "folder"])
def test_analytics_totals_match_the_stats_endpoint_for_the_same_filters(client, group):
    facets = client.get("/api/sessions/facets").json()
    filter_sets = [
        {},
        {"harness": "codex"},
        {"harness": ["claude_code", "pi"]},
        {"model": facets["models"][0]["value"]},
        {"folder": facets["folders"][0]["value"]},
    ]
    for filters in filter_sets:
        stats = client.get("/api/stats", params=filters).json()
        report = client.get("/api/analytics/usage", params={**filters, "group": group}).json()
        total = report["total"]
        assert total["sessions"] == stats["sessions"], filters
        assert total["messages"] == sum(g["messages"] for g in stats["usage"]), filters
        for metric in METRICS:
            mine = None if total[metric] is None else round(total[metric], 6)
            assert mine == summed(stats["usage"], metric), (filters, metric)
            assert summed(report["rows"], metric) == mine, (filters, group, metric)
        if group != "model":
            assert sum(r["sessions"] for r in report["rows"]) == total["sessions"]


def test_analytics_by_model_leave_duration_unknown(client):
    report = client.get("/api/analytics/usage", params={"group": "model"}).json()
    assert report["rows"] and all(r["duration_seconds"] is None for r in report["rows"])
    assert report["total"]["duration_seconds"] is not None


def test_tool_mix_counts_every_call(client, filled):
    report = client.get("/api/analytics/tools").json()
    engine, store = direct(filled)
    with store() as db:
        calls = db.scalar(
            select(func.count()).select_from(Event).where(Event.kind == EventKind.TOOL_CALL)
        )
    engine.dispose()
    assert report["total_calls"] == calls
    assert sum(r["calls"] for r in report["rows"]) == calls
    assert 0 < report["outcomes_known"] <= calls
    assert all(r["errors"] <= r["outcomes_known"] <= r["calls"] for r in report["rows"])
    counts = [r["calls"] for r in report["rows"]]
    assert counts == sorted(counts, reverse=True)
    pi = client.get("/api/analytics/tools", params={"harness": "pi"}).json()
    assert 0 < pi["total_calls"] < calls


@pytest.fixture
def synthetic(tmp_path):
    """Two sessions of one harness: one reports usage without cache writes, one reports none."""
    url = f"sqlite:///{tmp_path / 'synthetic.db'}"
    upgrade(url)
    engine = create_engine(url)
    start = datetime(2024, 12, 1, 9, tzinfo=UTC)
    with sessionmaker(engine)() as db:
        talking = Session(
            harness="pi",
            native_id="talking",
            started_at=start,
            last_activity_at=start + timedelta(minutes=30),
            models=["m"],
        )
        silent = Session(harness="pi", native_id="silent", started_at=start)
        other = Session(harness="codex", native_id="other")
        db.add_all([talking, silent, other])
        db.flush()
        db.add_all(
            [
                Event(
                    session_id=talking.id,
                    sequence=0,
                    kind=EventKind.USAGE,
                    payload={
                        "scope": "message",
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "model": "m",
                    },
                ),
                Event(
                    session_id=talking.id,
                    sequence=32,
                    kind=EventKind.TOOL_CALL,
                    payload={"name": "bash", "call_id": "c1"},
                ),
                Event(
                    session_id=talking.id,
                    sequence=64,
                    kind=EventKind.TOOL_RESULT,
                    payload={"call_id": "c1", "output": "boom", "is_error": True},
                ),
                Event(
                    session_id=talking.id,
                    sequence=96,
                    kind=EventKind.TOOL_CALL,
                    payload={"name": "bash", "call_id": "c2"},
                ),
            ]
        )
        db.commit()
    engine.dispose()
    return url


def test_unknown_usage_stays_unknown_and_partial_totals_say_so(synthetic):
    with local_client(synthetic) as client:
        report = client.get("/api/analytics/usage", params={"group": "harness"}).json()
        tools = client.get("/api/analytics/tools").json()
    rows = {row["key"]: row for row in report["rows"]}
    pi, codex = rows["pi"], rows["codex"]
    assert (pi["sessions"], pi["sessions_with_usage"]) == (2, 1), "partial, and it says so"
    assert (pi["input_tokens"], pi["output_tokens"]) == (10, 5)
    assert pi["cache_write_tokens"] is None and pi["reported_cost"] is None, "never zero"
    assert pi["duration_seconds"] == 1800 and pi["sessions_with_duration"] == 1
    assert codex["messages"] == 0 and codex["sessions_with_usage"] == 0
    assert all(codex[m] is None for m in METRICS), "nothing reported is null, not zero"
    assert codex["duration_seconds"] is None
    assert report["sessions_without_usage"] == 2
    assert report["total"]["cache_write_tokens"] is None
    (bash,) = tools["rows"]
    assert (bash["calls"], bash["outcomes_known"], bash["errors"]) == (2, 1, 1)
    assert pi["reported_by"]["input_tokens"] == 1 and pi["reported_by"]["cache_write_tokens"] == 0
    assert report["total"]["reported_by"]["reported_cost"] == 0
    assert (tools["total_sessions"], tools["truncated"]) == (1, False)


def test_partial_sums_say_how_many_sessions_they_cover(client):
    total = client.get("/api/analytics/usage").json()["total"]
    covered = total["reported_by"]
    assert set(covered) == set(METRICS)
    assert all(0 <= count <= total["sessions_with_usage"] for count in covered.values())
    # Only Pi reports a cost in the fixtures: the cost sum must not read as everyone's.
    assert covered["reported_cost"] < total["sessions_with_usage"]
