"""Encoded data is left out of the archive, and taken out of what was archived before."""

import base64
import json
import random
import threading
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from agdash import archive as archive_module
from agdash import saved
from agdash.archive import (
    ENCODED_RUN,
    Archiving,
    Trimmer,
    leave_out_encoded,
    trim_in_steps,
    untrimmed,
)
from agdash.config import get_settings
from agdash.db.migrate import upgrade
from agdash.db.models import RawChunk
from agdash.ingest.compressed import decompress
from agdash.ingest.engine import Cursor, Line
from agdash.main import create_app
from agdash.runtime import writer_engine

TOKEN = "archive-test-token"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
PATH = "/files/synthetic.jsonl"


def encoded(length: int, seed: int = 1) -> str:
    """Made-up encoded data: random bytes in base64, which does not compress."""
    return base64.b64encode(random.Random(seed).randbytes(length)).decode()[:length]


def line(number: int, *, blob: int = 0) -> str:
    record = {"synthetic": number, "text": "plant the quince orchard"}
    if blob:
        record["signature"] = encoded(blob, seed=number)
    return json.dumps(record)


@pytest.fixture
def url(tmp_path) -> str:
    database = f"sqlite:///{tmp_path / 'agdash.db'}"
    upgrade(database)
    return database


@pytest.fixture
def store(url):
    engine = writer_engine(url)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def settings_as_found():
    yield
    get_settings.cache_clear()


def archive(store, lines: list[str], *, first: int = 0, **options) -> None:
    """Archive ``lines`` as one batch read from the file at ``first``."""
    offset, batch = first, []
    for text in lines:
        data = text.encode()
        batch.append(Line(offset, offset + len(data) + 1, data))
        offset += len(data) + 1
    writer = Archiving(lambda db, cursor, got: None, "pi", **options)
    with store.begin() as db:
        writer(db, Cursor("pi", PATH, "1", "1", offset, offset), batch)


def stored(store) -> list[tuple[int, list[str]]]:
    with store() as db:
        return [
            (len(chunk.data), decompress_lines(chunk.data))
            for chunk in db.scalars(select(RawChunk).order_by(RawChunk.id))
        ]


def decompress_lines(data: bytes) -> list[str]:
    import io

    return decompress(io.BytesIO(data), 1 << 26).decode().split("\n")[:-1]


# -- the rule -------------------------------------------------------------------------


def test_a_long_run_of_encoded_data_gives_way_to_its_length():
    blob = encoded(ENCODED_RUN + 100)
    text = json.dumps({"image": blob, "note": "kept as it is", "short": encoded(ENCODED_RUN - 1)})
    lean = leave_out_encoded(text)
    assert json.loads(lean) == {
        "image": f"[OMITTED:{ENCODED_RUN + 100}]",
        "note": "kept as it is",
        "short": encoded(ENCODED_RUN - 1),
    }
    assert leave_out_encoded(lean) == lean, "doing it again changes nothing"
    assert leave_out_encoded("words " * 200) == "words " * 200
    assert leave_out_encoded("/a/long/path/" * 60) == "/a/long/path/" * 60, "a path is no blob"


def test_lines_are_archived_without_their_encoded_data(store):
    lines = [line(1), line(2, blob=3000), line(3)]
    archive(store, lines)
    ((size, kept),) = stored(store)
    assert kept == [lines[0], leave_out_encoded(lines[1]), lines[2]]
    assert "[OMITTED:3000]" in kept[1] and size < 600


def test_a_person_can_keep_the_encoded_data(store):
    lines = [line(1, blob=3000)]
    archive(store, lines, keep_encoded=True)
    ((size, kept),) = stored(store)
    assert kept == lines and size > 2000
    # Asked of the store each time, so a saved change holds from the next lines read.
    asked = []
    archive(
        store, [line(2, blob=3000)], first=9000, keep_encoded=lambda db: asked.append(db) or False
    )
    assert len(asked) == 1 and "[OMITTED:3000]" in stored(store)[1][1][0]


# -- the setting ----------------------------------------------------------------------


def test_encoded_data_is_left_out_unless_set_otherwise(store, monkeypatch):
    with store() as db:
        assert saved.keep_encoded(db) == (False, "default")
    monkeypatch.setenv("AGDASH_KEEP_ENCODED", "1")
    get_settings.cache_clear()
    with store() as db:
        assert saved.keep_encoded(db) == (True, "environment")


def test_saving_one_choice_about_the_archive_leaves_the_other(store):
    with store.begin() as db:
        saved.save_keep_encoded(db, True)
    with store.begin() as db:
        assert saved.keep_encoded(db) == (True, "page")
        assert saved.keep_raw(db) == (True, "default"), "not saved, so not from the page"
        saved.save_keep_raw(db, False)
    with store() as db:
        assert saved.keep_raw(db) == (False, "page")
        assert saved.keep_encoded(db) == (True, "page")


# -- trimming -------------------------------------------------------------------------


def archived_whole(store, batches: int = 5) -> list[list[str]]:
    """Batches archived while encoded data was kept; every other one holds some."""
    kept, first = [], 0
    for number in range(batches):
        lines = [line(number * 10 + 1), line(number * 10 + 2, blob=4000 if number % 2 == 0 else 0)]
        archive(store, lines, first=first, keep_encoded=True)
        kept.append(lines)
        first += 20_000
    return kept


def test_what_was_archived_before_is_trimmed_and_nothing_else_changes(store):
    batches = archived_whole(store)
    before = stored(store)
    with store() as db:
        assert untrimmed(db) == 5
    done = trim_in_steps(store, clock=lambda: NOW)
    after = stored(store)
    assert (done.chunks, done.finished) == (5, True)
    assert [lines for _, lines in after] == [
        [leave_out_encoded(text) for text in lines] for lines in batches
    ]
    for number, ((size_before, _), (size_after, _)) in enumerate(zip(before, after, strict=True)):
        if number % 2 == 0:
            assert size_after < size_before / 4
        else:
            assert size_after == size_before, "a chunk with nothing encoded is left as it is"
    assert done.saved_bytes == sum(b[0] for b in before) - sum(a[0] for a in after) > 8000
    with store() as db:
        assert untrimmed(db) == 0
        noted = saved.last_trim(db)
    assert noted == {
        "at": NOW.isoformat(),
        "through": 5,
        "chunks": 5,
        "saved_bytes": done.saved_bytes,
    }
    again = trim_in_steps(store, clock=lambda: NOW)
    assert (again.chunks, again.saved_bytes, again.finished) == (0, 0, True)


def test_the_same_lines_read_again_still_match_a_trimmed_chunk(store):
    (lines, *_rest) = archived_whole(store, batches=1)
    trim_in_steps(store)
    archive(store, lines)  # as after a restart: the same stretch of the same file
    assert len(stored(store)) == 1, "no second copy"


def test_trimming_goes_a_few_chunks_at_a_time_and_can_be_stopped(store, monkeypatch):
    archived_whole(store, batches=6)
    monkeypatch.setattr(archive_module, "TRIM_BYTES", 1)  # one chunk to a transaction
    stop = threading.Event()
    steps = []

    def clock() -> datetime:
        steps.append(1)
        if len(steps) == 2:
            stop.set()
        return NOW

    done = trim_in_steps(store, stop=stop, clock=clock, pause=0)
    assert (done.chunks, done.finished) == (2, False)
    with store() as db:
        assert untrimmed(db) == 4 and saved.last_trim(db)["through"] == 2
    # Asked again, it goes on from where it stopped.
    assert trim_in_steps(store, clock=lambda: NOW).chunks == 4
    with store() as db:
        assert saved.last_trim(db)["chunks"] == 6


# -- the API --------------------------------------------------------------------------


def client(url: str, trimmer: Trimmer | None = None) -> TestClient:
    return TestClient(
        create_app(url, token=TOKEN, port=8765, trimmer=trimmer),
        base_url="http://127.0.0.1:8765",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def test_the_choice_is_shown_and_saved(url, store):
    archived_whole(store, batches=2)
    with client(url) as api:
        found = api.get("/api/storage").json()["archive"]
        assert (found["keep_encoded"], found["keep_encoded_source"]) == (False, "default")
        assert (found["untrimmed_chunks"], found["trimming"], found["last_trim"]) == (
            2,
            False,
            None,
        )
        saved_now = api.post("/api/storage/settings", json={"keep_encoded": True}).json()
        assert saved_now["archive"]["keep_encoded"] is True
        assert saved_now["archive"]["keep_encoded_source"] == "page"
        assert saved_now["archive"]["enabled"] is True, "the other choice is as it was"
        assert api.post("/api/storage/settings", json={"keep_encoded": None}).status_code == 422
        assert api.post("/api/storage/settings", json={}).status_code == 422


def test_trimming_is_asked_for_and_refused_when_it_cannot_be_done(url, store):
    archived_whole(store, batches=2)
    with client(url) as api:
        assert api.post("/api/storage/archive/trim").status_code == 503, "nothing here trims"
    trimmer = Trimmer(store)
    with client(url, trimmer) as api:
        api.post("/api/storage/settings", json={"keep_encoded": True})
        assert api.post("/api/storage/archive/trim").status_code == 409, "kept, so nothing to do"
        assert trimmer.running() is False
        api.post("/api/storage/settings", json={"keep_encoded": False})
        answer = api.post("/api/storage/archive/trim")
        assert answer.status_code == 202 and answer.json()["archive"]["trimming"] is True


def test_the_trimmer_runs_in_the_service_when_asked_and_stops_with_it(url, store, monkeypatch):
    monkeypatch.setattr(archive_module, "LOOK_SECONDS", 0.02)
    archived_whole(store, batches=3)
    trimmer = Trimmer(store)
    stop = threading.Event()
    threads = trimmer.run(stop)
    try:
        assert [thread.name for thread in threads] == ["agdash-archive"]
        time.sleep(0.1)
        with store() as db:
            assert untrimmed(db) == 3, "nothing is trimmed unasked"
        trimmer.ask()
        deadline = time.monotonic() + 5
        while trimmer.running() and time.monotonic() < deadline:
            time.sleep(0.02)
        with store() as db:
            assert untrimmed(db) == 0
            assert db.scalar(select(func.count()).select_from(RawChunk)) == 3
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)


# -- the command ----------------------------------------------------------------------


def test_the_command_says_what_waits_and_trims_only_when_told(url, store, monkeypatch, capsys):
    from agdash import cli

    monkeypatch.setenv("AGDASH_DATABASE_URL", url)
    get_settings.cache_clear()
    archived_whole(store, batches=3)
    assert cli.main(["archive"]) == 0
    assert "3 chunks, 3 not yet looked at" in capsys.readouterr().out
    with store() as db:
        assert untrimmed(db) == 3
    assert cli.main(["archive", "--trim"]) == 0
    assert "Looked at 3 chunks" in capsys.readouterr().out
    with store.begin() as db:
        assert untrimmed(db) == 0
        saved.save_keep_encoded(db, True)
    assert cli.main(["archive", "--trim"]) == 0
    assert "nothing to take out" in capsys.readouterr().out
