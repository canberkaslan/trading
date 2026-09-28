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
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine, text

from scripts import manage_positions as mp
from tradingagents_us.dataflows.alpaca_broker import FillActivity, Order, Position
from tradingagents_us.dataflows.polygon import BASE, Aggregate, PolygonClient
from tradingagents_us.risk.position_manager import (
    Bar,
    ManagedPosition,
    ManagementConfig,
    PlaceStop,
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
#: On the mark's own scale, but calmer than any name in the universe: ATR 0.1
#: on a $100 close, a 3-ATR stop 0.3% under the mark.
TOO_CALM = _bars(100.05, 99.95, 100.0)
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


def _naked(ticker: str = "AAPL", price: float = 100.0, **kw) -> ManagedPosition:
    return _held(ticker, price, current_stop=None, stop_order_id=None, naked_quantity=10.0, **kw)


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

    def test_a_series_calmer_than_any_real_name_does_not_ratchet_to_the_market(
        self,
    ) -> None:
        # ATR 0.1 on a $100 mark: the trail is 99.70, 0.3% under the price,
        # closer than any real series of this universe could put a 3-ATR stop.
        actions, skips = plan_actions([_held()], {"AAPL": TOO_CALM}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["stop_at_market"]
        assert "99.70" in skips[0].detail

    def test_a_series_calmer_than_any_real_name_does_not_place_at_the_market(self) -> None:
        actions, skips = plan_actions([_naked()], {"AAPL": TOO_CALM}, BACKFILL)

        assert actions == []
        assert "stop_at_market" in [s.reason for s in skips]

    def test_bars_on_another_scale_do_not_ratchet_the_stop_to_the_market(self) -> None:
        # ATR 0.2 on a $100 mark put the trail at 99.40. The last close, 10.00,
        # says the bars are not on the mark's scale at all.
        actions, skips = plan_actions([_held()], {"AAPL": TENTH_SCALE}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["mark_disagrees_with_bars"]

    def test_bars_on_another_scale_do_not_place_a_stop_at_the_market(self) -> None:
        actions, skips = plan_actions([_naked()], {"AAPL": TENTH_SCALE}, BACKFILL)

        assert actions == []
        assert "mark_disagrees_with_bars" in [s.reason for s in skips]

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
        # a real 2.0 ATR on a $120 name still moves 90 -> 114.
        book = [_held("AAPL", 120.0), _held("XOM"), _naked("MSFT")]
        bars = {"AAPL": _bars(121.0, 119.0, 120.0), "XOM": FLAT_TAPE, "MSFT": TENTH_SCALE}

        actions, skips = plan_actions(book, bars, BACKFILL)

        assert actions == [RatchetStop("AAPL", "stop-AAPL", 90.0, 114.0, 2.0)]
        assert {(s.ticker, s.reason) for s in skips} >= {
            ("XOM", "bad_atr"),
            ("MSFT", "mark_disagrees_with_bars"),
        }


class TestTheMarkAgainstTheBars:
    """The distance guard measures from the mark, so it cannot doubt the mark.

    A mark that is itself wrong (the broker's last print doubled) puts the stop
    a normal distance under a price that is not real, above the real market.
    Bars on the wrong scale for a volatile name put it 0.9% under the mark,
    past the 0.75% floor. Bars in cents put a back-fill at one cent. All three
    look like a sane stop from the mark's side, and all three disagree with the
    last cached close by far more than a session moves.
    """

    def test_a_doubled_mark_does_not_ratchet_a_stop_above_the_real_market(self) -> None:
        # Bars at 100 with an ATR of 2; the mark reads 200. The trail was 194.
        actions, skips = plan_actions([_held(price=200.0)], {"AAPL": HEALTHY}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["mark_disagrees_with_bars"]
        assert "200.00" in skips[0].detail and "100.00" in skips[0].detail

    def test_a_doubled_mark_does_not_place_a_stop_above_the_real_market(self) -> None:
        actions, skips = plan_actions([_naked(price=200.0)], {"AAPL": HEALTHY}, BACKFILL)

        assert actions == []
        assert "mark_disagrees_with_bars" in [s.reason for s in skips]

    @pytest.mark.parametrize("factor", [1.05, 1.1, 1.2, 1.24])
    def test_a_mark_high_by_less_than_the_band_trails_from_the_last_close(
        self, factor: float
    ) -> None:
        # Inside the band a wrong mark still moved the trail: 20% high on bars
        # at 100 with an ATR of 2 ratcheted 90 -> 114 and back-filled at 114,
        # every stop above the last close, the whole book sold at the open.
        mark = 100.0 * factor
        actions, skips = plan_actions(
            [_held(price=mark), _naked("MSFT", price=mark)],
            {"AAPL": HEALTHY, "MSFT": HEALTHY},
            BACKFILL,
        )

        assert actions == [
            RatchetStop("AAPL", "stop-AAPL", 90.0, 94.0, 2.0),
            PlaceStop("MSFT", 10.0, 94.0, 2.0),
        ], skips

    @pytest.mark.parametrize(
        ("name", "factor"),
        [
            ("META 2022-10-27", 0.754),   # -24.6%, refused at 1.25
            ("UNH 2025-04-17", 0.776),    # -22.4%, refused at 1.25
            ("UNH 2026-01-27", 0.8039),   # -19.61%, cleared 1.25 by 0.005
            ("NVDA 2023-05-25", 1.244),   # +24.4%, cleared 1.25 by 0.005
        ],
    )
    def test_a_real_one_session_move_is_not_read_as_a_wrong_mark(
        self, name: str, factor: float
    ) -> None:
        # Real closes from the last five years of this universe. A crash day is
        # when a naked lot most needs its stop; refusing it as a "wrong mark"
        # left it naked on exactly that day.
        mark = 100.0 * factor
        actions, skips = plan_actions([_naked(price=mark)], {"AAPL": HEALTHY}, BACKFILL)

        assert "mark_disagrees_with_bars" not in [s.reason for s in skips], (name, skips)
        assert [type(a).__name__ for a in actions] == ["PlaceStop"], (name, skips)

    @pytest.mark.parametrize("factor", [1.51, 1 / 1.51, 2.0, 0.5])
    def test_a_mark_past_the_band_is_still_refused(self, factor: float) -> None:
        actions, skips = plan_actions([_naked(price=100.0 * factor)], {"AAPL": HEALTHY}, BACKFILL)

        assert actions == []
        assert "mark_disagrees_with_bars" in [s.reason for s in skips]

    def test_a_mark_under_the_last_close_is_trailed_from_the_mark(self) -> None:
        # A fall since the close is followed at once: the lower price is the
        # mark, and a stop measured from the close would sit above it.
        actions, _ = plan_actions([_naked(price=95.0)], {"AAPL": HEALTHY}, BACKFILL)

        assert actions == [PlaceStop("AAPL", 10.0, 89.0, 2.0)]

    def test_tenth_scale_bars_on_a_volatile_name_do_not_ratchet_the_stop(self) -> None:
        # 3% ATR at 1/10 scale: 0.30 on a close of 10, so the trail on a $100
        # mark is 99.10, 0.9% under it, which the distance floor lets through.
        actions, skips = plan_actions(
            [_held()], {"AAPL": _bars(10.15, 9.85, 10.0)}, BACKFILL
        )

        assert actions == []
        assert [s.reason for s in skips] == ["mark_disagrees_with_bars"]

    def test_bars_in_cents_do_not_place_a_one_cent_stop(self) -> None:
        # ATR 200 on a $100 mark floors the back-fill at a cent: the name then
        # reads as covered and the naked-book page goes quiet.
        actions, skips = plan_actions(
            [_naked()], {"AAPL": _bars(10_100.0, 9_900.0, 10_000.0)}, BACKFILL
        )

        assert actions == []
        assert "mark_disagrees_with_bars" in [s.reason for s in skips]

    def test_a_stale_tail_of_identical_bars_is_not_an_atr(self) -> None:
        # Real bars, then the same h=l=c bar appended twenty times: the ATR
        # decays to 0.45 and the trail tightens to 98.63 on a 100 mark. No name
        # in this universe prints a day with no range.
        bars = [*HEALTHY[:25], *_bars(100.0, 100.0, 100.0, n=20)]

        actions, skips = plan_actions([_held()], {"AAPL": bars}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["bad_atr"]
        assert "no range" in skips[0].detail

    def test_a_split_re_fetched_in_part_does_not_place_a_one_cent_stop(self) -> None:
        # A 10:1 split. A chart view re-fetched the last nine bars adjusted; the
        # older rows are still at 1000. The last close agrees with the mark, so
        # the band passes, and the true range across the join puts the ATR at
        # 46.73: the back-fill floored at a cent, and coverage counted it.
        bars = [*_bars(1010.0, 990.0, 1000.0, n=32), *_bars(101.0, 99.0, 100.0, n=9)]

        actions, skips = plan_actions([_naked(), _held("XOM")], {"AAPL": bars, "XOM": bars},
                                      BACKFILL)

        assert actions == []
        assert [(s.ticker, s.reason) for s in skips] == [("AAPL", "bad_atr"), ("XOM", "bad_atr")]
        assert "two scales" in skips[0].detail

    def test_a_join_older_than_the_atr_period_is_still_a_join(self) -> None:
        # A 2:1 split twenty bars back. Wilder's smoothing still carries the
        # join: the ATR reads 4.18 against a real 2.00, and the back-fill went
        # to 87.45, a stop sized off a range the name never had.
        bars = [*_bars(202.0, 198.0, 200.0, n=21), *_bars(101.0, 99.0, 100.0, n=20)]

        actions, skips = plan_actions([_naked()], {"AAPL": bars}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["bad_atr"]

    def test_a_range_no_real_name_has_does_not_place_a_stop_far_under_the_price(
        self,
    ) -> None:
        # One scale throughout, and the mark on it, but a daily range of the
        # whole price: 3 ATRs floor the back-fill at a cent.
        actions, skips = plan_actions([_naked()], {"AAPL": _bars(150.0, 50.0, 100.0)}, BACKFILL)

        assert actions == []
        assert [s.reason for s in skips] == ["bad_atr"]
        assert "50%" in skips[0].detail

    def test_bars_further_behind_than_two_sessions_ratchet_nothing(self) -> None:
        # A cache weeks behind cannot vouch for the mark: a trail off it is a
        # move nothing recent backs. The standing stop stays.
        actions, skips = plan_actions(
            [_held(price=120.0, bars_behind=3)], {"AAPL": _bars(121.0, 119.0, 120.0)}, BACKFILL
        )

        assert actions == []
        assert [s.reason for s in skips] == ["stale_bars"]

    def test_a_move_past_the_band_on_stale_bars_is_named_for_the_bars(self) -> None:
        # Bars twenty sessions old at 100, a real run to 160 since: past the
        # band (+50%), refused, and named for the cache being behind rather than
        # blamed on the mark. (This read 135 while the band was 1.25.)
        actions, skips = plan_actions(
            [_held(price=160.0, bars_behind=20), _naked("MSFT", price=160.0, bars_behind=20)],
            {"AAPL": HEALTHY, "MSFT": HEALTHY},
            BACKFILL,
        )

        assert actions == []
        assert [(s.ticker, s.reason) for s in skips] == [
            ("AAPL", "stale_bars"), ("MSFT", "stale_bars")
        ]
        assert "20 sessions behind" in skips[0].detail
        assert "mark_disagrees_with_bars" in skips[0].detail

    def test_a_move_inside_the_band_on_stale_bars_covers_the_naked_lot_only(self) -> None:
        # 135 on bars twenty sessions old is inside the band now. The naked lot
        # is covered, measured from the lower of mark and close (100 -> 94), and
        # the held lot's stop is not ratcheted off bars that far behind.
        actions, skips = plan_actions(
            [_held(price=135.0, bars_behind=20), _naked("MSFT", price=135.0, bars_behind=20)],
            {"AAPL": HEALTHY, "MSFT": HEALTHY},
            BACKFILL,
        )

        assert actions == [PlaceStop("MSFT", 10.0, 94.0, 2.0)]
        assert ("AAPL", "stale_bars") in [(s.ticker, s.reason) for s in skips]

    def test_naked_shares_are_still_covered_off_stale_bars_that_pass_every_check(
        self,
    ) -> None:
        # No stop at all is the worse state. Measured from the lower of mark
        # and close, a rise since the last bar puts the stop lower, not higher.
        actions, _ = plan_actions(
            [_naked(price=110.0, bars_behind=20)], {"AAPL": HEALTHY}, BACKFILL
        )

        assert actions == [PlaceStop("AAPL", 10.0, 94.0, 2.0)]

    def test_the_session_just_ended_and_a_holiday_are_not_stale(self) -> None:
        actions, _ = plan_actions([_held(price=120.0, bars_behind=2)],
                                  {"AAPL": _bars(121.0, 119.0, 120.0)}, BACKFILL)

        assert actions == [RatchetStop("AAPL", "stop-AAPL", 90.0, 114.0, 2.0)]

    def test_stale_bars_do_not_hold_back_a_time_exit(self) -> None:
        # A stale cache undercounts the age, never overcounts it: the exit is
        # late at worst, and the budget caps it either way.
        actions, _ = plan_actions(
            [_held(price=100.5, avg_entry_price=100.0, bars_held=25, bars_behind=20)],
            {"AAPL": HEALTHY}, BACKFILL,
        )

        assert [type(a).__name__ for a in actions] == ["TimeExit"]

    def test_a_volatile_names_real_move_is_not_taken_for_a_bad_mark(self) -> None:
        # ATR 10 on a close of 100: a 28% move since the last cached close is
        # under three ATRs, so it is a session, not a scale error. The stop is
        # set off the close until the bars show the move: 100 - 30.
        actions, _ = plan_actions(
            [_naked(price=128.0)], {"AAPL": _bars(105.0, 95.0, 100.0)}, BACKFILL
        )

        assert [(type(a).__name__, a.stop_price) for a in actions] == [("PlaceStop", 70.0)]


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


def _cache_rows(url: str) -> list[tuple]:
    """Every row of the shared bar cache, as stored, fetch stamps included."""
    with create_engine(url, future=True).connect() as conn:
        return sorted(tuple(r) for r in conn.execute(text("SELECT * FROM price_bars")))


def _run(monkeypatch: pytest.MonkeyPatch, fake: _Broker, url: str, *extra: str) -> int:
    monkeypatch.setattr(mp, "AlpacaClient", lambda: fake)
    return mp.main(["--submit", "--backfill-stops", "--db-url", url, *extra])


def _weekdays_to(end: date, n: int) -> list[date]:
    """The n weekdays up to and including `end` (or the one before it), oldest first."""
    out: list[date] = []
    d = end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


class _Polygon:
    """Serves daily bars at each name's own level, through today, and records asks."""

    def __init__(self, closes: dict[str, float], fail: frozenset[str] = frozenset()) -> None:
        self.closes = closes
        self.fail = fail
        self.asked: list[tuple[str, date, date]] = []

    def __enter__(self) -> _Polygon:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def aggregates(self, ticker: str, from_date: date, to_date: date, **_: object):
        self.asked.append((ticker, from_date, to_date))
        if ticker in self.fail:
            raise RuntimeError(f"polygon request failed: {ticker}")
        c = self.closes[ticker]
        eastern = ZoneInfo("America/New_York")
        return [
            Aggregate(
                timestamp_ms=int(datetime.combine(d, time(0), eastern).timestamp() * 1000),
                open=c, high=c + 1.0, low=c - 1.0, close=c, volume=1e6,
                vwap=None, transactions=None,
            )
            for d in _weekdays_to(to_date, (to_date - from_date).days)
            if d >= from_date
        ]


#: Four names bought at 100 and protected at 94, one naked, that have since
#: run 30-40% for real. The cache got its last bars 20 sessions ago, at 100:
#: nobody has opened their charts since, and nothing else writes the cache.
STALE_BOOK = {"AAPL": 135.0, "MSFT": 130.0, "NVDA": 140.0, "META": 133.0, "JPM": 131.0}


#: The stale book and two more names, one of them past Polygon's five a minute.
WIDE_BOOK = {**STALE_BOOK, "V": 128.0, "UNH": 132.0}


class _RateLimitedPolygon:
    """Polygon at five requests in any sixty seconds, on a virtual clock.

    The real PolygonClient runs against it, backoff and all: `sleep` advances
    the clock instead of waiting, for the client's retries and the runner's
    pacing alike. Each request takes REQUEST_S of it.
    """

    LIMIT, WINDOW_S, REQUEST_S = 5, 60.0, 0.3

    def __init__(self, closes: dict[str, float]) -> None:
        self.closes = closes
        self.now = 0.0
        self.served: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def _handle(self, request: httpx.Request) -> httpx.Response:
        now, self.now = self.now, self.now + self.REQUEST_S
        if sum(now - t < self.WINDOW_S for t in self.served) >= self.LIMIT:
            return httpx.Response(429, json={"status": "ERROR"})
        self.served.append(now)
        c = self.closes[request.url.path.split("/")[4]]
        eastern = ZoneInfo("America/New_York")
        return httpx.Response(200, json={"results": [
            {"t": int(datetime.combine(d, time(0), eastern).timestamp() * 1000),
             "o": c, "h": c + 1.0, "l": c - 1.0, "c": c, "v": 1e6}
            for d in _weekdays_to(TODAY, 40)
        ]})

    def client(self) -> PolygonClient:
        client = PolygonClient(api_key="test")
        client._http = httpx.Client(transport=httpx.MockTransport(self._handle), base_url=BASE)
        return client


class TestABarCacheLeftBehind:
    """The band trusts the last cached close to be the session just ended.

    Only the prices route writes the cache, when someone opens a chart. A held
    name nobody charted kept weeks-old bars, and once it had moved past the
    band its stop was refused every night with rc 0: no page, and the stop
    stayed at its entry level while origin/main still ratcheted it.
    """

    @pytest.fixture(autouse=True)
    def _no_pacing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The refresh waits between names for Polygon's rate limit.
        monkeypatch.setattr("time.sleep", lambda _: None)

    def _stale(
        self, tmp_path: Path, book: dict[str, float] = STALE_BOOK, uncached: str = ""
    ) -> tuple[_Broker, str]:
        fake = _Broker(
            [_position(s, m, avg=100.0) for s, m in book.items()],
            [_stop(s, 94.0) for s in list(book)[:4]],
        )
        url = f"sqlite:///{tmp_path / 'bars.db'}"
        repo = TradeLogRepository(engine=create_engine(url, future=True))
        cached = _weekdays_to(TODAY, 60)[:40]
        with repo.session() as session:
            for symbol in (s for s in book if s != uncached):
                write_bars(session, symbol, [
                    {"t": d.isoformat(), "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0,
                     "v": 1_000_000}
                    for d in cached
                ])
        return fake, url

    def test_a_stale_cache_fails_the_pass_instead_of_refusing_in_silence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake, url = self._stale(tmp_path)

        rc = _run(monkeypatch, fake, url)

        assert rc == 1, "daily_run pages on a failed pass, and on nothing quieter"
        # No stop is ratcheted off bars twenty sessions behind. The one naked lot
        # is still covered: its move (131 on a close of 100) is inside the band,
        # and it is measured from the lower of mark and close, 100 - 3 x 2.0.
        assert fake.writes == [
            ("submit_order", {"symbol": "JPM", "qty": 10.0, "side": "sell",
                              "order_type": "stop", "time_in_force": "gtc",
                              "stop_price": 94.0}),
        ]
        assert caplog.text.count("SKIP  stale_bars") == len(STALE_BOOK) - 1

    def test_the_refresh_brings_the_bars_up_and_the_stops_follow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake, url = self._stale(tmp_path)
        polygon = _Polygon(STALE_BOOK)
        monkeypatch.setattr(mp, "PolygonClient", lambda: polygon)

        rc = _run(monkeypatch, fake, url, "--refresh-bars")

        assert rc == 0
        assert sorted(t for t, _, _ in polygon.asked) == sorted(STALE_BOOK)
        assert {(f, t) for _, f, t in polygon.asked} == {
            (TODAY - timedelta(days=mp.BAR_LOOKBACK_DAYS), TODAY)
        }
        # Three ATRs of 2.0 under each name's own close.
        assert sorted(fake.writes, key=str) == sorted([
            ("replace_order", "stop-AAPL", {"stop_price": 129.0}),
            ("replace_order", "stop-MSFT", {"stop_price": 124.0}),
            ("replace_order", "stop-NVDA", {"stop_price": 134.0}),
            ("replace_order", "stop-META", {"stop_price": 127.0}),
            ("submit_order", {"symbol": "JPM", "qty": 10.0, "side": "sell",
                              "order_type": "stop", "time_in_force": "gtc",
                              "stop_price": 125.0}),
        ], key=str)

    def test_the_refresh_leaves_the_shared_bar_cache_as_it_found_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # trade.py's BUY checks read this cache: the correlation cap
        # (count_correlated over the held names), the price-anomaly gate
        # (rolling_price_stats) and the liquidity floor (average_dollar_volume).
        # Nothing in the daily run wrote it on main, so writing the held
        # names' bars here, minutes before the councils run, changed what those
        # checks decide on a BUY. The pass uses the fresh bars and keeps them.
        fake, url = self._stale(tmp_path)
        before = _cache_rows(url)
        monkeypatch.setattr(mp, "PolygonClient", lambda: _Polygon(STALE_BOOK))

        rc = _run(monkeypatch, fake, url, "--refresh-bars")

        assert rc == 0
        assert len(fake.writes) == len(STALE_BOOK), "the stops still follow the fresh bars"
        assert _cache_rows(url) == before

    def test_the_refresh_keeps_to_polygons_rate_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Seven held names through the real PolygonClient, at five requests
        # in any sixty seconds. Unpaced, the first five went through at once
        # and the sixth spent all five of the client's retries inside the same
        # minute: its bars were never refreshed, every night, the same name.
        caplog.set_level(logging.INFO, logger="manage_positions")
        polygon = _RateLimitedPolygon(WIDE_BOOK)
        monkeypatch.setattr("time.sleep", polygon.sleep)
        monkeypatch.setattr(mp, "PolygonClient", polygon.client)
        fake, url = self._stale(tmp_path, WIDE_BOOK)

        rc = _run(monkeypatch, fake, url, "--refresh-bars")

        assert "not refreshed" not in caplog.text
        assert rc == 0
        assert len(fake.writes) == len(WIDE_BOOK)

    @pytest.mark.parametrize("cached", [0, 5], ids=["no-bars", "too-few-bars"])
    def test_a_name_the_refresh_misses_with_no_usable_cache_fails_the_pass(
        self, cached: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # NVDA's refresh fails, and its cache has nothing to judge it by: no
        # age for the time exit, no ATR for its stop. It was skipped as
        # no_bars or insufficient_bars, neither of them a refusal, so the pass
        # returned 0: a held name left unmanaged, night after night, unpaged.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake, url = self._stale(tmp_path, uncached="NVDA")
        repo = TradeLogRepository(engine=create_engine(url, future=True))
        with repo.session() as session:
            write_bars(session, "NVDA", [
                {"t": d.isoformat(), "o": 140.0, "h": 141.0, "l": 139.0, "c": 140.0, "v": 1e6}
                for d in _weekdays_to(TODAY, cached)
            ] if cached else [])
        monkeypatch.setattr(mp, "PolygonClient", lambda: _Polygon(STALE_BOOK, frozenset({"NVDA"})))

        rc = _run(monkeypatch, fake, url, "--refresh-bars")

        assert rc == 1
        (line,) = [r.getMessage() for r in caplog.records if "UNREFRESHED" in r.getMessage()]
        assert "NVDA" in line
        assert len(fake.writes) == len(STALE_BOOK) - 1

    def test_a_name_the_refresh_misses_is_judged_by_its_cache_and_pages(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake, url = self._stale(tmp_path)
        monkeypatch.setattr(mp, "PolygonClient", lambda: _Polygon(STALE_BOOK, frozenset({"NVDA"})))

        rc = _run(monkeypatch, fake, url, "--refresh-bars")

        assert rc == 1
        assert "NVDA   SKIP  stale_bars" in caplog.text
        assert len(fake.writes) == len(STALE_BOOK) - 1

    def test_a_name_sold_during_the_refresh_is_not_written_to(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The book was read before the refresh, which paces Polygon at 12 s a
        # name, and the writes planned off that read went out minutes later.
        # AAPL, sold in regular hours while the refresh ran (its stop
        # cancelled, the lot closed), still had its stop moved.
        fake, url = self._stale(tmp_path)

        def sell_aapl() -> None:
            fake.positions = [p for p in fake.positions if p.symbol != "AAPL"]
            fake.orders = [o for o in fake.orders if o.symbol != "AAPL"]

        polygon = _PolygonMidRefresh(STALE_BOOK, sell_aapl)
        monkeypatch.setattr(mp, "PolygonClient", lambda: polygon)

        rc = _run(monkeypatch, fake, url, "--refresh-bars")

        assert polygon.fired
        assert rc == 0
        assert [w for w in fake.writes if "AAPL" in str(w)] == []
        assert len(fake.writes) == len(STALE_BOOK) - 1

    def test_a_flatten_flipped_during_the_refresh_is_seen_before_any_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The kill switch was read once, before the refresh: FLATTEN_ALL
        # flipped on the phone while it ran was never seen, and the pass
        # wrote beside the flatten path, the two writers the switch exists
        # to keep apart.
        fake, url = self._stale(tmp_path)
        switch = tmp_path / "kill_switch.state"  # where _no_real_kill_switch points it
        polygon = _PolygonMidRefresh(STALE_BOOK, lambda: switch.write_text("FLATTEN_ALL"))
        monkeypatch.setattr(mp, "PolygonClient", lambda: polygon)

        rc = _run(monkeypatch, fake, url, "--refresh-bars")

        assert polygon.fired
        assert rc == 0
        assert fake.writes == []


class _PolygonMidRefresh(_Polygon):
    """Polygon that lets something happen at the broker on the third name asked."""

    def __init__(self, closes: dict[str, float], event) -> None:
        super().__init__(closes)
        self.event = event
        self.fired = False

    def aggregates(self, ticker: str, from_date: date, to_date: date, **kw: object):
        if len(self.asked) == 2:
            self.fired = True
            self.event()
        return super().aggregates(ticker, from_date, to_date, **kw)


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

        assert rc == 1, "a refused input fails the pass, so daily_run pages on it"
        assert fake.writes == [], fake.writes
        assert caplog.text.count("SKIP  bad_atr") == len(NAMES)
        (refused,) = [r.getMessage() for r in caplog.records if "REFUSED" in r.getMessage()]
        assert all(f"{s} (bad_atr)" in refused for s in NAMES), refused

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
            {**{s: (10.1, 9.9, 10.0) for s in NAMES}, "AAPL": (121.0, 119.0, 120.0)},
        )

        rc = _run(monkeypatch, fake, url)

        assert rc == 1, "a refused input fails the pass, so daily_run pages on it"
        # No back-fill for the naked three, no ratchet for the protected three.
        assert fake.writes == [("replace_order", "stop-AAPL", {"stop_price": 114.0})], (
            f"naked {naked}, protected {protected}: {fake.writes}"
        )

    def test_a_doubled_mark_moves_no_stop_above_the_real_market(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Twelve names, half protected and half naked, over real bars at 100
        # with an ATR of 2; the broker's market value reads double. Every stop
        # used to go to 194.00, all above the real market, all selling at the
        # next open, and the exit budget counted none of it.
        names = [f"D{i:02d}" for i in range(12)]
        fake = _Broker(
            [_position(s, 200.0) for s in names], [_stop(s) for s in names[:6]]
        )
        url = _cache(tmp_path, {s: (101.0, 99.0, 100.0) for s in names})

        rc = _run(monkeypatch, fake, url)

        assert rc == 1, "a refused input fails the pass, so daily_run pages on it"
        assert fake.writes == [], fake.writes

    def test_a_split_re_fetched_in_part_places_no_one_cent_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Twelve names, half naked, after a 10:1 split whose last nine bars were
        # re-fetched adjusted: every naked one was back-filled at 0.01.
        names = [f"S{i:02d}" for i in range(12)]
        fake = _Broker([_position(s, 100.0) for s in names], [_stop(s) for s in names[:6]])
        url = f"sqlite:///{tmp_path / 'bars.db'}"
        repo = TradeLogRepository(engine=create_engine(url, future=True))
        with repo.session() as session:
            for symbol in names:
                rows = [
                    {"t": (TODAY - timedelta(days=d)).isoformat(), "o": c, "h": c * 1.01,
                     "l": c * 0.99, "c": c, "v": 1_000_000}
                    for d in range(BAR_DAYS, 0, -1)
                    for c in [1000.0 if d > 9 else 100.0]
                ]
                write_bars(session, symbol, rows)

        _run(monkeypatch, fake, url)

        assert fake.writes == [], fake.writes

    @pytest.mark.parametrize("factor", [1.05, 1.1, 1.2, 1.24])
    def test_a_mark_high_inside_the_band_moves_no_stop_above_the_last_close(
        self, factor: float, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The doubled-mark case at a smaller factor. Twelve names, half naked,
        # bars at 100 with an ATR of 2: at 1.2x every one of the twelve went
        # to 114.00, above the real last close, against an exit budget of 3.
        names = [f"M{i:02d}" for i in range(12)]
        fake = _Broker(
            [_position(s, 100.0 * factor) for s in names], [_stop(s) for s in names[:6]]
        )
        url = _cache(tmp_path, {s: (101.0, 99.0, 100.0) for s in names})

        _run(monkeypatch, fake, url)

        levels = [w[2]["stop_price"] if w[0] == "replace_order" else w[1]["stop_price"]
                  for w in fake.writes]
        assert len(levels) == 12
        assert set(levels) == {94.0}, levels

    def test_tenth_scale_bars_on_a_volatile_series_move_no_stop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A 3% ATR at 1/10 scale puts every trail 0.9% under a $100 mark.
        names = [f"V{i:02d}" for i in range(12)]
        fake = _Broker(
            [_position(s, 100.0) for s in names], [_stop(s) for s in names[:6]]
        )
        url = _cache(tmp_path, {s: (10.15, 9.85, 10.0) for s in names})

        rc = _run(monkeypatch, fake, url)

        assert rc == 1, "a refused input fails the pass, so daily_run pages on it"
        assert fake.writes == [], fake.writes
