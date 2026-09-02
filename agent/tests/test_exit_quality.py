"""Attribution of realized P&L to the exit path that produced it.

The blended ledger has been quoted as evidence the strategy's exits lose money.
This module is what makes that claim checkable, so its classification rules are
pinned here — especially the ones that decide whether a loss counts against the
agent (a stop firing) or against a one-off operator action (a flatten), since
getting that backwards is how a bug's cleanup gets scored as strategy
performance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tradingagents_us.execution.exit_quality import (
    STRATEGY_CLASSES,
    ExitOrder,
    attribute,
    bucket_by_exit,
    classify_exit,
    strategy_bucket,
)
from tradingagents_us.execution.reconcile import ClosedTrade

T0 = datetime(2026, 8, 1, 14, 30, tzinfo=UTC)


def _order(
    order_type: str = "market",
    client_order_id: str = "tr-AAPL-20260801-SELL",
    *,
    is_leg: bool = False,
) -> ExitOrder:
    return ExitOrder(
        order_id="o1",
        client_order_id=client_order_id,
        order_type=order_type,
        is_leg=is_leg,
    )


def _trade(
    close_activity_id: str, pnl: float, *, symbol: str = "AAPL", hold: float = 3.0
) -> ClosedTrade:
    return ClosedTrade(
        trade_id=f"t-{close_activity_id}",
        symbol=symbol,
        direction="LONG",
        quantity=10.0,
        entry_price=100.0,
        exit_price=100.0 + pnl / 10.0,
        opened_at_utc=T0,
        closed_at_utc=T0 + timedelta(days=hold),
        realized_pnl=pnl,
        realized_pnl_pct=pnl / 1000.0,
        holding_days=hold,
        open_activity_id="f-open",
        close_activity_id=close_activity_id,
    )


class TestClassifyExit:
    def test_stop_leg_is_a_stop(self) -> None:
        assert classify_exit(_order("stop", is_leg=True)) == "stop"

    def test_stop_limit_and_trailing_stop_are_stops_too(self) -> None:
        assert classify_exit(_order("stop_limit", is_leg=True)) == "stop"
        assert classify_exit(_order("trailing_stop", is_leg=True)) == "stop"

    def test_a_stop_is_a_stop_even_at_top_level(self) -> None:
        # A protective level was reached whether or not it was bracketed; the
        # class describes what happened, not how the order was arranged.
        assert classify_exit(_order("stop", is_leg=False)) == "stop"

    def test_limit_leg_is_a_take_profit(self) -> None:
        assert classify_exit(_order("limit", is_leg=True)) == "take_profit"

    def test_order_type_case_and_padding_do_not_change_the_class(self) -> None:
        assert classify_exit(_order("  STOP  ", is_leg=True)) == "stop"

    def test_agent_market_sell_is_a_decision(self) -> None:
        assert classify_exit(_order("market", "tr-META-20260629-SELL")) == "decision_sell"

    def test_market_sell_without_the_agent_prefix_is_a_flatten(self) -> None:
        # The 2026-06-24 accumulation cleanup: Alpaca assigns a bare UUID when
        # the submitter supplies no client id, which is the evidence that the
        # sell did not come through trade.py.
        assert classify_exit(_order("market", "68c3c73e-245b-43f2-95cd-321a6edd402d")) == "flatten"

    def test_top_level_limit_sell_is_not_a_take_profit(self) -> None:
        # A limit sell nobody bracketed is not a take-profit leg firing; without
        # the agent's prefix the only thing established is that it came from
        # outside the agent.
        assert classify_exit(_order("limit", "manual-1", is_leg=False)) == "flatten"

    def test_agent_limit_sell_is_a_decision_not_a_take_profit(self) -> None:
        assert classify_exit(_order("limit", "tr-AAPL-20260801-SELL")) == "decision_sell"

    def test_missing_order_is_unknown_not_folded_into_a_class(self) -> None:
        assert classify_exit(None) == "unknown"

    def test_unexpected_leg_type_is_unknown_not_a_flatten(self) -> None:
        assert classify_exit(_order("bracket_child", "", is_leg=True)) == "unknown"

    def test_blank_client_id_on_a_market_sell_is_a_flatten(self) -> None:
        assert classify_exit(_order("market", "")) == "flatten"


class TestAttribute:
    def test_pairs_each_trade_with_its_closing_order(self) -> None:
        exits = {"f1": _order("stop", is_leg=True), "f2": _order("limit", is_leg=True)}
        rows = attribute([_trade("f1", -50.0), _trade("f2", 30.0)], exits)
        assert [r.exit_class for r in rows] == ["stop", "take_profit"]

    def test_a_fill_with_no_order_is_unknown(self) -> None:
        rows = attribute([_trade("f9", -50.0)], {})
        assert rows[0].exit_class == "unknown"
        assert rows[0].order is None

    def test_empty_ledger_attributes_to_nothing(self) -> None:
        assert attribute([], {}) == []


class TestBuckets:
    def _mixed(self) -> list:
        exits = {
            "f1": _order("stop", is_leg=True),
            "f2": _order("stop", is_leg=True),
            "f3": _order("limit", is_leg=True),
            "f4": _order("market", "68c3c73e-flatten"),
            "f5": _order("market", "68c3c73e-flatten"),
        }
        trades = [
            _trade("f1", -100.0, hold=2.0),
            _trade("f2", -50.0, hold=4.0),
            _trade("f3", 40.0),
            _trade("f4", -500.0),
            _trade("f5", -300.0),
            _trade("f6", -20.0),  # no order at the broker any more
        ]
        return attribute(trades, exits)

    def test_buckets_report_each_path_separately(self) -> None:
        by_class = {b.exit_class: b for b in bucket_by_exit(self._mixed())}
        assert by_class["stop"].trades == 2
        assert by_class["stop"].net_pnl == -150.0
        assert by_class["stop"].avg_pnl == -75.0
        assert by_class["stop"].avg_holding_days == 3.0
        assert by_class["take_profit"].net_pnl == 40.0
        assert by_class["flatten"].net_pnl == -800.0
        assert by_class["unknown"].trades == 1

    def test_rows_come_back_in_report_order(self) -> None:
        assert [b.exit_class for b in bucket_by_exit(self._mixed())] == [
            "take_profit",
            "stop",
            "flatten",
            "unknown",
        ]

    def test_empty_classes_are_omitted_not_reported_as_zero(self) -> None:
        rows = attribute([_trade("f1", 10.0)], {"f1": _order("limit", is_leg=True)})
        assert [b.exit_class for b in bucket_by_exit(rows)] == ["take_profit"]

    def test_strategy_bucket_excludes_flattens_and_unknowns(self) -> None:
        s = strategy_bucket(self._mixed())
        assert s.trades == 3
        assert s.net_pnl == -110.0
        assert s.wins == 1
        assert s.losses == 2

    def test_strategy_bucket_is_empty_when_nothing_is_attributable(self) -> None:
        s = strategy_bucket(attribute([_trade("f9", -20.0)], {}))
        assert s.trades == 0
        assert s.net_pnl == 0.0
        assert s.avg_pnl == 0.0
        assert s.win_rate == 0.0

    def test_a_scratch_counts_as_a_trade_that_did_not_win(self) -> None:
        rows = attribute([_trade("f1", 0.0)], {"f1": _order("stop", is_leg=True)})
        b = bucket_by_exit(rows)[0]
        assert (b.trades, b.wins, b.losses, b.win_rate) == (1, 0, 0, 0.0)

    def test_strategy_classes_are_the_three_agent_paths(self) -> None:
        assert set(STRATEGY_CLASSES) == {"stop", "take_profit", "decision_sell"}


class TestBrokerJoin:
    """The seam: broker records → the mapping the classifier is handed.

    The rules above are arithmetic on a mapping someone else builds. Every one
    of them can be green while the report still says every exit came from
    nowhere — which is exactly what happens if the join skips bracket children,
    since a protective stop is never a top-level order.
    """

    @staticmethod
    def _order(order_id: str, order_type: str, coid: str, legs: tuple = ()):
        from tradingagents_us.dataflows.alpaca_broker import Order

        return Order(
            id=order_id,
            client_order_id=coid,
            symbol="AAPL",
            side="sell" if "sell" in coid else "buy",
            qty=10.0,
            filled_qty=10.0,
            order_type=order_type,
            status="filled",
            submitted_at=T0,
            filled_avg_price=100.0,
            stop_price=95.0 if order_type == "stop" else None,
            limit_price=110.0 if order_type == "limit" else None,
            legs=legs,
        )

    @staticmethod
    def _fill(fid: str, side: str, order_id: str | None):
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

    def test_index_descends_into_bracket_legs(self) -> None:
        from scripts.exit_quality import index_orders

        stop = self._order("leg-stop", "stop", "tr-x-sell")
        tp = self._order("leg-tp", "limit", "tr-x-sell")
        parent = self._order("parent", "market", "tr-x-buy", legs=(stop, tp))

        indexed = index_orders([parent])

        assert set(indexed) == {"parent", "leg-stop", "leg-tp"}
        assert indexed["leg-stop"].is_leg is True
        assert indexed["parent"].is_leg is False
        assert classify_exit(indexed["leg-stop"]) == "stop"
        assert classify_exit(indexed["leg-tp"]) == "take_profit"

    def test_index_carries_the_trigger_price_from_whichever_field_holds_it(self) -> None:
        from scripts.exit_quality import index_orders

        indexed = index_orders(
            [self._order("s", "stop", "tr-x-sell"), self._order("l", "limit", "tr-x-sell")]
        )
        assert indexed["s"].trigger_price == 95.0
        assert indexed["l"].trigger_price == 110.0

    def test_join_keys_on_fills_and_ignores_the_buy_side(self) -> None:
        from scripts.exit_quality import exits_by_fill, index_orders

        indexed = index_orders([self._order("o-sell", "stop", "tr-x-sell")])
        got = exits_by_fill(
            [self._fill("f-buy", "buy", "o-sell"), self._fill("f-sell", "sell", "o-sell")],
            indexed,
        )
        assert set(got) == {"f-sell"}

    def test_a_pruned_or_missing_order_leaves_the_fill_unmapped(self) -> None:
        from scripts.exit_quality import exits_by_fill

        got = exits_by_fill(
            [self._fill("f1", "sell", "gone"), self._fill("f2", "sell", None)], {}
        )
        assert got == {}
        assert classify_exit(got.get("f1")) == "unknown"
