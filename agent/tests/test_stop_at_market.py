"""No stop is set at, or within a hair of, the market.

A long's stop is `atr_mult` ATRs under the price, so an ATR of zero puts it ON
the price, and a sell stop at the last price fires on the first print of the
next session: a market sell of the position that no close logic decided. The
inputs that do it are ordinary. A flat tape (every bar h=l=c) or a stale cache
of identical bars gives an ATR of exactly 0, and bars on another scale than the
mark (1/10 after an unadjusted reverse split) give one far too small. Either
moves every stop in the book to the market in one pass, which is liquidation
through the stop logic, and a cap on closes never counts it.

The planner tests pin the rule; the runner tests drive `manage_positions.main()`
against a real bar cache and assert on what would reach the broker, because a
guard in the planner proves nothing about the pass until the pass is shown to
stop there too.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from scripts import manage_positions as mp
from tradingagents_us.dataflows.alpaca_broker import FillActivity, Order, Position
from tradingagents_us.risk.position_manager import (
    Bar,
    ManagedPosition,
    ManagementConfig,
    RatchetStop,
    plan_actions,
)
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.price_cache import write_bars

BACKFILL = ManagementConfig(backfill_missing_stops=True)


def _bars(high: float, low: float, close: float, n: int = 30) -> list[Bar]:
    return [Bar(high, low, close) for _ in range(n)]


#: Every bar h=l=c: the ATR is exactly 0.
FLAT_TAPE = _bars(100.0, 100.0, 100.0)
#: A $100 name's bars at 1/10 scale: ATR 0.2, a 3-ATR stop 0.6% under the mark.
TENTH_SCALE = _bars(10.1, 9.9, 10.0)
#: A real, calm name: ATR 2.0 on a $100 close.
HEALTHY = _bars(101.0, 99.0, 100.0)


def _held(ticker: str = "AAPL", price: float = 100.0, **kw) -> ManagedPosition:
    base = dict(
        ticker=ticker,
        quantity=10.0,
        avg_entry_price=95.0,
        current_price=price,
        bars_held=5,
        current_stop=90.0,
        stop_order_id=f"stop-{ticker}",
    )
    base.update(kw)
    return ManagedPosition(**base)  # type: ignore[arg-type]


def _naked(ticker: str = "AAPL", price: float = 100.0) -> ManagedPosition:
    return _held(ticker, price, current_stop=None, stop_order_id=None, naked_quantity=10.0)


class TestPlanner:
    def test_a_flat_tape_does_not_ratchet_the_stop_onto_the_mark(self) -> None:
        # The reported shape: ATR 0, so the trail sat on the price and the stop
        # at 90 was replaced to 100.00 on a 100.00 mark.
        actions, skips = plan_actions([_held()], {"AAPL": FLAT_TAPE}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["bad_atr"]
        assert "flat tape" in skips[0].detail

    def test_a_flat_tape_does_not_place_a_stop_a_fraction_of_a_cent_under_the_mark(
        self,
    ) -> None:
        # The old back-fill guard was `level >= price`: at a mark of 100.004 it
        # let a stop through at 100.00, 0.4 cents under the market.
        actions, skips = plan_actions([_naked(price=100.004)], {"AAPL": FLAT_TAPE}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["bad_atr"]

    def test_bars_on_another_scale_do_not_ratchet_the_stop_to_the_market(self) -> None:
        # ATR 0.2 on a $100 mark: the trail is 99.40, 0.6% under the price,
        # closer than any real series of this universe could put a 3-ATR stop.
        actions, skips = plan_actions([_held()], {"AAPL": TENTH_SCALE}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["stop_at_market"]
        assert "99.40" in skips[0].detail

    def test_bars_on_another_scale_do_not_place_a_stop_at_the_market(self) -> None:
        actions, skips = plan_actions([_naked()], {"AAPL": TENTH_SCALE}, BACKFILL)

        assert actions == []
        assert "stop_at_market" in [s.reason for s in skips]

    def test_a_nan_in_the_bars_is_named_not_read_as_an_unchanged_stop(self) -> None:
        # max(stop, nan) keeps the stop, so the pass used to call this
        # `stop_unchanged`: a corrupt series reported as a quiet day.
        bars = [*HEALTHY[:-1], Bar(math.nan, math.nan, math.nan)]
        actions, skips = plan_actions([_held()], {"AAPL": bars}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["bad_atr"]

    def test_one_bad_series_is_refused_and_a_real_one_beside_it_still_ratchets(
        self,
    ) -> None:
        # The guard is a floor under the distance, not a brake on the trail:
        # a real 2.0 ATR on a $120 mark still moves 90 -> 114.
        book = [_held("AAPL", 120.0), _held("XOM"), _naked("MSFT")]
        bars = {"AAPL": HEALTHY, "XOM": FLAT_TAPE, "MSFT": TENTH_SCALE}

        actions, skips = plan_actions(book, bars, BACKFILL)

        assert actions == [RatchetStop("AAPL", "stop-AAPL", 90.0, 114.0, 2.0)]
        assert {(s.ticker, s.reason) for s in skips} >= {
            ("XOM", "bad_atr"),
            ("MSFT", "stop_at_market"),
        }


# --------------------------------------------------------------------------
# The pass, end to end.
# --------------------------------------------------------------------------

TODAY = datetime.now(UTC).date()
ENTRY = TODAY - timedelta(days=50)
BAR_DAYS = 45
NAMES = [f"T{i:02d}" for i in range(6)]
WRITES = ("submit_order", "replace_order", "cancel_order", "close_position")


class _Broker:
    """Records every call; reads return the book it was built with."""

    def __init__(self, positions: list[Position], orders: list[Order]) -> None:
        self.positions = positions
        self.orders = orders
        self.calls: list[tuple] = []

    def __enter__(self) -> _Broker:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def list_positions(self) -> list[Position]:
        return list(self.positions)

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False):
        return list(self.orders)

    def list_fill_activities(self) -> list[FillActivity]:
        return [
            FillActivity(
                id=f"fill-{p.symbol}",
                symbol=p.symbol,
                side="buy",
                qty=p.qty,
                price=p.avg_entry_price,
                transaction_time=datetime.combine(ENTRY, datetime.min.time(), UTC),
                order_id=f"buy-{p.symbol}",
            )
            for p in self.positions
        ]

    def replace_order(self, order_id: str, **kw) -> SimpleNamespace:
        self.calls.append(("replace_order", order_id, kw))
        return SimpleNamespace(id=f"replaced-{order_id}")

    def submit_order(self, **kw) -> SimpleNamespace:
        self.calls.append(("submit_order", kw))
        return SimpleNamespace(id=f"placed-{kw['symbol']}")

    def cancel_order(self, order_id: str) -> dict:
        self.calls.append(("cancel_order", order_id))
        return {}

    def close_position(self, symbol: str) -> dict:
        self.calls.append(("close_position", symbol))
        return {}

    @property
    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in WRITES]


def _position(symbol: str, price: float, avg: float = 95.0) -> Position:
    # Up 5%+ on entry, so nothing is flat and no time exit is due: every write
    # this pass makes is stop maintenance.
    return Position(
        symbol=symbol,
        qty=10.0,
        side="long",
        avg_entry_price=avg,
        market_value=10.0 * price,
        unrealized_pl=10.0 * (price - avg),
        unrealized_plpc=price / avg - 1.0,
    )


def _stop(symbol: str, stop_price: float = 90.0) -> Order:
    return Order(
        id=f"stop-{symbol}",
        client_order_id=f"coid-stop-{symbol}",
        symbol=symbol,
        side="sell",
        qty=10.0,
        filled_qty=0.0,
        order_type="stop",
        status="new",
        submitted_at=datetime.now(UTC),
        filled_avg_price=None,
        stop_price=stop_price,
    )


def _cache(tmp_path: Path, bars_by_symbol: dict[str, tuple[float, float, float]]) -> str:
    url = f"sqlite:///{tmp_path / 'bars.db'}"
    repo = TradeLogRepository(engine=create_engine(url, future=True))
    with repo.session() as session:
        for symbol, (high, low, close) in bars_by_symbol.items():
            rows = [
                {"t": (TODAY - timedelta(days=d)).isoformat(), "o": close, "h": high,
                 "l": low, "c": close, "v": 1_000_000}
                for d in range(BAR_DAYS, 0, -1)
            ]
            write_bars(session, symbol, rows)
    return url


@pytest.fixture(autouse=True)
def _no_real_kill_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KILL_SWITCH_FILE", str(tmp_path / "kill_switch.state"))


def _run(monkeypatch: pytest.MonkeyPatch, fake: _Broker, url: str) -> int:
    monkeypatch.setattr(mp, "AlpacaClient", lambda: fake)
    return mp.main(["--submit", "--backfill-stops", "--db-url", url])


class TestThePass:
    def test_a_flat_tape_sends_no_stop_to_the_market(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Six protected names on a flat tape at 105: every stop used to be
        # replaced to 105.00, the whole book selling at the next open.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _Broker([_position(s, 105.0) for s in NAMES], [_stop(s) for s in NAMES])
        url = _cache(tmp_path, {s: (100.0, 100.0, 100.0) for s in NAMES})

        rc = _run(monkeypatch, fake, url)

        assert rc == 0
        assert fake.writes == [], fake.writes
        assert caplog.text.count("bad_atr") == len(NAMES)

    def test_bars_on_another_scale_neither_ratchet_nor_place_at_the_market(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Half the book protected, half naked, all on 1/10-scale bars; one real
        # name beside them. Only the real one may move.
        protected, naked = NAMES[:3], NAMES[3:]
        fake = _Broker(
            [_position(s, 100.0) for s in NAMES] + [_position("AAPL", 120.0)],
            [_stop(s) for s in protected] + [_stop("AAPL")],
        )
        url = _cache(
            tmp_path,
            {**{s: (10.1, 9.9, 10.0) for s in NAMES}, "AAPL": (101.0, 99.0, 100.0)},
        )

        rc = _run(monkeypatch, fake, url)

        assert rc == 0
        # No back-fill for the naked three, no ratchet for the protected three.
        assert fake.writes == [("replace_order", "stop-AAPL", {"stop_price": 114.0})], (
            f"naked {naked}, protected {protected}: {fake.writes}"
        )
