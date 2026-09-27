"""scripts.manage_positions end to end, against a stub broker.

The planner has its own tests; nothing covered the runner that turns its plan
into broker calls, and `close_position` appeared in no test at all. These drive
`main()` with a real bar cache and a fake client that records every call, and
assert on what would reach the broker: nothing in a dry run, exactly the plan
with --submit, and one bad symbol never stopping the rest of the pass.

The TimeExit has two known defects, and this tranche changes neither, because
fixing either changes what reaches the broker:

  * it is a bare DELETE /positions/{symbol}, so on any position whose GTC stop
    reserves the shares (every bracket entry, and every stop this pass
    back-fills) the broker refuses it: held_for_orders=qty, available=0;
  * the DELETE carries a broker-generated client id, so even a close that goes
    through books as an operator "flatten" and drops out of the strategy's
    eval roll-up.

Each is pinned twice: a `known_bug` test that asserts today's behaviour (a
guard that this tranche did not move order flow), and a strict xfail that
states the target. The follow-up that fixes the exit must flip both.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from scripts import manage_positions as mp
from tradingagents_us.dataflows.alpaca_broker import FillActivity, Order, Position
from tradingagents_us.risk.position_manager import PlaceStop, RatchetStop, TimeExit
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.price_cache import write_bars

TODAY = datetime.now(UTC).date()
#: Entered well before every bar in the cache, so all of them count as held.
ENTRY = TODAY - timedelta(days=50)
#: A flat tape: every true range is 2.0, so the ATR is exactly 2.0.
BAR_DAYS = 45

WRITES = ("close_position", "submit_order", "replace_order", "cancel_order", "close_all_positions")


def _position(symbol: str, price: float, avg: float = 100.0, qty: float = 10.0) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        side="long",
        avg_entry_price=avg,
        market_value=qty * price,
        unrealized_pl=qty * (price - avg),
        unrealized_plpc=price / avg - 1.0,
    )


def _stop(order_id: str, symbol: str, stop_price: float, qty: float = 10.0) -> Order:
    return Order(
        id=order_id,
        client_order_id=f"coid-{order_id}",
        symbol=symbol,
        side="sell",
        qty=qty,
        filled_qty=0.0,
        order_type="stop",
        status="new",
        submitted_at=datetime.now(UTC),
        filled_avg_price=None,
        stop_price=stop_price,
    )


def _buy(symbol: str, qty: float = 10.0) -> FillActivity:
    return FillActivity(
        id=f"fill-{symbol}",
        symbol=symbol,
        side="buy",
        qty=qty,
        price=100.0,
        transaction_time=datetime.combine(ENTRY, datetime.min.time(), UTC),
        order_id=f"buy-{symbol}",
    )


class FakeAlpaca:
    """Records every call. Reads return the book it was built with."""

    def __init__(
        self,
        positions: list[Position],
        orders: list[Order],
        fills: list[FillActivity],
        refuse_close: set[str] | None = None,
    ) -> None:
        self.positions = positions
        self.orders = orders
        self.fills = fills
        self.refuse_close = refuse_close or set()
        self.calls: list[tuple] = []

    def __enter__(self) -> FakeAlpaca:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def list_positions(self) -> list[Position]:
        self.calls.append(("list_positions",))
        return self.positions

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False):
        self.calls.append(("list_orders", status))
        return self.orders

    def list_fill_activities(self) -> list[FillActivity]:
        self.calls.append(("list_fill_activities",))
        return self.fills

    def close_position(self, symbol: str) -> dict:
        self.calls.append(("close_position", symbol))
        if symbol in self.refuse_close:
            # What the live XOM close got back: every share reserved by the
            # GTC stop, nothing free to sell.
            raise RuntimeError(
                f"alpaca DELETE /positions/{symbol} failed 403: "
                '{"code":40310000,"held_for_orders":"10","available":"0"}'
            )
        return {"id": f"close-{symbol}", "client_order_id": "68c3c73e-broker-uuid"}

    def submit_order(self, **kw) -> SimpleNamespace:
        self.calls.append(("submit_order", kw))
        return SimpleNamespace(id=f"placed-{kw['symbol']}")

    def replace_order(self, order_id: str, **kw) -> SimpleNamespace:
        self.calls.append(("replace_order", order_id, kw))
        return SimpleNamespace(id=f"replaced-{order_id}")

    def cancel_order(self, order_id: str) -> dict:
        self.calls.append(("cancel_order", order_id))
        return {}

    def close_all_positions(self, cancel_orders: bool = True) -> list:
        self.calls.append(("close_all_positions", cancel_orders))
        return []

    @property
    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in WRITES]


def _book(refuse_close: set[str] | None = None) -> FakeAlpaca:
    """One position per branch of the plan.

    XOM   flat for 45 bars under a covering stop  -> TimeExit
    AAPL  up 20% with its stop at 90              -> RatchetStop 90 -> 114
    MSFT  up 33% with no stop at all              -> PlaceStop 10 @ 194
    NVDA  flat for 50 days, bar cache empty       -> no action, reported
    """
    return FakeAlpaca(
        positions=[
            _position("XOM", 100.5),
            _position("AAPL", 120.0),
            _position("MSFT", 200.0, avg=150.0),
            _position("NVDA", 100.5),
        ],
        orders=[
            _stop("stop-xom", "XOM", 90.0),
            _stop("stop-aapl", "AAPL", 90.0),
            _stop("stop-nvda", "NVDA", 90.0),
        ],
        fills=[_buy(s) for s in ("XOM", "AAPL", "MSFT", "NVDA")],
        refuse_close=refuse_close,
    )


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A bar cache holding a flat tape for every name except NVDA."""
    monkeypatch.setenv("KILL_SWITCH_FILE", str(tmp_path / "kill_switch.state"))
    url = f"sqlite:///{tmp_path / 'bars.db'}"
    repo = TradeLogRepository(engine=create_engine(url, future=True))
    flat = {"o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0}
    bars = [{"t": (TODAY - timedelta(days=d)).isoformat(), **flat} for d in range(BAR_DAYS, 0, -1)]
    with repo.session() as session:
        for symbol in ("XOM", "AAPL", "MSFT"):
            write_bars(session, symbol, bars)
    return url


def _run(monkeypatch: pytest.MonkeyPatch, fake: FakeAlpaca, *argv: str) -> int:
    monkeypatch.setattr(mp, "AlpacaClient", lambda: fake)
    return mp.main(list(argv))


EXPECTED_PLAN = [
    ("close_position", "XOM"),
    ("replace_order", "stop-aapl", {"stop_price": 114.0}),
    (
        "submit_order",
        {
            "symbol": "MSFT",
            "qty": 10.0,
            "side": "sell",
            "order_type": "stop",
            "time_in_force": "gtc",
            "stop_price": 194.0,
        },
    ),
]


class TestDryRunVersusSubmit:
    def test_a_dry_run_sends_nothing(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _book()

        rc = _run(monkeypatch, fake, "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.writes == []
        # The plan is still described in full, so a dry run is a real preview.
        assert sum("[dry run]" in r.getMessage() for r in caplog.records) == 3
        assert "dry run — pass --submit to act" in caplog.text

    def test_submit_sends_exactly_the_plan(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _book()

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.writes == EXPECTED_PLAN

    def test_without_backfill_a_naked_name_is_reported_not_protected(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _book()

        _run(monkeypatch, fake, "--submit", "--db-url", db_url)

        assert [w for w in fake.writes if w[0] == "submit_order"] == []

    def test_flatten_all_skips_the_pass_before_any_broker_call(
        self, db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "kill_switch.state").write_text("FLATTEN_ALL")
        fake = _book()

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.calls == []


class TestPerSymbolFailureIsolation:
    def test_every_action_is_attempted_and_each_failure_counted(self) -> None:
        fake = _book(refuse_close={"XOM", "NVDA"})

        failures = mp._execute(
            fake,
            [
                TimeExit("XOM", 10.0, 45, 0.005),
                RatchetStop("AAPL", "stop-aapl", 90.0, 114.0, 2.0),
                TimeExit("NVDA", 10.0, 30, 0.001),
                PlaceStop("MSFT", 10.0, 194.0, 2.0),
            ],
        )

        assert failures == 2
        assert [w[0] for w in fake.writes] == [
            "close_position",
            "replace_order",
            "close_position",
            "submit_order",
        ]

    def test_a_refused_close_is_counted_and_the_rest_of_the_pass_still_runs(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _book(refuse_close={"XOM"})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        assert ("replace_order", "stop-aapl", {"stop_price": 114.0}) in fake.writes
        assert [w for w in fake.writes if w[0] == "submit_order"]
        assert "XOM    FAILED" in caplog.text
        assert "held_for_orders" in caplog.text


class ReservingAlpaca(FakeAlpaca):
    """A broker that reserves shares for live sell orders, as Alpaca does.

    DELETE /positions/{symbol} is refused while any live sell order holds the
    shares, and goes through once they are released. Closes it accepts become
    filled market-sell orders carrying a broker-generated client id, since that
    endpoint takes none; a submitted order keeps the id it was given.
    """

    def __init__(self, *args: object, refuse_after_release: set[str] | None = None, **kw) -> None:
        super().__init__(*args, **kw)  # type: ignore[arg-type]
        self.cancelled: set[str] = set()
        self.refuse_after_release = refuse_after_release or set()
        self.created: list[Order] = []

    def cancel_order(self, order_id: str) -> dict:
        self.cancelled.add(order_id)
        return super().cancel_order(order_id)

    def _reserved(self, symbol: str) -> float:
        return sum(
            o.qty - o.filled_qty
            for o in self.orders
            if o.symbol == symbol and o.side == "sell" and o.id not in self.cancelled
        )

    def close_position(self, symbol: str) -> dict:
        self.calls.append(("close_position", symbol))
        held = self._reserved(symbol)
        if held > 0:
            raise RuntimeError(
                f"alpaca DELETE /positions/{symbol} failed 403: "
                f'{{"code":40310000,"held_for_orders":"{held:g}","available":"0"}}'
            )
        if symbol in self.refuse_after_release:
            raise RuntimeError(f"alpaca DELETE /positions/{symbol} failed 422: market closed")
        pos = next(p for p in self.positions if p.symbol == symbol)
        order = self._order(f"close-{symbol}", symbol, pos.qty, "market", "68c3c73e-broker-uuid")
        return {"id": order.id, "client_order_id": order.client_order_id}

    def submit_order(self, **kw) -> SimpleNamespace:
        placed = super().submit_order(**kw)
        self._order(
            placed.id,
            kw["symbol"],
            kw["qty"],
            kw.get("order_type", "market"),
            kw.get("client_order_id") or "9f1d-broker-uuid",
        )
        return placed

    def _order(self, oid: str, symbol: str, qty: float, order_type: str, coid: str) -> Order:
        order = Order(
            id=oid,
            client_order_id=coid,
            symbol=symbol,
            side="sell",
            qty=qty,
            filled_qty=qty,
            order_type=order_type,
            status="filled",
            submitted_at=datetime.now(UTC),
            filled_avg_price=100.5,
        )
        self.created.append(order)
        return order


def _aged_xom(**kw) -> ReservingAlpaca:
    """XOM alone: flat for 45 bars, fully covered by a GTC stop -> TimeExit."""
    return ReservingAlpaca(
        positions=[_position("XOM", 100.5)],
        orders=[_stop("stop-xom", "XOM", 90.0)],
        fills=[_buy("XOM")],
        **kw,
    )


class TestTimeExitAgainstReservedShares:
    """The time exit is the system's only rule-driven exit; see the module docstring."""

    def test_known_bug_time_exit_refused_while_stop_reserves_shares(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Today, and deliberately unchanged in the zero-order tranche: the stop
        # is left in place, the close is refused, and nothing else is sent.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _aged_xom()

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        assert fake.writes == [("close_position", "XOM")]
        assert "held_for_orders" in caplog.text

    def test_known_bug_time_exit_never_frees_the_shares_its_stop_reserves(self) -> None:
        fake = _book()

        failures = mp._execute(fake, [TimeExit("XOM", 10.0, 45, 0.005)])

        assert failures == 0
        assert fake.writes == [("close_position", "XOM")]

    @pytest.mark.xfail(
        strict=True,
        reason="known bug: TimeExit does not release the stop that reserves its shares",
    )
    def test_target_time_exit_releases_the_reserving_stop_then_closes(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _aged_xom()

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.writes == [("cancel_order", "stop-xom"), ("close_position", "XOM")]

    @pytest.mark.xfail(
        strict=True,
        reason="known bug: TimeExit has no path that re-arms a stop it released",
    )
    def test_target_a_close_that_fails_after_the_release_re_arms_the_stop(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _aged_xom(refuse_after_release={"XOM"})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        rearm = [w for w in fake.writes if w[0] == "submit_order"]
        assert [(w[1]["symbol"], w[1]["order_type"], w[1]["stop_price"], w[1]["qty"])
                for w in rearm] == [("XOM", "stop", 90.0, 10.0)]
        assert rearm[0][1]["time_in_force"] == "gtc"

    def test_the_close_log_names_the_order_and_what_the_ledger_will_call_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Until the close carries its own stamp, it books as a flatten. The log
        # says so, and names the order so it can be traced by hand.
        caplog.set_level(logging.INFO, logger="manage_positions")

        mp._execute(_book(), [TimeExit("XOM", 10.0, 45, 0.005)])

        assert "close-XOM" in caplog.text
        assert "books as a flatten" in caplog.text


class TestTimeExitAttributionThroughTheLedger:
    """What the ledger books a production time exit as, from the pass to reconcile.

    The classifier knows a `time_exit` class and the id helper can mint the
    stamp it reads (test_exit_quality), but no production path sends that id.
    This follows the order the pass really produces into the reconcile step
    that stores the class, so it cannot be satisfied by the helpers alone.
    """

    def _booked_as(self, db_url: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
        from scripts.reconcile import attribute_exits, to_fills
        from tradingagents_us.execution.reconcile import reconcile_fills

        # No stop reserving the shares, so today's DELETE goes through and
        # there is an order to attribute.
        fake = ReservingAlpaca(
            positions=[_position("XOM", 100.5)], orders=[], fills=[_buy("XOM")]
        )
        _run(monkeypatch, fake, "--submit", "--db-url", db_url)
        (close,) = [o for o in fake.created if o.symbol == "XOM"]
        sell = FillActivity(
            id="fill-XOM-close",
            symbol="XOM",
            side="sell",
            qty=close.qty,
            price=100.5,
            transaction_time=datetime.now(UTC),
            order_id=close.id,
        )
        activities = [*fake.fills, sell]
        closed = reconcile_fills(to_fills(activities)).closed
        assert len(closed) == 1
        return attribute_exits(closed, activities, fake.created)

    def test_known_bug_a_production_time_exit_books_as_a_flatten(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert list(self._booked_as(db_url, monkeypatch).values()) == ["flatten"]

    @pytest.mark.xfail(
        strict=True, reason="TimeExit is an unstamped DELETE; books as flatten"
    )
    def test_target_a_production_time_exit_books_as_a_time_exit(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert list(self._booked_as(db_url, monkeypatch).values()) == ["time_exit"]


class TestEmptyBarCache:
    def test_a_held_name_with_no_bars_is_reported_and_never_closed(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # NVDA is flat and was bought 50 days ago, but the cache has nothing
        # for it. Its age must read as unknown, not as zero.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _book()

        _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        nvda = [r.getMessage() for r in caplog.records if r.getMessage().startswith("NVDA")]
        assert len(nvda) == 1
        assert "no_bars" in nvda[0]
        assert "age unknown" in nvda[0]
        assert "NVDA" not in str(fake.writes)
