"""The sell path through `scripts/trade.py`, end to end.

test_risk_wiring.py opens with the lesson this file applies to the sell side:
"A test that only feeds a checker a breaching input proves the checker works,
never that anything reaches it." Every other sell-side test calls
`size_from_decision` directly, so all of them keep passing if trade.py stops
passing `held_quantity` — and a sell would then be sized at 0 and refused as
`nothing_held_to_sell`, silently, for every ticker. These drive `main()` with
the broker and Polygon stubbed and assert on what lands in the DB.

The two properties that were defects:

- A Sell with no entry price used to `return 1` before writing anything. The
  run then had zero order rows, which `actionability` classifies as `idle` and
  the inert alerter deliberately stays silent on — so a dropped exit signal was
  indistinguishable from a quiet day, and daily_run.sh logged it as a generic
  ticker failure.
- The holding has to reach the sizer, or a sell is unbounded by it.
"""

from __future__ import annotations

import dataclasses
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from tradingagents_us.dataflows.alpaca_broker import Account, Order, Position
from tradingagents_us.schemas import AgentDecision
from tradingagents_us.storage import TradeLogRepository

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from scripts import trade as trade_cli  # noqa: E402

HELD_QTY = 30
MARK = 400.0


def _account(*, last_equity: float = 100_000.0) -> Account:
    return Account(
        account_number="PA123",
        status="ACTIVE",
        cash=5_000.0,
        buying_power=5_000.0,
        portfolio_value=100_000.0,
        pattern_day_trader=False,
        trading_blocked=False,
        currency="USD",
        last_equity=last_equity,
    )


def _position() -> Position:
    return Position(
        symbol="MSFT",
        qty=float(HELD_QTY),
        side="long",
        avg_entry_price=380.0,
        market_value=HELD_QTY * MARK,
        unrealized_pl=600.0,
        unrealized_plpc=0.05,
    )


class _NoOpenOrders:
    """main() pages open orders over the raw http client, not list_orders."""

    def get(self, url: str) -> SimpleNamespace:
        return SimpleNamespace(json=lambda: [])


class _StubAlpaca:
    """Context-manager shaped like AlpacaClient for the calls main() makes."""

    base_url = "https://paper.invalid/v2"
    _http = _NoOpenOrders()

    last_equity = 100_000.0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def account(self) -> Account:
        return _account(last_equity=self.last_equity)

    def list_positions(self) -> list[Position]:
        return [_position()]

    def list_orders(self, status: str = "open", limit: int = 200) -> list:
        return []


def _sell_decision(*, entry: float | None, stop: float | None) -> AgentDecision:
    return AgentDecision(
        ticker="MSFT",
        market="US",
        quote_currency="USD",
        rating="Sell",
        entry_price=entry,
        stop_loss=stop,
        reasoning=[],
        timestamp_utc=datetime.now(UTC),
        decision_id=str(uuid.uuid4()),
    )


@pytest.fixture
def run_sell(tmp_path, monkeypatch):
    """Drive `trade.main()` for a Sell and hand back the order rows it wrote."""

    def _run(
        decision: AgentDecision,
        *,
        current_price: float | None = None,
        kill_switch: str | None = None,
        submit: bool = False,
    ):
        monkeypatch.setattr(trade_cli, "_load_env", lambda: None)
        monkeypatch.setattr(trade_cli, "AlpacaClient", _StubAlpaca)
        monkeypatch.setattr(trade_cli, "_decision_from_cached", lambda ticker: decision)
        monkeypatch.setattr(trade_cli, "_fetch_current_price", lambda ticker: current_price)
        monkeypatch.setattr(trade_cli, "average_dollar_volume", lambda ticker: 5e8)
        monkeypatch.setattr(trade_cli, "count_correlated", lambda ticker, book: 0)
        # No kill-switch file -> RUN, and never the developer's own.
        flag = tmp_path / "kill.state"
        if kill_switch is not None:
            flag.write_text(kill_switch)
        monkeypatch.setenv("KILL_SWITCH_PATH", str(flag))

        db = f"sqlite:///{tmp_path / 'trades.db'}"
        monkeypatch.setattr(
            sys, "argv",
            ["trade", "--ticker", "MSFT", "--use-cached", "--db-url", db]
            + (["--submit"] if submit else []),
        )
        rc = trade_cli.main()
        repo = TradeLogRepository(engine=create_engine(db, future=True))
        return rc, repo.list_orders_since()

    return _run


def test_a_sell_without_an_entry_price_is_written_not_dropped(run_sell) -> None:
    """Defect 3. The trader agent has no reason to quote an entry for a name it
    wants out of, and requiring one dropped the decision before any row existed."""
    rc, orders = run_sell(_sell_decision(entry=None, stop=None), current_price=MARK)

    assert rc == 0, "a sell with no entry price is a trade, not a run failure"
    assert len(orders) == 1, "the exit has to leave a row — zero rows reads as an idle run"
    assert orders[0].side == "SELL"
    assert orders[0].risk_approved, orders[0].rejection_reasons_json


def test_the_sell_is_bounded_by_the_holding_the_broker_reports(run_sell) -> None:
    """Defect 2, at the wiring. A tight stop makes the risk budget size far more
    than 30 shares; only the position bounds it, and only if trade.py passes it."""
    rc, orders = run_sell(_sell_decision(entry=MARK, stop=MARK + 1.0))

    assert rc == 0
    assert orders[0].quantity == HELD_QTY, (
        f"sold {orders[0].quantity} against {HELD_QTY} held — the surplus is a "
        "naked short, and the executor attaches a protective leg to buys only"
    )


def test_no_quote_and_nothing_held_is_a_refusal_row_not_an_abort(
    tmp_path, monkeypatch, run_sell
) -> None:
    """The last corner where trade.py could still lose a decision: no entry, no
    Polygon print, and no position to mark against. It is a refusal — which
    belongs in the order log, where the actionability report can see it."""
    monkeypatch.setattr(_StubAlpaca, "list_positions", lambda self: [])
    rc, orders = run_sell(_sell_decision(entry=None, stop=None), current_price=None)

    assert rc == 0
    assert len(orders) == 1
    assert not orders[0].risk_approved
    assert "nothing_held_to_sell" in orders[0].rejection_reasons_json


def test_a_held_name_with_no_price_anywhere_is_refused_not_sold_blind(
    monkeypatch, run_sell
) -> None:
    """Held, but nothing prices it: no entry, Polygon down, the broker's mark
    reads 0, and too few cached closes for the anomaly gate to run. The fallback
    to 0.0 above used to reach the sizer as a price, and nothing that doubts a
    price could run on it, so the whole holding was approved as a market sell
    that nothing had looked at. It must be refused, and say why."""
    zero_mark = Position(
        symbol="MSFT",
        qty=float(HELD_QTY),
        side="long",
        avg_entry_price=380.0,
        market_value=0.0,
        unrealized_pl=0.0,
        unrealized_plpc=0.0,
    )
    monkeypatch.setattr(_StubAlpaca, "list_positions", lambda self: [zero_mark])
    # The empty test DB already has no bars; said here so the repro is explicit.
    monkeypatch.setattr(trade_cli, "rolling_price_stats", lambda ticker, repo=None: None)

    rc, orders = run_sell(_sell_decision(entry=None, stop=None), current_price=None)

    assert rc == 0
    assert len(orders) == 1, "the refusal has to leave a row"
    assert not orders[0].risk_approved, (
        f"approved a {orders[0].quantity}-share market sell with no price at all"
    )
    assert "no_reference_price" in orders[0].rejection_reasons_json


def test_a_sell_is_not_priced_off_the_models_entry_when_the_market_is_silent(
    monkeypatch, run_sell
) -> None:
    """The same blind book, but the council quoted an entry (ten times the real
    price). The entry stood in for the print, so the whole holding was approved
    as a market sell that no market number had priced or checked."""
    zero_mark = dataclasses.replace(_position(), market_value=0.0)
    monkeypatch.setattr(_StubAlpaca, "list_positions", lambda self: [zero_mark])
    monkeypatch.setattr(trade_cli, "rolling_price_stats", lambda ticker, repo=None: None)

    rc, orders = run_sell(_sell_decision(entry=MARK * 10, stop=None), current_price=None)

    assert rc == 0
    assert not orders[0].risk_approved, (
        f"approved a {orders[0].quantity}-share market sell priced by the model alone"
    )
    assert "no_reference_price" in orders[0].rejection_reasons_json


def test_a_sell_is_not_stranded_by_the_models_entry_failing_the_anomaly_gate(
    monkeypatch, run_sell
) -> None:
    """A good print at the mean of the cached closes, and an entry ten times it.
    The anomaly gate judged the entry (z=720) and refused the exit: a model's
    number, not the market's, kept the position open."""
    monkeypatch.setattr(trade_cli, "rolling_price_stats", lambda ticker, repo=None: (MARK, 5.0))

    rc, orders = run_sell(_sell_decision(entry=MARK * 10, stop=None), current_price=MARK)

    assert rc == 0
    assert orders[0].risk_approved, orders[0].rejection_reasons_json


def test_a_sell_with_no_print_is_priced_off_the_brokers_mark_not_the_entry(
    monkeypatch, run_sell
) -> None:
    """Polygon down: the broker's mark is the market's number, and the anomaly
    gate judges it rather than the council's entry."""
    monkeypatch.setattr(trade_cli, "rolling_price_stats", lambda ticker, repo=None: (MARK, 5.0))

    rc, orders = run_sell(_sell_decision(entry=MARK * 10, stop=None), current_price=None)

    assert rc == 0
    assert orders[0].risk_approved, orders[0].rejection_reasons_json


def test_a_buy_still_needs_an_entry_and_a_stop(run_sell) -> None:
    """The guard trade.py drops for sells has to stay up for buys: a buy with no
    entry cannot be sized at all, and there is no holding to fall back on."""
    decision = _sell_decision(entry=None, stop=None)
    buy = decision.model_copy(update={"rating": "Buy"})
    rc, orders = run_sell(buy, current_price=MARK)

    assert rc == 1
    assert orders == []


# --------------------------------------------------------------------------
# The breaker's side, at the wiring.
#
# test_risk_sell_side.py proves the breaker lets an exit through a PAUSE_NEW or
# a drawdown when handed side="SELL". These prove trade.py gets it there: the
# real FileKillSwitchReader reading a real flag file, and the drawdown measured
# off the account's last_equity, through main() to the row in the DB.
# --------------------------------------------------------------------------


def test_a_corrupt_kill_switch_file_does_not_seal_the_exit(run_sell) -> None:
    """FileKillSwitchReader reads a garbage flag as PAUSE_NEW. That may stop an
    entry; it must not stop the sell of a position already held."""
    rc, orders = run_sell(
        _sell_decision(entry=None, stop=None), current_price=MARK, kill_switch="garbage"
    )

    assert rc == 0
    assert orders[0].side == "SELL"
    assert orders[0].risk_approved, orders[0].rejection_reasons_json


def test_a_corrupt_kill_switch_file_still_stops_a_buy(run_sell) -> None:
    buy = _sell_decision(entry=MARK, stop=MARK - 10.0).model_copy(update={"rating": "Buy"})
    rc, orders = run_sell(buy, current_price=MARK, kill_switch="garbage")

    assert rc == 0
    assert not orders[0].risk_approved
    assert "kill_switch=PAUSE_NEW" in orders[0].rejection_reasons_json


def test_flatten_all_still_stops_the_sell(run_sell) -> None:
    """The flatten path is already selling the book; a second seller double-sells."""
    rc, orders = run_sell(
        _sell_decision(entry=None, stop=None), current_price=MARK, kill_switch="FLATTEN_ALL"
    )

    assert rc == 0
    assert not orders[0].risk_approved
    assert "kill_switch=FLATTEN_ALL" in orders[0].rejection_reasons_json


def test_a_drawdown_day_does_not_strand_the_exit(run_sell, monkeypatch) -> None:
    """Down 4% on the session against a 3% halt: the exit still goes."""
    monkeypatch.setattr(_StubAlpaca, "last_equity", 104_200.0)
    rc, orders = run_sell(_sell_decision(entry=None, stop=None), current_price=MARK)

    assert rc == 0
    assert orders[0].risk_approved, orders[0].rejection_reasons_json


# --------------------------------------------------------------------------
# The holding the sell was sized off, against the holding when it is sent.
#
# trade.py reads the position before the council, and the council takes five to
# ten minutes (the mobile approve path can be hours). While a stop or take-profit
# stands, the broker refuses this sell: the leg reserves the shares. It accepts
# it only once that leg has FILLED, which is exactly when the book is already
# flat, and a margin account books the sell as a short that nothing protects.
# The drawdown, loss-streak and PAUSE_NEW halts refused this sell on the days
# stops fire; the sell side now passes them, so the executor has to look.
# --------------------------------------------------------------------------


class _BrokerAtSubmit:
    """The broker as the executor meets it, after the council has run."""

    def __init__(self, positions: list[Position]) -> None:
        self.positions = positions
        self.calls: list[tuple] = []

    def account(self) -> Account:
        self.calls.append(("account",))
        return _account(last_equity=104_200.0)

    def get_order_by_client_order_id(self, key: str) -> Order | None:
        # No earlier attempt under today's key.
        self.calls.append(("get_order_by_client_order_id", key))
        return None

    def list_positions(self) -> list[Position]:
        self.calls.append(("list_positions",))
        return list(self.positions)

    def submit_order(self, **kw) -> Order:
        self.calls.append(("submit_order", kw))
        return Order(
            id="broker-1",
            client_order_id=kw["client_order_id"],
            symbol=kw["symbol"],
            side=kw["side"],
            qty=float(kw["qty"]),
            filled_qty=0.0,
            order_type=kw["order_type"],
            status="accepted",
            submitted_at=datetime.now(UTC),
            filled_avg_price=None,
        )

    def close(self) -> None:
        return None

    @property
    def submits(self) -> list[dict]:
        return [c[1] for c in self.calls if c[0] == "submit_order"]


def _submit_sell(tmp_path, monkeypatch, run_sell, held_at_submit: list[Position]):
    """A drawdown day (down 4%), 30 MSFT held when trade.py starts, and the
    holding at submit time as given. Returns the broker and the update rows."""
    from tradingagents_us.execution import executor
    from tradingagents_us.storage.models import OrderUpdateRow

    broker = _BrokerAtSubmit(held_at_submit)
    monkeypatch.setattr(executor, "AlpacaClient", lambda *a, **k: broker)
    monkeypatch.setattr(_StubAlpaca, "last_equity", 104_200.0)
    rc, orders = run_sell(_sell_decision(entry=None, stop=None), current_price=MARK, submit=True)
    repo = TradeLogRepository(engine=create_engine(f"sqlite:///{tmp_path / 'trades.db'}"))
    with repo.session() as s:
        updates = [(u.status, u.error_message) for u in s.query(OrderUpdateRow).all()]
    return rc, orders, broker, updates


def test_a_sell_whose_holding_is_gone_by_submit_time_is_not_sent(
    tmp_path, monkeypatch, run_sell
) -> None:
    """The stop filled during the council: nothing is held when the sell goes.
    Sent anyway, a margin account books it as a 30-share short with no stop."""
    rc, orders, broker, updates = _submit_sell(tmp_path, monkeypatch, run_sell, [])

    assert rc == 0, "a refusal, not a run failure"
    assert broker.submits == [], "a sell on a flat book is a short"
    assert len(orders) == 1
    ((status, reason),) = updates
    assert status == "REJECTED"
    assert "position_changed_since_sizing" in reason


def test_a_sell_whose_holding_shrank_by_submit_time_is_not_sent(
    tmp_path, monkeypatch, run_sell
) -> None:
    """Part of the lot was sold while the council ran. Something else is acting
    on it, and a sell sized off the earlier read sells shares that are gone."""
    shrunk = dataclasses.replace(_position(), qty=20.0, market_value=20 * MARK)
    rc, _, broker, updates = _submit_sell(tmp_path, monkeypatch, run_sell, [shrunk])

    assert rc == 0
    assert broker.submits == []
    ((status, reason),) = updates
    assert status == "REJECTED"
    assert "position_changed_since_sizing" in reason and "20 held" in reason


def test_a_short_holding_at_submit_time_is_not_sold_into(
    tmp_path, monkeypatch, run_sell
) -> None:
    short = dataclasses.replace(_position(), side="short")
    rc, _, broker, updates = _submit_sell(tmp_path, monkeypatch, run_sell, [short])

    assert broker.submits == []
    ((status, reason),) = updates
    assert status == "REJECTED" and "short" in reason


def test_a_sell_whose_holding_is_unchanged_is_sent_as_sized(
    tmp_path, monkeypatch, run_sell
) -> None:
    """The re-read must not stand in the way of the exit it exists to guard."""
    rc, _, broker, updates = _submit_sell(tmp_path, monkeypatch, run_sell, [_position()])

    assert rc == 0
    ((submit),) = broker.submits
    assert (submit["symbol"], submit["side"], submit["qty"]) == ("MSFT", "sell", HELD_QTY)
    ((status, _),) = updates
    assert status == "ACCEPTED"
