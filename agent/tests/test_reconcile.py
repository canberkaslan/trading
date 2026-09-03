"""FIFO round-trip matching + realized-P&L stats.

A P&L ledger that is quietly wrong is worse than no ledger: it would be used to
judge whether the agent has an edge, and every downstream decision (go-live,
reflection memory, sizing) inherits the error. So the matcher is pure and every
rule it claims — FIFO ordering, shorts, flips, partials, idempotent ids — is
pinned against a hand-built fill stream here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tradingagents_us.execution.reconcile import (
    ClosedTrade,
    Fill,
    compute_stats,
    reconcile_fills,
)

T0 = datetime(2026, 8, 1, 14, 30, tzinfo=UTC)


def _fill(
    fid: str, symbol: str, side: str, qty: float, price: float, day_offset: float = 0.0
) -> Fill:
    return Fill(
        id=fid,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        transaction_time=T0 + timedelta(days=day_offset),
    )


class TestLongRoundTrips:
    def test_simple_buy_then_sell_realizes_pnl(self) -> None:
        r = reconcile_fills([
            _fill("f1", "AAPL", "buy", 10, 100.0),
            _fill("f2", "AAPL", "sell", 10, 110.0, day_offset=3),
        ])

        assert len(r.closed) == 1
        t = r.closed[0]
        assert (t.symbol, t.direction, t.quantity) == ("AAPL", "LONG", 10)
        assert (t.entry_price, t.exit_price) == (100.0, 110.0)
        assert t.realized_pnl == 100.0
        assert t.realized_pnl_pct == 0.1
        assert t.holding_days == 3.0
        assert r.open_lots == []

    def test_losing_trade_is_negative(self) -> None:
        r = reconcile_fills([
            _fill("f1", "AAPL", "buy", 5, 200.0),
            _fill("f2", "AAPL", "sell", 5, 180.0, day_offset=1),
        ])
        assert r.closed[0].realized_pnl == -100.0
        assert r.closed[0].realized_pnl_pct == -0.1

    def test_unclosed_buy_produces_no_trade_but_an_open_lot(self) -> None:
        # An open position is not a result. Counting it would let a paper gain
        # inflate the win rate before it is taken.
        r = reconcile_fills([_fill("f1", "MSFT", "buy", 8, 400.0)])

        assert r.closed == []
        assert len(r.open_lots) == 1
        assert (r.open_lots[0].symbol, r.open_lots[0].quantity) == ("MSFT", 8)
        assert r.open_lots[0].direction == "LONG"

    def test_partial_exit_leaves_the_rest_open(self) -> None:
        r = reconcile_fills([
            _fill("f1", "AAPL", "buy", 10, 100.0),
            _fill("f2", "AAPL", "sell", 4, 120.0, day_offset=1),
        ])

        assert len(r.closed) == 1
        assert r.closed[0].quantity == 4
        assert r.closed[0].realized_pnl == 80.0
        assert [(o.quantity, o.price) for o in r.open_lots] == [(6, 100.0)]


class TestFifoOrdering:
    def test_oldest_lot_closes_first(self) -> None:
        # Two entries at different prices; one exit. FIFO must pair against the
        # $100 lot. LIFO or average-cost would report a different P&L, so this
        # is the test that actually pins the accounting method.
        r = reconcile_fills([
            _fill("f1", "AAPL", "buy", 10, 100.0),
            _fill("f2", "AAPL", "buy", 10, 150.0, day_offset=1),
            _fill("f3", "AAPL", "sell", 10, 160.0, day_offset=2),
        ])

        assert len(r.closed) == 1
        assert r.closed[0].entry_price == 100.0
        assert r.closed[0].realized_pnl == 600.0
        assert [(o.quantity, o.price) for o in r.open_lots] == [(10, 150.0)]

    def test_one_exit_spanning_two_lots_splits_into_two_trades(self) -> None:
        r = reconcile_fills([
            _fill("f1", "AAPL", "buy", 10, 100.0),
            _fill("f2", "AAPL", "buy", 5, 200.0, day_offset=1),
            _fill("f3", "AAPL", "sell", 15, 210.0, day_offset=2),
        ])

        assert len(r.closed) == 2
        assert [(t.quantity, t.entry_price, t.realized_pnl) for t in r.closed] == [
            (10, 100.0, 1100.0),
            (5, 200.0, 50.0),
        ]
        assert r.open_lots == []

    def test_same_timestamp_partials_order_by_activity_id(self) -> None:
        # Alpaca stamps same-second partials identically; without the id
        # tiebreak the lot order (and thus the ledger) would be unstable.
        fills = [
            _fill("f2", "AAPL", "buy", 5, 200.0),
            _fill("f1", "AAPL", "buy", 5, 100.0),
            _fill("f3", "AAPL", "sell", 5, 300.0, day_offset=1),
        ]
        assert reconcile_fills(fills).closed[0].entry_price == 100.0
        # Input order must not matter — the sort is on (time, id), not arrival.
        assert reconcile_fills(list(reversed(fills))).closed[0].entry_price == 100.0


class TestShortsAndFlips:
    def test_sell_first_opens_a_short_and_buy_covers_it(self) -> None:
        r = reconcile_fills([
            _fill("f1", "TSLA", "sell", 10, 300.0),
            _fill("f2", "TSLA", "buy", 10, 280.0, day_offset=2),
        ])

        assert len(r.closed) == 1
        t = r.closed[0]
        assert t.direction == "SHORT"
        # Short profits when the cover prints BELOW the entry.
        assert t.realized_pnl == 200.0
        assert t.realized_pnl_pct == 200.0 / 3000.0

    def test_short_that_moves_against_you_loses(self) -> None:
        r = reconcile_fills([
            _fill("f1", "TSLA", "sell", 10, 300.0),
            _fill("f2", "TSLA", "buy", 10, 330.0, day_offset=1),
        ])
        assert r.closed[0].realized_pnl == -300.0

    def test_fill_crossing_zero_closes_then_opens(self) -> None:
        # Long 10, sell 30: closes the 10 and opens a 20-share short. One fill,
        # two effects — collapsing it into a single 30-share exit would invent
        # 20 shares that were never owned.
        r = reconcile_fills([
            _fill("f1", "NVDA", "buy", 10, 100.0),
            _fill("f2", "NVDA", "sell", 30, 120.0, day_offset=1),
        ])

        assert len(r.closed) == 1
        assert (r.closed[0].direction, r.closed[0].quantity) == ("LONG", 10)
        assert r.closed[0].realized_pnl == 200.0
        assert len(r.open_lots) == 1
        assert (r.open_lots[0].direction, r.open_lots[0].quantity) == ("SHORT", 20)


class TestMultiSymbolAndIdempotency:
    def test_symbols_are_matched_independently(self) -> None:
        r = reconcile_fills([
            _fill("f1", "AAPL", "buy", 10, 100.0),
            _fill("f2", "MSFT", "buy", 10, 400.0),
            _fill("f3", "AAPL", "sell", 10, 110.0, day_offset=1),
            _fill("f4", "MSFT", "sell", 10, 390.0, day_offset=1),
        ])

        assert {t.symbol: t.realized_pnl for t in r.closed} == {
            "AAPL": 100.0,
            "MSFT": -100.0,
        }

    def test_trade_ids_are_stable_across_replays(self) -> None:
        # The reconciler replays the whole fill history every run and upserts
        # by trade_id; unstable ids would duplicate the ledger hourly.
        fills = [
            _fill("f1", "AAPL", "buy", 10, 100.0),
            _fill("f2", "AAPL", "sell", 10, 110.0, day_offset=1),
        ]
        assert [t.trade_id for t in reconcile_fills(fills).closed] == [
            t.trade_id for t in reconcile_fills(fills).closed
        ]

    def test_trade_ids_are_unique_within_a_replay(self) -> None:
        r = reconcile_fills([
            _fill("f1", "AAPL", "buy", 10, 100.0),
            _fill("f2", "AAPL", "buy", 5, 200.0, day_offset=1),
            _fill("f3", "AAPL", "sell", 15, 210.0, day_offset=2),
        ])
        assert len({t.trade_id for t in r.closed}) == len(r.closed)

    def test_fractional_residue_does_not_leave_a_ghost_lot(self) -> None:
        # Fractional shares subtract into float noise; a 1e-13 residual must
        # not survive as an open lot forever.
        r = reconcile_fills([
            _fill("f1", "AAPL", "buy", 0.1, 100.0),
            _fill("f2", "AAPL", "buy", 0.2, 100.0),
            _fill("f3", "AAPL", "sell", 0.3, 110.0, day_offset=1),
        ])
        assert r.open_lots == []
        assert len(r.closed) == 2

    def test_empty_stream_is_empty(self) -> None:
        r = reconcile_fills([])
        assert r.closed == [] and r.open_lots == []


class TestStats:
    def _closed(self, pnls: list[float]):
        fills: list[Fill] = []
        for i, pnl in enumerate(pnls):
            fills.append(_fill(f"b{i}", f"SYM{i}", "buy", 10, 100.0, day_offset=i))
            fills.append(
                _fill(f"s{i}", f"SYM{i}", "sell", 10, 100.0 + pnl / 10, day_offset=i + 2)
            )
        return reconcile_fills(fills).closed

    def test_win_rate_counts_scratches_in_the_denominator(self) -> None:
        # 2 wins, 1 loss, 1 scratch → 50%, not 66.7%. Dropping scratches from
        # the denominator is the classic way a mediocre record reads well.
        s = compute_stats(self._closed([100.0, 200.0, -50.0, 0.0]))

        assert (s.trades, s.wins, s.losses, s.scratches) == (4, 2, 1, 1)
        assert s.win_rate == 0.5

    def test_totals_and_averages(self) -> None:
        s = compute_stats(self._closed([100.0, 200.0, -50.0]))

        assert s.gross_profit == 300.0
        assert s.gross_loss == 50.0  # reported positive
        assert s.net_pnl == 250.0
        assert s.avg_win == 150.0
        assert s.avg_loss == 50.0
        assert s.profit_factor == 6.0
        assert s.best_trade == 200.0
        assert s.worst_trade == -50.0

    def test_expectancy_equals_the_textbook_identity(self) -> None:
        trades = self._closed([100.0, 200.0, -50.0, 0.0])
        s = compute_stats(trades)

        loss_rate = s.losses / s.trades
        assert s.expectancy == s.net_pnl / s.trades
        assert abs(s.expectancy - (s.win_rate * s.avg_win - loss_rate * s.avg_loss)) < 1e-9

    def test_profit_factor_is_none_not_infinity_without_losses(self) -> None:
        s = compute_stats(self._closed([100.0, 50.0]))
        assert s.profit_factor is None
        assert s.gross_loss == 0.0

    def test_empty_stats_are_zeroed_not_crashed(self) -> None:
        s = compute_stats([])
        assert (s.trades, s.win_rate, s.net_pnl, s.expectancy) == (0, 0.0, 0.0, 0.0)
        assert s.profit_factor is None

    def test_avg_holding_days(self) -> None:
        s = compute_stats(self._closed([100.0, -100.0]))
        assert s.avg_holding_days == 2.0


class TestExitAttributionAtWriteTime:
    """`attribute_exits` — the class the ledger stores, decided against orders.

    Classification needs the broker's ORDER history, which Alpaca prunes; the
    fill feed is permanent. So the interesting cases are all about the gap:
    what gets stored when the closing order is no longer there, and what must
    NOT get stored (a `"unknown"` that would read as evidence we looked and
    found nothing, when the caller may simply never have asked).
    """

    def _order(self, oid: str, order_type: str, client_id: str, legs: tuple = ()) -> object:
        from tradingagents_us.dataflows.alpaca_broker import Order

        return Order(
            id=oid,
            client_order_id=client_id,
            symbol="AAPL",
            side="sell",
            qty=10.0,
            filled_qty=10.0,
            order_type=order_type,
            status="filled",
            submitted_at=T0,
            filled_avg_price=100.0,
            stop_price=95.0 if order_type in {"stop", "stop_limit"} else None,
            limit_price=110.0 if order_type == "limit" else None,
            legs=tuple(legs),
        )

    def _fill(self, fid: str, side: str, order_id: str | None) -> object:
        from tradingagents_us.dataflows.alpaca_broker import FillActivity

        return FillActivity(
            id=fid,
            symbol="AAPL",
            side=side,
            qty=10.0,
            price=100.0,
            transaction_time=T0,
            order_id=order_id,
        )

    def _closed(self, tid: str, close_activity_id: str):
        return ClosedTrade(
            trade_id=tid,
            symbol="AAPL",
            direction="LONG",
            quantity=10.0,
            entry_price=100.0,
            exit_price=110.0,
            opened_at_utc=T0,
            closed_at_utc=T0 + timedelta(days=2),
            realized_pnl=100.0,
            realized_pnl_pct=0.1,
            holding_days=2.0,
            open_activity_id="f-open",
            close_activity_id=close_activity_id,
        )

    def test_maps_each_trade_to_the_class_of_its_closing_order(self) -> None:
        from scripts.reconcile import attribute_exits

        stop = self._order("leg-stop", "stop", "tr-AAPL-SELL")
        parent = self._order("parent", "market", "tr-AAPL-BUY", legs=(stop,))
        flatten = self._order("bare", "market", "68c3c73e-uuid")

        got = attribute_exits(
            [self._closed("t1", "f1"), self._closed("t2", "f2")],
            [self._fill("f1", "sell", "leg-stop"), self._fill("f2", "sell", "bare")],
            [parent, flatten],
        )

        assert got == {"t1": "stop", "t2": "flatten"}

    def test_a_pruned_order_is_absent_rather_than_stored_as_unknown(self) -> None:
        # The seam. `attribute` classifies a missing order as "unknown", and
        # writing that would assert we checked the broker — but this same
        # empty result is what an unreadable order feed produces. Leaving the
        # trade out lets the repository preserve whatever it already knew.
        from scripts.reconcile import attribute_exits

        got = attribute_exits(
            [self._closed("t1", "f1")], [self._fill("f1", "sell", "gone")], []
        )

        assert got == {}

    def test_an_unreadable_order_feed_attributes_nothing_at_all(self) -> None:
        from scripts.reconcile import attribute_exits

        assert attribute_exits([self._closed("t1", "f1")], [], []) == {}
