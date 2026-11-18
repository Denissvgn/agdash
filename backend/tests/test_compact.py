"""Room that removed data left inside the store's file goes back to the disk."""

import logging
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from agdash import compact as compact_module
from agdash import runtime
from agdash.compact import compact, compact_if_worth_it, release, room
from agdash.config import get_settings
from agdash.db.migrate import upgrade
from agdash.db.models import Event, RawChunk, Session
from agdash.platform import Host, OperatingSystem
from agdash.runtime import prepare, writer_engine
from agdash.storelock import InsideHarness, hold, lock_file
from agdash.vocab import EventKind

KEPT, REMOVED = 40, 400


@pytest.fixture(autouse=True)
def settings_as_found():
    yield
    get_settings.cache_clear()


def filled(tmp_path: Path) -> tuple[str, Path]:
    """A store that held ten times what it holds now, so most of its file is free."""
    path = tmp_path / "agdash.db"
    url = f"sqlite:///{path}"
    upgrade(url)
    engine = writer_engine(url)
    with sessionmaker(engine).begin() as db:
        session = Session(harness="pi", native_id="synthetic", models=[])
        db.add(session)
        db.flush()
        for number in range(KEPT):
            db.add(
                Event(
                    session_id=session.id,
                    sequence=number,
                    kind=EventKind.USER_PROMPT,
                    payload={"text": f"plant the quince orchard {number}"},
                )
            )
        for number in range(REMOVED):
            db.add(
                RawChunk(
                    harness="pi",
                    source_file="/files/synthetic.jsonl",
                    first_offset=number,
                    end_offset=number + 1,
                    line_count=1,
                    data=bytes([number % 251]) * 20_000,
                )
            )
    with sessionmaker(engine).begin() as db:
        db.execute(text("DELETE FROM raw_chunks"))
    engine.dispose()
    return url, path


def look(url: str):
    engine = create_engine(url)
    with engine.connect() as connection:
        found = room(connection)
    engine.dispose()
    return found


def whole(url: str) -> None:
    """The store holds what it held, and everything that hangs on its events still works."""
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        assert connection.execute(text("SELECT count(*) FROM events")).scalar() == KEPT
        found = connection.execute(
            text("SELECT count(*) FROM event_search WHERE event_search MATCH 'quince'")
        ).scalar()
        assert found == KEPT
        triggers = connection.execute(
            text(
                "SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'events'"
            )
        ).scalar()
        assert triggers == 6
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    engine.dispose()


def test_compacting_writes_the_store_anew_without_its_free_pages(tmp_path):
    url, path = filled(tmp_path)
    # Enough kept data that SQLite cannot hold the new copy in memory and must write it;
    # it takes some of the free room, and the rest is what compacting gives back.
    engine = writer_engine(url)
    with sessionmaker(engine).begin() as db:
        db.add(
            RawChunk(
                harness="pi",
                source_file="/synthetic/kept.jsonl",
                first_offset=0,
                end_offset=1,
                line_count=1,
                data=os.urandom(4_000_000),
            )
        )
    engine.dispose()
    before = look(url)
    assert before.free_bytes > 4_000_000 and before.releases is False
    copy = tmp_path / "copy"
    copy.mkdir()
    os.utime(copy, (1, 1))
    done = compact(url, temporary=copy)
    after = look(url)
    assert copy.stat().st_mtime > 1, "the temporary copy was written where it was told to be"
    assert done is not None and (done.before_bytes, done.after_bytes) == (
        before.file_bytes,
        after.file_bytes,
    )
    assert after.file_bytes < before.file_bytes - 4_000_000
    assert after.free_bytes == 0 and after.releases is True
    assert path.stat().st_size == after.file_bytes
    assert not list(copy.iterdir()), "and nothing of it is left"
    with create_engine(url).connect() as connection:
        told = connection.exec_driver_sql("PRAGMA temp_store_directory").scalar()
    assert not told, "SQLite chooses its temporary folder by itself again"
    whole(url)


def test_a_sqlite_that_will_not_take_the_folder_is_not_compacted(tmp_path, monkeypatch):
    url, path = filled(tmp_path)
    size = path.stat().st_size
    monkeypatch.setattr(compact_module, "_place_copy", lambda connection, folder: folder is None)
    with pytest.raises(compact_module.CopyNotPlaced):
        compact(url)
    assert path.stat().st_size == size
    whole(url)


def test_it_compacts_only_when_enough_is_free_and_there_is_room(tmp_path, monkeypatch, caplog):
    url, path = filled(tmp_path)
    size = path.stat().st_size
    # By the real measure this small store is not worth it: a quarter is free, but not 256 MB.
    assert look(url).worth_compacting is False
    assert compact_if_worth_it(url) is None and path.stat().st_size == size

    monkeypatch.setattr(compact_module, "MIN_FREE_BYTES", 1_000_000)
    assert look(url).worth_compacting is True
    monkeypatch.setattr(compact_module, "_volume_free", lambda path: 1000)
    with caplog.at_level(logging.WARNING, logger="agdash.compact"):
        assert compact_if_worth_it(url) is None
    assert "lacks" in caplog.text and path.stat().st_size == size

    monkeypatch.undo()
    monkeypatch.setattr(compact_module, "MIN_FREE_BYTES", 1_000_000)
    done = compact_if_worth_it(url)
    assert done is not None and path.stat().st_size < size - 6_000_000
    assert compact_if_worth_it(url) is None, "nothing is free any more"
    whole(url)


def test_a_store_that_is_not_a_file_is_left_alone():
    assert compact("sqlite://") is None
    assert compact_if_worth_it("postgresql+psycopg://user@localhost/agdash") is None


def test_free_pages_are_released_a_few_at_a_time_once_compacted(tmp_path, monkeypatch):
    url, path = filled(tmp_path)
    engine = writer_engine(url)
    assert release(engine) == 0, "a store never compacted cannot release"
    engine.dispose()
    compact(url)
    engine = writer_engine(url)
    with sessionmaker(engine).begin() as db:
        for number in range(100):
            db.add(
                RawChunk(
                    harness="pi",
                    source_file="/files/synthetic.jsonl",
                    first_offset=number,
                    end_offset=number + 1,
                    line_count=1,
                    data=bytes([number]) * 20_000,
                )
            )
    with sessionmaker(engine).begin() as db:
        db.execute(text("DELETE FROM raw_chunks"))
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    grown = path.stat().st_size
    free = look(url).free_bytes
    assert free > 1_500_000
    monkeypatch.setattr(compact_module, "RELEASE_PAGES", 100)  # several steps
    assert release(engine) == free
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    engine.dispose()
    assert look(url).free_bytes == 0
    assert path.stat().st_size <= grown - free
    whole(url)


def test_the_service_compacts_as_it_starts_when_it_is_worth_it(tmp_path, monkeypatch):
    monkeypatch.setenv("AGDASH_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    _url, path = filled(tmp_path)
    size = path.stat().st_size
    host = Host(OperatingSystem.LINUX, tmp_path, {"AGDASH_DATA_DIR": str(tmp_path)})
    prepare(host, compact=True)
    assert path.stat().st_size == size, "not by the real measure"
    monkeypatch.setattr(compact_module, "MIN_FREE_BYTES", 1_000_000)
    prepare(host)
    assert path.stat().st_size == size, "never by a command run beside the service"
    store = prepare(host, compact=True)
    assert store.database_file == path and path.stat().st_size < size - 6_000_000
    assert path.stat().st_mode & 0o077 == 0, "still readable by its owner only"
    whole(store.url)


def test_the_command_reports_and_compacts_only_with_the_service_stopped(
    tmp_path, monkeypatch, capsys
):
    url, path = filled(tmp_path)
    monkeypatch.setenv("AGDASH_DATABASE_URL", url)
    get_settings.cache_clear()
    size = path.stat().st_size
    assert compact_module.main([]) == 0
    assert "is free inside the file" in capsys.readouterr().out and path.stat().st_size == size
    assert compact_module.main(["--apply"], answering=lambda: True) == 1
    assert "Stop it first" in capsys.readouterr().out and path.stat().st_size == size
    # A service still bringing its store up to date answers nothing, but holds the lock.
    starting = hold(lock_file(path, tmp_path))
    assert compact_module.main(["--apply"], answering=lambda: False) == 1
    assert "Stop it first" in capsys.readouterr().out and path.stat().st_size == size
    starting.release()
    assert compact_module.main(["--apply"], answering=lambda: False) == 0
    assert "Compacted from" in capsys.readouterr().out and path.stat().st_size < size
    whole(url)


def test_a_second_service_on_the_same_store_ends_at_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGDASH_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    path = tmp_path / "agdash.db"
    first = hold(lock_file(path, tmp_path))
    assert first is not None
    monkeypatch.setattr(runtime, "prepare", lambda *a, **k: pytest.fail("the store was opened"))
    assert runtime.serve() == 2
    assert "another agdash service already has this store" in capsys.readouterr().err
    assert not path.exists()
    first.release()
    again = hold(lock_file(path, tmp_path))
    assert again is not None, "the lock goes with the process that held it"
    again.release()


def test_the_lock_is_beside_a_store_that_is_a_file(tmp_path):
    assert lock_file(tmp_path / "agdash.db", tmp_path / "data") == tmp_path / "agdash.db.lock"
    assert lock_file(None, tmp_path / "data") == tmp_path / "data" / "service.lock"
    held = hold(tmp_path / "agdash.db.lock")
    assert hold(tmp_path / "agdash.db.lock") is None, "one holder at a time"
    held.release()
    held.release()  # twice is harmless
    assert (tmp_path / "agdash.db.lock").stat().st_mode & 0o077 == 0


def test_no_lock_is_made_inside_a_harness_directory(tmp_path):
    harness = tmp_path / "harness"
    harness.mkdir()
    with pytest.raises(InsideHarness):
        hold(harness / "store.db.lock", forbidden_roots=(harness,))
    assert not list(harness.iterdir())
