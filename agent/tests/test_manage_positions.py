"""scripts.manage_positions end to end, against a stub broker.

The planner has its own tests; nothing covered the runner that turns its plan
into broker calls, and `close_position` appeared in no test at all. These drive
`main()` with a real bar cache and a fake client that records every call, and
assert on what would reach the broker: nothing in a dry run, exactly the plan
with --submit, and a TimeExit that is still the bare close it was — no cancel
of the protective stop and no sell of our own, so a close refused today stays
refused.
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


class TestTimeExitIsStillTheBareClose:
    """Labelling only. The close must fail in exactly the cases it fails today."""

    def test_a_refused_close_is_counted_and_the_rest_of_the_pass_still_runs(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _book(refuse_close={"XOM"})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        # Same calls as a clean pass: the refusal is not "fixed" by cancelling
        # the stop that reserves the shares, nor worked around with a sell.
        assert fake.writes == EXPECTED_PLAN
        assert "XOM    FAILED" in caplog.text
        assert "held_for_orders" in caplog.text

    def test_the_close_never_cancels_protection_or_submits_a_sell_of_its_own(self) -> None:
        fake = _book()

        failures = mp._execute(fake, [TimeExit("XOM", 10.0, 45, 0.005)])

        assert failures == 0
        assert fake.writes == [("close_position", "XOM")]

    def test_the_close_log_names_the_order_and_what_the_ledger_will_call_it(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Until the close carries its own stamp, it books as a flatten. The log
        # says so, and names the order so it can be traced by hand.
        caplog.set_level(logging.INFO, logger="manage_positions")

        mp._execute(_book(), [TimeExit("XOM", 10.0, 45, 0.005)])

        assert "close-XOM" in caplog.text
        assert "books as a flatten" in caplog.text

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
