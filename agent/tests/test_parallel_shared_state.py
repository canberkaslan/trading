"""What councils running side by side share: local.db and the vendor memory log.

With COUNCIL_PARALLELISM above 1, up to four `scripts.trade --plan-dir`
processes run at once. Each writes its decision to local.db, and each council
reads and writes the vendor's one markdown memory log. These run the writers as
separate processes, as the daily run does, and count what survives.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from tradingagents_us.graph import memory_lock
from tradingagents_us.storage import TradeLogRepository, make_engine

AGENT = Path(__file__).resolve().parent.parent
VENDOR = AGENT / "vendor" / "tradingagents"

from tradingagents.agents.utils.memory import TradingMemoryLog  # noqa: E402


def _spawn(code: str, *args: str) -> subprocess.Popen[str]:
    env = {**os.environ, "PYTHONPATH": f"{AGENT}{os.pathsep}{VENDOR}"}
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(code), *args],
        env=env, cwd=AGENT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _finish(procs: list[subprocess.Popen[str]]) -> None:
    for proc in procs:
        out, _ = proc.communicate(timeout=120)
        assert proc.returncode == 0, out


# --- local.db -----------------------------------------------------------------------

SAVE_DECISIONS = """
    import sys
    from datetime import UTC, datetime
    from tradingagents_us.schemas import AgentDecision
    from tradingagents_us.storage import TradeLogRepository, make_engine

    url, worker, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
    repo = TradeLogRepository(engine=make_engine(url))
    for i in range(count):
        repo.save_decision(AgentDecision(
            ticker=f"T{worker}", market="US", quote_currency="USD", rating="Hold",
            reasoning=[], timestamp_utc=datetime.now(UTC), decision_id=f"{worker}-{i}",
        ))
"""

HOLD_WRITE_LOCK = """
    import sys, time
    from sqlalchemy import text
    from tradingagents_us.storage import make_engine

    engine = make_engine(sys.argv[1])
    with engine.begin() as conn:
        conn.execute(text("UPDATE agent_decisions SET rating = rating"))
        print("locked", flush=True)
        time.sleep(float(sys.argv[2]))
"""


def test_councils_writing_decisions_at_once_lose_none(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'local.db'}"
    TradeLogRepository(engine=make_engine(url))  # the box's file: tables exist
    # Another writer holding the lock for a while (an API write, the backup):
    # the councils wait it out within the busy timeout instead of failing.
    holder = _spawn(HOLD_WRITE_LOCK, url, "1.0")
    assert holder.stdout is not None and holder.stdout.readline().strip() == "locked"
    workers = [_spawn(SAVE_DECISIONS, url, str(w), "25") for w in range(4)]
    _finish([holder, *workers])

    rows = TradeLogRepository(engine=make_engine(url)).list_recent_decisions(limit=1000)
    assert len(rows) == 100
    assert {r.decision_id for r in rows} == {f"{w}-{i}" for w in range(4) for i in range(25)}


# --- the vendor memory log ----------------------------------------------------------

STORE = """
    import sys
    from tradingagents.agents.utils.memory import TradingMemoryLog
    from tradingagents_us.graph import memory_lock

    assert memory_lock.install()
    path, worker = sys.argv[1], sys.argv[2]
    for i in range(10):
        TradingMemoryLog({"memory_log_path": path}).store_decision(
            f"W{worker}N{i}", "2026-10-07", "Rating: Buy"
        )
"""

REWRITE = """
    import sys
    from tradingagents.agents.utils.memory import TradingMemoryLog
    from tradingagents_us.graph import memory_lock

    assert memory_lock.install()
    path = sys.argv[1]
    for _ in range(40):
        # Resolving outcomes: read the whole log, rename a rewrite over it.
        TradingMemoryLog({"memory_log_path": path}).batch_update_with_outcomes([{
            "ticker": "NONE", "trade_date": "1999-01-01", "raw_return": 0.0,
            "alpha_return": 0.0, "holding_days": 1, "reflection": "-",
        }])
"""


@pytest.fixture(autouse=True)
def _installed() -> None:
    assert memory_lock.install()


def _log(tmp_path: Path) -> TradingMemoryLog:
    return TradingMemoryLog({"memory_log_path": str(tmp_path / "memory" / "log.md")})


def test_councils_appending_and_rewriting_at_once_lose_no_entry(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.store_decision("SPY", "2026-09-01", "Rating: Hold")
    path = str(log._log_path)
    procs = [_spawn(STORE, path, str(w)) for w in range(4)]
    procs += [_spawn(REWRITE, path) for _ in range(2)]
    _finish(procs)

    stored = {e["ticker"] for e in log.load_entries()}
    assert stored == {"SPY"} | {f"W{w}N{i}" for w in range(4) for i in range(10)}


def test_a_write_waits_for_the_lock(tmp_path: Path) -> None:
    log = _log(tmp_path)
    done = threading.Event()

    def write() -> None:
        log.store_decision("AAPL", "2026-10-07", "Rating: Buy")
        done.set()

    with memory_lock.exclusive(memory_lock.lock_path_for(log._log_path)):
        t = threading.Thread(target=write)
        t.start()
        assert not done.wait(0.3), "the append went ahead while another held the log"
    t.join(5)
    assert done.is_set()
    assert "AAPL" in log._log_path.read_text(encoding="utf-8")


def test_the_lock_is_reentrant_within_a_thread(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    with memory_lock.exclusive(lock), memory_lock.exclusive(lock):
        pass


def test_install_is_idempotent() -> None:
    before = TradingMemoryLog.store_decision
    assert memory_lock.install()
    assert TradingMemoryLog.store_decision is before


# A fresh interpreter, as each `scripts.trade` council is: nothing has installed
# the lock yet, so only propagate() can have put it there by the time the graph
# (and with it every memory-log read and write) is built. The graph is a stub
# that looks and stops, so no model is called.
COUNCIL_INSTALLS = """
    from tradingagents.agents.utils.memory import TradingMemoryLog
    from tradingagents.graph import trading_graph
    from tradingagents_us.graph import memory_lock, pipeline

    def locked():
        return all(getattr(getattr(TradingMemoryLog, name), "_memory_locked", False)
                   for name in memory_lock._LOCKED_METHODS)

    class Built(Exception):
        pass

    class Graph:
        def __init__(self, *args, **kwargs):
            raise Built(locked())

    pipeline._load_env = lambda: None  # never a real agent/.env
    trading_graph.TradingAgentsGraph = Graph
    print("before", locked())
    try:
        pipeline.propagate("AAPL", "2026-10-07")
    except Built as built:
        print("at_graph", built.args[0])
"""


def test_a_council_installs_the_lock_before_its_graph_is_built() -> None:
    proc = _spawn(COUNCIL_INSTALLS)
    out, _ = proc.communicate(timeout=120)
    assert proc.returncode == 0, out
    lines = out.splitlines()
    assert "before False" in lines, out
    assert "at_graph True" in lines, out
