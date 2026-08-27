"""Tests for the settled-cash guard: apply_cash_cap + cash_budget + sizer wiring.

Regression origin: on 2026-08-13 the live paper book sat at -$856.29 cash on
$108k equity. Every cap in position_sizing is a fraction of EQUITY, and the equity
of a fully-invested long book rises with the marks, so each day's buy was sized off
money that was already spent. These tests pin the two holes that allowed it:
a single order overspending, and eleven per-ticker processes each spending the same
dollar because none of them could see the others' pending orders.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradingagents_us.risk.cash_budget import (
    PendingBuy,
    reserved_cash_for_open_buys,
    spendable_cash,
)
from tradingagents_us.risk.circuit_breaker import CircuitBreaker
from tradingagents_us.risk.kill_switch import StaticKillSwitchReader
from tradingagents_us.risk.portfolio_limits import PortfolioContext, PortfolioLimits
from tradingagents_us.risk.position_sizing import apply_cash_cap
from tradingagents_us.risk.sizer import MarketContext, size_from_decision
from tradingagents_us.schemas import AgentDecision, AgentReasoning


def _decision(
    rating: str = "Overweight", entry: float = 100.0, stop: float = 90.0
) -> AgentDecision:
    return AgentDecision(
        ticker="AAPL",
        market="US",
        quote_currency="USD",
        rating=rating,  # type: ignore[arg-type]
        entry_price=entry,
        stop_loss=stop,
        suggested_size_pct=0.05,
        reasoning=[
            AgentReasoning(
                agent="pm", model="m", summary="x", tokens_in=0, tokens_out=0, latency_ms=0
            )
        ],
        timestamp_utc=datetime.now(UTC),
        decision_id="dec-cash",
    )


def _market(price: float = 100.0) -> MarketContext:
    return MarketContext(
        current_price=price,
        rolling_mean=price,
        rolling_std=1.0,
        atr=2.0,
        avg_daily_volume_usd=1_000_000_000.0,
        sector="Tech",
    )


def _ctx(cash: float | None, equity: float = 100_000.0) -> PortfolioContext:
    return PortfolioContext(
        equity=equity,
        existing_position_values_by_ticker={},
        existing_position_values_by_sector={},
        high_correlation_count=0,
        available_cash=cash,
    )


def _cb() -> CircuitBreaker:
    return CircuitBreaker(kill_switch=StaticKillSwitchReader("RUN"))  # type: ignore[arg-type]


class TestApplyCashCap:
    def test_trims_to_what_cash_affords(self) -> None:
        assert apply_cash_cap(suggested_shares=100, price=50.0, available_cash=1_000.0) == 20

    def test_leaves_smaller_size_untouched(self) -> None:
        assert apply_cash_cap(suggested_shares=5, price=50.0, available_cash=1_000.0) == 5

    def test_negative_cash_blocks_entirely(self) -> None:
        # The exact live condition on 2026-08-13. A levered book adds no exposure.
        assert apply_cash_cap(suggested_shares=100, price=50.0, available_cash=-856.29) == 0

    def test_utilization_leaves_dry_powder(self) -> None:
        # 90% of $1,000 = $900 -> 18 shares, not 20.
        assert (
            apply_cash_cap(
                suggested_shares=100, price=50.0, available_cash=1_000.0, cash_utilization=0.9
            )
            == 18
        )

    def test_rounds_down_never_up(self) -> None:
        # $999 / $50 = 19.98 shares. Rounding up would overdraw by one share.
        assert apply_cash_cap(suggested_shares=100, price=50.0, available_cash=999.0) == 19

    def test_nonpositive_price_or_utilization_is_zero(self) -> None:
        assert apply_cash_cap(suggested_shares=10, price=0.0, available_cash=1_000.0) == 0
        assert (
            apply_cash_cap(
                suggested_shares=10, price=50.0, available_cash=1_000.0, cash_utilization=0.0
            )
            == 0
        )


class TestReservedCashForOpenBuys:
    def test_no_pending_reserves_nothing(self) -> None:
        assert reserved_cash_for_open_buys([], lambda _s: 100.0) == 0.0

    def test_limit_order_prices_itself(self) -> None:
        pending = [PendingBuy(symbol="MSFT", unfilled_qty=10, limit_price=250.0)]
        # The quote resolver must not be consulted when a limit price exists.
        assert reserved_cash_for_open_buys(pending, lambda _s: 999.0) == 2_500.0

    def test_market_order_falls_back_to_quote(self) -> None:
        pending = [PendingBuy(symbol="NVDA", unfilled_qty=4, limit_price=None)]
        assert reserved_cash_for_open_buys(pending, lambda _s: 200.0) == 800.0

    def test_reserves_only_the_unfilled_remainder(self) -> None:
        # Partially filled: the filled part is already out of cash and would be
        # double-counted. Caller passes qty - filled_qty.
        pending = [PendingBuy(symbol="V", unfilled_qty=3, limit_price=100.0)]
        assert reserved_cash_for_open_buys(pending, lambda _s: 100.0) == 300.0

    def test_unpriceable_buy_returns_none_not_zero(self) -> None:
        # The critical distinction: an unknown commitment must not read as $0
        # of commitment, which is precisely the over-optimism being fixed.
        pending = [
            PendingBuy(symbol="MSFT", unfilled_qty=10, limit_price=250.0),
            PendingBuy(symbol="XOM", unfilled_qty=5, limit_price=None),
        ]
        assert reserved_cash_for_open_buys(pending, lambda _s: None) is None

    def test_fully_filled_pending_is_skipped_not_fatal(self) -> None:
        # Zero remainder needs no price, so an unresolvable quote is irrelevant.
        pending = [PendingBuy(symbol="XOM", unfilled_qty=0.0, limit_price=None)]
        assert reserved_cash_for_open_buys(pending, lambda _s: None) == 0.0

    def test_sums_across_multiple_pending_buys(self) -> None:
        pending = [
            PendingBuy(symbol="A", unfilled_qty=10, limit_price=10.0),
            PendingBuy(symbol="B", unfilled_qty=2, limit_price=None),
        ]
        assert reserved_cash_for_open_buys(pending, lambda _s: 50.0) == 200.0


class TestSpendableCash:
    def test_subtracts_reservation(self) -> None:
        assert spendable_cash(10_000.0, 2_500.0) == 7_500.0

    def test_never_negative(self) -> None:
        assert spendable_cash(1_000.0, 5_000.0) == 0.0
        assert spendable_cash(-856.29, 0.0) == 0.0

    def test_unknown_reservation_propagates(self) -> None:
        assert spendable_cash(10_000.0, None) is None


class TestSizerCashCapWiring:
    def test_buy_is_trimmed_to_cash(self) -> None:
        # Equity $100k allows a 10% ($10k / 100 shares) position, but only $1,000
        # of cash has settled — the equity-only path would have bought 100.
        order = size_from_decision(
            decision=_decision(),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_ctx(cash=1_000.0),
            circuit_breaker=_cb(),
            method="llm_pct",
            portfolio_limits=PortfolioLimits(max_position_pct=0.20),
        )
        assert order.risk_approved
        assert order.quantity == 10

    def test_negative_cash_rejects_the_buy(self) -> None:
        order = size_from_decision(
            decision=_decision(),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_ctx(cash=-856.29),
            circuit_breaker=_cb(),
            method="llm_pct",
            portfolio_limits=PortfolioLimits(max_position_pct=0.20),
        )
        assert not order.risk_approved
        assert any("cash_cap" in r for r in order.rejection_reasons)

    def test_missing_cash_figure_does_not_reject(self) -> None:
        # available_cash=None means "caller supplied nothing", which must skip the
        # cap rather than behave like cash=0 and refuse every order.
        order = size_from_decision(
            decision=_decision(),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_ctx(cash=None),
            circuit_breaker=_cb(),
            method="llm_pct",
            portfolio_limits=PortfolioLimits(max_position_pct=0.20),
        )
        assert order.risk_approved
        assert not any("cash_cap" in r for r in order.rejection_reasons)

    def test_sell_is_not_cash_capped(self) -> None:
        # A SELL raises cash; gating it on cash would strand a position in a
        # drawdown exactly when it most needs to be exited.
        order = size_from_decision(
            decision=_decision(rating="Sell"),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_ctx(cash=-856.29),
            circuit_breaker=_cb(),
            method="llm_pct",
            portfolio_limits=PortfolioLimits(max_position_pct=0.20),
        )
        assert order.side == "SELL"
        assert not any("cash_cap" in r for r in order.rejection_reasons)

    def test_pending_buys_shrink_the_budget_for_the_next_ticker(self) -> None:
        # End-to-end shape of the multi-process bug: $10k cash, ticker #1's order
        # for $9.5k still pending. Ticker #2 must see $500, not $10k.
        pending = [PendingBuy(symbol="MSFT", unfilled_qty=95, limit_price=100.0)]
        reserved = reserved_cash_for_open_buys(pending, lambda _s: 100.0)
        budget = spendable_cash(10_000.0, reserved)
        order = size_from_decision(
            decision=_decision(),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_ctx(cash=budget),
            circuit_breaker=_cb(),
            method="llm_pct",
            portfolio_limits=PortfolioLimits(max_position_pct=0.20),
        )
        assert order.quantity == 5
