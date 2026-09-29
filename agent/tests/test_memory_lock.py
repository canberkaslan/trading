"""Concurrent councils share one memory log without losing entries."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from tradingagents.agents.utils.memory import TradingMemoryLog  # noqa: E402

from tradingagents_us.file_lock import LockTimeoutError, exclusive  # noqa: E402
from tradingagents_us.graph import memory_lock  # noqa: E402


@pytest.fixture(autouse=True)
def _installed() -> None:
    assert memory_lock.install()


def _log(tmp_path: Path) -> TradingMemoryLog:
    return TradingMemoryLog({"memory_log_path": str(tmp_path / "memory" / "log.md")})


def test_install_is_idempotent() -> None:
    before = TradingMemoryLog.store_decision
    assert memory_lock.install()
    assert TradingMemoryLog.store_decision is before


def test_a_write_waits_for_the_lock(tmp_path: Path) -> None:
    log = _log(tmp_path)
    lock = memory_lock.lock_path_for(log._log_path)
    done = threading.Event()

    def write() -> None:
        log.store_decision("AAPL", "2026-09-29", "Rating: Buy")
        done.set()

    with exclusive(lock):  # another council mid-rewrite
        t = threading.Thread(target=write)
        t.start()
        assert not done.wait(0.3), "the append went ahead while the log was locked"
    t.join(5)
    assert done.is_set()
    assert "AAPL" in log._log_path.read_text(encoding="utf-8")


def test_concurrent_appends_and_rewrites_lose_nothing(tmp_path: Path) -> None:
    # The vendor's outcome rewrite reads the whole log and renames a rewrite
    # over it: without the lock an append landing in between is dropped.
    log = _log(tmp_path)
    log.store_decision("SPY", "2026-09-01", "Rating: Hold")
    tickers = [f"T{i:02d}" for i in range(24)]
    errors: list[BaseException] = []

    def append(ticker: str) -> None:
        try:
            _log(tmp_path).store_decision(ticker, "2026-09-29", "Rating: Buy")
        except BaseException as exc:  # noqa: BLE001 — surfaced below
            errors.append(exc)

    def rewrite() -> None:
        for _ in range(30):
            _log(tmp_path).batch_update_with_outcomes([{
                "ticker": "NONE", "trade_date": "1999-01-01", "raw_return": 0.0,
                "alpha_return": 0.0, "holding_days": 1, "reflection": "-",
            }])

    threads = [threading.Thread(target=append, args=(t,)) for t in tickers]
    threads.append(threading.Thread(target=rewrite))
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == []
    stored = {e["ticker"] for e in log.load_entries()}
    assert stored == {"SPY", *tickers}


def test_exclusive_is_reentrant_within_a_thread(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    with exclusive(lock), exclusive(lock, timeout_s=0.1):
        pass


def test_exclusive_times_out_when_someone_else_holds_it(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    failures: list[BaseException] = []

    def contender() -> None:
        try:
            with exclusive(lock, timeout_s=0.2):
                pass
        except LockTimeoutError as exc:
            failures.append(exc)

    with exclusive(lock):
        t = threading.Thread(target=contender)
        t.start()
        t.join(5)
    assert len(failures) == 1
    assert "another process holds it" in str(failures[0])
