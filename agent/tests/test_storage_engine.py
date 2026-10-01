"""local.db opens in WAL with a 15 s busy timeout; memory and other backends do not.

The API, the daily run and the timers share one SQLite file on the box. Under
the rollback journal every one of them opened, an open read stalled a
writer's commit and pysqlite failed it after 5 s with "database is locked".
These pin the pragmas the factory sets, and prove with real connections the
two things they buy: a writer commits past an open reader, and a second
writer waits for the first instead of failing. Each concurrency test also runs
the old configuration and watches it fail, so a pass means something: the
rollback journal against a reader that never lets go (any timeout fails, so
0.2 s stands in for 5 s), and pysqlite's own 5 s against a write lock held
past it (the one slow test here; `-m "not slow"` skips it).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError

from tradingagents_us.storage import TradeLogRepository, make_engine
from tradingagents_us.storage import engine as engine_mod
from tradingagents_us.storage.engine import BUSY_TIMEOUT_MS, is_sqlite_file

#: pysqlite's own busy timeout, what every connection had before the factory.
DRIVER_DEFAULT_BUSY_MS = 5000
SYNCHRONOUS_FULL = 2


def _pragma(engine: Engine, name: str) -> object:
    with engine.connect() as c:
        return c.exec_driver_sql(f"PRAGMA {name}").scalar()


def _file_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _seed(url: str) -> None:
    with create_engine(url).begin() as c:
        c.exec_driver_sql("CREATE TABLE t (x INTEGER)")
        c.exec_driver_sql("INSERT INTO t VALUES (1)")


def _seed_in_wal(url: str) -> None:
    """Seed, then let the factory switch the file, as the first process on the box does."""
    _seed(url)
    engine = make_engine(url)
    assert _pragma(engine, "journal_mode") == "wal"
    engine.dispose()


class TestPragmas:
    def test_a_file_db_runs_in_wal_with_full_sync_and_the_timeout(self, tmp_path: Path) -> None:
        engine = make_engine(_file_url(tmp_path / "a.db"))
        assert _pragma(engine, "journal_mode") == "wal"
        assert _pragma(engine, "synchronous") == SYNCHRONOUS_FULL
        assert _pragma(engine, "busy_timeout") == BUSY_TIMEOUT_MS == 15_000
        # Never enforced against this file; turning it on is not this change's call.
        assert _pragma(engine, "foreign_keys") == 0

    def test_every_pooled_connection_gets_them(self, tmp_path: Path) -> None:
        # busy_timeout and synchronous are per connection, not stored in the file.
        engine = make_engine(_file_url(tmp_path / "a.db"))
        with engine.connect() as one, engine.connect() as two:
            for c in (one, two):
                assert c.exec_driver_sql("PRAGMA busy_timeout").scalar() == BUSY_TIMEOUT_MS
                assert c.exec_driver_sql("PRAGMA synchronous").scalar() == SYNCHRONOUS_FULL

    def test_an_existing_rollback_journal_file_is_switched_for_good(self, tmp_path: Path) -> None:
        db = tmp_path / "legacy.db"
        _seed(_file_url(db))
        assert sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone() == ("delete",)
        make_engine(_file_url(db)).connect().close()
        # Stored in the file: a process still on old code opens it in WAL too.
        assert sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone() == ("wal",)

    @pytest.mark.parametrize(
        "url",
        [
            "sqlite://",
            "sqlite:///:memory:",
            "sqlite:///file:shared_mem?mode=memory&cache=shared&uri=true",
        ],
    )
    def test_a_memory_db_is_left_alone(self, url: str) -> None:
        engine = make_engine(url)
        assert not is_sqlite_file(engine.url)
        assert _pragma(engine, "journal_mode") == "memory"
        assert _pragma(engine, "busy_timeout") == DRIVER_DEFAULT_BUSY_MS

    def test_another_backend_is_left_alone(self) -> None:
        pytest.importorskip("psycopg")
        engine = make_engine("postgresql+psycopg://u@localhost:5432/trades")
        assert not is_sqlite_file(engine.url)
        assert not event.contains(engine, "connect", engine_mod._configure_sqlite_file)
        assert not event.contains(engine, "connect", engine_mod._set_busy_timeout)

    def test_a_read_only_url_gets_the_timeout_but_no_switch(self, tmp_path: Path) -> None:
        db = tmp_path / "ro.db"
        _seed(_file_url(db))
        engine = make_engine(f"sqlite:///file:{db}?mode=ro&uri=true")
        assert _pragma(engine, "busy_timeout") == BUSY_TIMEOUT_MS
        assert _pragma(engine, "journal_mode") == "delete"

    def test_engine_arguments_pass_through(self, tmp_path: Path) -> None:
        # commentator_fetch keeps item ids out of logged tracebacks this way.
        engine = make_engine(_file_url(tmp_path / "a.db"), hide_parameters=True)
        assert engine.hide_parameters is True

    def test_the_repository_default_engine_goes_through_the_factory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", _file_url(tmp_path / "default.db"))
        repo = TradeLogRepository()
        assert _pragma(repo.engine, "journal_mode") == "wal"
        assert _pragma(repo.engine, "busy_timeout") == BUSY_TIMEOUT_MS

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("sqlite:///./local.db", True),
            ("sqlite:////opt/ai-trader/agent/local.db", True),
            ("sqlite:///file:/x/local.db?mode=ro&uri=true", True),
            ("sqlite://", False),
            ("sqlite:///:memory:", False),
            ("sqlite:///file::memory:?uri=true", False),
            ("postgresql+psycopg://u@h/db", False),
        ],
    )
    def test_is_sqlite_file(self, url: str, expected: bool) -> None:
        assert is_sqlite_file(make_url(url)) is expected


class TestTheSwitchDuringADeploy:
    def test_a_switch_that_cannot_get_the_file_carries_on_and_the_next_one_switches(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Old code mid-transaction in rollback mode blocks the switch. The new
        # process must still start, in the old mode, rather than fail its run.
        monkeypatch.setattr(engine_mod, "BUSY_TIMEOUT_MS", 200)
        db = tmp_path / "busy.db"
        _seed(_file_url(db))
        old = sqlite3.connect(db, isolation_level=None)
        old.execute("BEGIN")
        old.execute("SELECT * FROM t").fetchall()

        engine = make_engine(_file_url(db))
        with caplog.at_level(logging.WARNING, logger=engine_mod.__name__):
            assert _pragma(engine, "journal_mode") == "delete"
        assert "WAL not enabled" in caplog.text

        old.execute("COMMIT")
        engine.dispose()
        assert _pragma(engine, "journal_mode") == "wal"


class TestConcurrency:
    def _reader_holding_a_snapshot(self, db: Path) -> sqlite3.Connection:
        reader = sqlite3.connect(db, isolation_level=None)
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM t").fetchone() == (1,)
        return reader

    def test_a_writer_commits_while_a_reader_holds_a_snapshot(self, tmp_path: Path) -> None:
        db = tmp_path / "rw.db"
        url = _file_url(db)
        _seed_in_wal(url)
        reader = self._reader_holding_a_snapshot(db)

        writer = make_engine(url)
        started = time.monotonic()
        with writer.begin() as c:
            c.execute(text("INSERT INTO t VALUES (2)"))
        # Did not wait for the reader at all, let alone time out.
        assert time.monotonic() - started < 1.0
        # The reader keeps the snapshot it started with, then sees the commit.
        assert reader.execute("SELECT count(*) FROM t").fetchone() == (1,)
        reader.execute("COMMIT")
        assert reader.execute("SELECT count(*) FROM t").fetchone() == (2,)

    def test_the_old_rollback_journal_failed_that_same_commit(self, tmp_path: Path) -> None:
        db = tmp_path / "rollback.db"
        url = _file_url(db)
        _seed(url)
        reader = self._reader_holding_a_snapshot(db)
        old = create_engine(url, connect_args={"timeout": 0.2})
        with (
            pytest.raises(OperationalError, match="database is locked"),
            old.begin() as c,
        ):
            c.execute(text("INSERT INTO t VALUES (2)"))
        reader.execute("COMMIT")

    @pytest.mark.slow
    def test_a_second_writer_outlasts_the_old_five_second_limit(self, tmp_path: Path) -> None:
        # The real old configuration against the factory's, on one lock held
        # past pysqlite's 5 s: the old writer gives up with "database is
        # locked", the factory's waits it out and commits. Shorter holds
        # cannot tell them apart, since both wait 5 s or more. ~5.5 s.
        db = tmp_path / "ww.db"
        url = _file_url(db)
        _seed_in_wal(url)
        outcome: dict[str, float | OperationalError] = {}
        old_done = threading.Event()

        def write(name: str, engine: Engine, value: int) -> None:
            started = time.monotonic()
            try:
                with engine.begin() as c:
                    c.execute(text(f"INSERT INTO t VALUES ({value})"))
                outcome[name] = time.monotonic() - started
            except OperationalError as exc:
                outcome[name] = exc
            finally:
                if name == "old":
                    old_done.set()

        first = sqlite3.connect(db, isolation_level=None)  # another process's write
        first.execute("BEGIN IMMEDIATE")
        first.execute("INSERT INTO t VALUES (10)")
        writers = [
            threading.Thread(target=write, args=("old", create_engine(url), 11)),
            threading.Thread(target=write, args=("new", make_engine(url), 12)),
        ]
        for w in writers:
            w.start()
        # Released only once the old writer has given up, and a little after,
        # so the factory's writer has provably waited past the old limit.
        assert old_done.wait(DRIVER_DEFAULT_BUSY_MS / 1000 + 5)
        time.sleep(0.5)
        first.execute("COMMIT")
        first.close()
        for w in writers:
            w.join(BUSY_TIMEOUT_MS / 1000)

        old, new = outcome["old"], outcome["new"]
        assert isinstance(old, OperationalError) and "database is locked" in str(old)
        assert not isinstance(new, OperationalError), new  # the factory's writer committed
        assert new > DRIVER_DEFAULT_BUSY_MS / 1000  # after waiting past the old limit
        with make_engine(url).connect() as c:
            assert c.execute(text("SELECT x FROM t ORDER BY x")).scalars().all() == [1, 10, 12]
