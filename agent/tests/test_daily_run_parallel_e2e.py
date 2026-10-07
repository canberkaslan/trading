"""daily_run.sh end to end: the real script, the real `scripts.trade` and the real
`scripts.submit_plans`, against one fake broker every process shares.

test_daily_run_parallel.py pins the orchestration with stand-ins for the two
Python halves, and test_submit_plans.py pins the halves by calling them in one
process. Neither runs them the way the box does: each council its own process
started by the script, the script deciding from their exit codes and records
what goes to the submit pass, and the submit pass's lines read back by it. Here
only the outside world is stubbed (the broker, the price feed and the council's
model calls), so a mismatch anywhere along that path changes what reaches the
broker.

The yardstick is the run at COUNCIL_PARALLELISM=1, today's sequential loop.
"""

from __future__ import annotations

import fcntl
import functools
import json
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_daily_run_alerting import AGENT, Run, run_daily
from tradingagents_us.dataflows.alpaca_broker import Account, Order, Position
from tradingagents_us.schemas import AgentDecision
from tradingagents_us.storage import TradeLogRepository, make_engine

#: Two Buys the cash covers in full, a Hold, a third Buy trimmed to what the
#: first two left, and an exit of a held name.
DECISIONS = {"AAPL": "Buy", "MSFT": "Buy", "JPM": "Hold", "NVDA": "Buy", "XOM": "Sell"}
UNIVERSE = "AAPL MSFT JPM NVDA XOM"

#: A whole run: what it printed and returned, the broker's writes (each as
#: [module, call, detail]) and the order rows, oldest first.
Outcome = tuple[Run, list[list[Any]], list[tuple[Any, ...]]]


class FileBroker:
    """One paper account in a JSON file, so every process of the run sees one book.

    Each `AlpacaClient()` the code under test opens is one of these. A BUY it
    accepts stays open (the run is after the close), so a later read of open
    orders reserves its cash, as Alpaca's would. Every write is recorded with the
    module of the process that made it.
    """

    base_url = "https://paper.invalid/v2"

    def __init__(self, state: Path, module: str) -> None:
        self._state = state
        self._module = module
        self._http = SimpleNamespace(get=self._list_open_orders)

    def __enter__(self) -> FileBroker:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def close(self) -> None:
        return None

    @contextmanager
    def _book(self) -> Iterator[dict[str, Any]]:
        with open(self._state.with_name(self._state.name + ".lock"), "a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            book = json.loads(self._state.read_text(encoding="utf-8"))
            yield book
            self._state.write_text(json.dumps(book), encoding="utf-8")

    def _write(self, book: dict[str, Any], name: str, detail: object) -> None:
        book["writes"].append([self._module, name, detail])

    @staticmethod
    def _order(o: dict[str, Any]) -> Order:
        return Order(
            id=o["id"], client_order_id=o["client_order_id"], symbol=o["symbol"],
            side=o["side"], qty=o["qty"], filled_qty=0.0, order_type=o["order_type"],
            status="accepted", submitted_at=datetime.fromisoformat(o["submitted_at"]),
            filled_avg_price=None,
        )

    # -- reads --------------------------------------------------------------------

    def account(self) -> Account:
        with self._book() as book:
            return Account(
                account_number="PA-FAKE", status="ACTIVE", cash=book["cash"],
                buying_power=book["cash"], portfolio_value=book["equity"],
                pattern_day_trader=False, trading_blocked=False, currency="USD",
                last_equity=book["equity"],
            )

    def list_positions(self) -> list[Position]:
        with self._book() as book:
            return [
                Position(symbol=s, qty=q, side="long", avg_entry_price=100.0,
                         market_value=q * 100.0, unrealized_pl=0.0, unrealized_plpc=0.0)
                for s, q in book["positions"].items()
            ]

    def _list_open_orders(self, url: str) -> SimpleNamespace:
        with self._book() as book:
            page = [
                {"symbol": o["symbol"], "side": o["side"], "qty": str(o["qty"]),
                 "filled_qty": "0", "limit_price": None, "submitted_at": o["submitted_at"]}
                for o in reversed(book["open_orders"])
            ]
        return SimpleNamespace(json=lambda: page)

    def get_order_by_client_order_id(self, client_order_id: str) -> Order | None:
        with self._book() as book:
            for o in book["open_orders"]:
                if o["client_order_id"] == client_order_id:
                    return self._order(o)
        return None

    # -- writes -------------------------------------------------------------------

    def submit_order(self, **kw: Any) -> Order:
        with self._book() as book:
            self._write(book, "submit_order", [kw["symbol"], kw["side"], kw["qty"]])
            o = {
                "id": f"ord-{len(book['open_orders']) + 1}",
                "client_order_id": str(kw["client_order_id"]), "symbol": kw["symbol"],
                "side": kw["side"], "qty": float(kw["qty"]),
                "order_type": kw.get("order_type", "market"),
                "submitted_at": datetime.now(UTC).isoformat(),
            }
            book["open_orders"].append(o)
        return self._order(o)

    def cancel_order(self, order_id: str) -> None:
        with self._book() as book:
            self._write(book, "cancel_order", order_id)

    def replace_order(self, order_id: str, **kw: Any) -> None:
        with self._book() as book:
            self._write(book, "replace_order", order_id)

    def close_position(self, symbol: str) -> None:
        with self._book() as book:
            self._write(book, "close_position", symbol)

    def close_all_positions(self, cancel_orders: bool = True) -> None:
        with self._book() as book:
            self._write(book, "close_all_positions", cancel_orders)


def _decision(ticker: str, rating: str) -> AgentDecision:
    buy = rating == "Buy"
    return AgentDecision(
        ticker=ticker, market="US", quote_currency="USD", rating=rating,
        entry_price=100.0 if buy else None, stop_loss=95.0 if buy else None,
        price_target=130.0 if buy else None, reasoning=[],
        timestamp_utc=datetime.now(UTC), decision_id=f"dec-{ticker}",
    )


def install(module: str) -> None:
    """In a hook process: the outside world of `scripts.trade` and `scripts.submit_plans`."""
    from scripts import submit_plans, trade
    from tradingagents_us.execution import executor

    broker = functools.partial(FileBroker, Path(os.environ["E2E_BROKER"]), module)
    trade.AlpacaClient = broker
    executor.AlpacaClient = broker
    ratings = json.loads(os.environ["E2E_DECISIONS"])
    trade.propagate = lambda ticker, trade_date: _decision(ticker, ratings[ticker])
    trade._load_env = lambda: None
    trade._fetch_current_price = lambda ticker: 100.0
    trade.average_dollar_volume = lambda ticker: 5e8
    trade.count_correlated = lambda ticker, book: 0
    trade.rolling_price_stats = lambda ticker, repo=None: None
    # A fixed price costs nothing to read: no pacing.
    submit_plans.POLYGON_WINDOW_S = 0.0


HOOK = """#!{python}
import sys

sys.path[:0] = [{agent!r}, {vendor!r}]
from tests.test_daily_run_parallel_e2e import install

install({module!r})
from scripts import {name}

sys.exit({name}.main(sys.argv[1:]))
"""


def _hook(root: Path, module: str) -> str:
    path = root / f"{module}.py"
    path.write_text(HOOK.format(
        python=sys.executable, agent=str(AGENT), vendor=str(AGENT / "vendor" / "tradingagents"),
        module=module, name=module.split(".", 1)[1],
    ), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _daily_run(tmp_path: Path, parallelism: str, kill_switch: str | None = None) -> Outcome:
    """One whole run against a fresh book, the kill switch as given (unarmed by default)."""
    root = tmp_path / f"parallelism-{parallelism}"
    root.mkdir()
    if kill_switch is not None:
        (root / "kill.state").write_text(kill_switch, encoding="utf-8")
    state = root / "broker.json"
    state.write_text(json.dumps({
        "cash": 25_000.0, "equity": 100_000.0, "positions": {"XOM": 20},
        "open_orders": [], "writes": [],
    }), encoding="utf-8")
    db_url = f"sqlite:///{root / 'local.db'}"
    TradeLogRepository(engine=make_engine(db_url))  # the box's file: tables exist

    run = run_daily(
        root,
        UNIVERSE=UNIVERSE, SUBMIT="1", COUNCIL_PARALLELISM=parallelism,
        FAKE_HOOK_scripts_trade=_hook(root, "scripts.trade"),
        FAKE_HOOK_scripts_submit_plans=_hook(root, "scripts.submit_plans"),
        E2E_BROKER=str(state), E2E_DECISIONS=json.dumps(DECISIONS),
        LOCAL_DATABASE_URL=db_url, KILL_SWITCH_PATH=str(root / "kill.state"),
    )
    writes = json.loads(state.read_text(encoding="utf-8"))["writes"]
    repo = TradeLogRepository(engine=make_engine(db_url))
    rows = [
        (r.ticker, r.side, r.quantity, r.risk_approved, tuple(r.rejection_reasons_json or []),
         r.broker_order_id)
        for r in repo.list_orders_since()
    ]
    return run, writes, rows


@pytest.fixture(scope="module")
def sequential(tmp_path_factory: pytest.TempPathFactory) -> Outcome:
    return _daily_run(tmp_path_factory.mktemp("e2e"), "1")


def test_today_s_run_sends_what_the_book_allows(sequential: Outcome) -> None:
    run, writes, rows = sequential
    assert run.rc == 0, run.output
    assert [w[2] for w in writes] == [
        ["AAPL", "buy", 100], ["MSFT", "buy", 100], ["NVDA", "buy", 50], ["XOM", "sell", 20],
    ]
    assert {w[0] for w in writes} == {"scripts.trade"}
    assert [r[0] for r in rows] == UNIVERSE.split()
    assert "scripts.submit_plans" not in run.modules()


@pytest.mark.parametrize("parallelism", ["2", "4"])
def test_the_two_passes_send_what_today_s_run_sends(
    tmp_path: Path, sequential: Outcome, parallelism: str
) -> None:
    expected_run, expected_writes, expected_rows = sequential
    run, writes, rows = _daily_run(tmp_path, parallelism)

    assert run.rc == expected_run.rc == 0, run.output
    # The same orders, in the same order, with the same rows behind them.
    assert [w[1:] for w in writes] == [w[1:] for w in expected_writes]
    assert rows == expected_rows
    # And every one of them from the submit pass: no council reached the broker.
    assert {w[0] for w in writes} == {"scripts.submit_plans"}
    assert f"--- councils: parallelism={parallelism}," in run.output
    assert f"--- submit pass: {UNIVERSE} ---" in run.output
    for ticker in UNIVERSE.split():
        assert f"-> {ticker} decided" in run.output
        assert f"  -> {ticker} done" in run.output
    assert "Daily run complete. 0 ticker(s) errored." in run.output


def test_a_kill_switch_flipped_before_the_submit_pass_holds_every_entry(tmp_path: Path) -> None:
    # PAUSE_NEW armed after kill_check let the run through: the councils run, and
    # the submit pass reads the switch again for each order. Entries are refused,
    # the exit still goes out.
    run, writes, rows = _daily_run(tmp_path, "4", kill_switch="PAUSE_NEW")
    assert run.rc == 0, run.output
    assert [w[1:] for w in writes] == [["submit_order", ["XOM", "sell", 20]]]
    refused = {r[0]: r[4] for r in rows if r[0] in ("AAPL", "MSFT", "NVDA")}
    assert all("kill_switch=PAUSE_NEW" in reasons for reasons in refused.values()), refused
