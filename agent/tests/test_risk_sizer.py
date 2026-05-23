"""Tests for risk.sizer — converting AgentDecision -> TradeOrder."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradingagents_us.risk.circuit_breaker import CircuitBreaker
from tradingagents_us.risk.kill_switch import StaticKillSwitchReader
from tradingagents_us.risk.portfolio_limits import PortfolioContext, PortfolioLimits
from tradingagents_us.risk.sizer import MarketContext, size_from_decision
from tradingagents_us.schemas import AgentDecision, AgentReasoning


def _decision(rating: str, entry: float | None = 271.0, stop: float | None = 229.0, size: float = 0.045) -> AgentDecision:
    return AgentDecision(
        ticker="AAPL",
        market="US",
        quote_currency="USD",
        rating=rating,  # type: ignore[arg-type]
        entry_price=entry,
        stop_loss=stop,
        suggested_size_pct=size,
        reasoning=[AgentReasoning(agent="pm", model="claude-opus-4-7", summary="x", tokens_in=0, tokens_out=0, latency_ms=0)],
        timestamp_utc=datetime.now(timezone.utc),
        decision_id="dec-1",
    )


def _market(price: float = 271.0) -> MarketContext:
    return MarketContext(
        current_price=price,
        rolling_mean=271.0,
        rolling_std=2.0,
        atr=4.0,
        avg_daily_volume_usd=10_000_000.0,
        sector="Tech",
    )


def _portfolio_empty() -> PortfolioContext:
    return PortfolioContext(
        equity=100_000.0,
        existing_position_values_by_ticker={},
        existing_position_values_by_sector={},
        high_correlation_count=0,
    )


def _cb() -> CircuitBreaker:
    return CircuitBreaker(kill_switch=StaticKillSwitchReader("RUN"))  # type: ignore[arg-type]


class TestSiderHappyPath:
    def test_overweight_produces_approved_buy_order(self) -> None:
        # ATR sizing would yield 62 shares ($16.8k) — exceeds default 10% cap on
        # $100k equity. With 20% cap the trim keeps ~36 shares, all reasons clear.
        order = size_from_decision(
            decision=_decision("Overweight"),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_portfolio_empty(),
            circuit_breaker=_cb(),
            method="atr",
            portfolio_limits=PortfolioLimits(max_position_pct=0.20),
        )
        assert order.risk_approved
        assert order.side == "BUY"
        assert order.quantity > 0
        assert order.rejection_reasons == []

    def test_buy_rating_also_actionable(self) -> None:
        order = size_from_decision(
            decision=_decision("Buy"),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_portfolio_empty(),
            circuit_breaker=_cb(),
            portfolio_limits=PortfolioLimits(max_position_pct=0.20),
        )
        assert order.risk_approved
        assert order.side == "BUY"


class TestRejections:
    def test_hold_rating_not_actionable(self) -> None:
        order = size_from_decision(
            decision=_decision("Hold"),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_portfolio_empty(),
            circuit_breaker=_cb(),
        )
        assert not order.risk_approved
        assert any("non-actionable" in r for r in order.rejection_reasons)

    def test_kill_switch_blocks(self) -> None:
        cb = CircuitBreaker(kill_switch=StaticKillSwitchReader("PAUSE_NEW"))  # type: ignore[arg-type]
        order = size_from_decision(
            decision=_decision("Overweight"),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_portfolio_empty(),
            circuit_breaker=cb,
        )
        assert not order.risk_approved
        assert any("kill_switch" in r for r in order.rejection_reasons)

    def test_position_cap_trims_to_zero_when_existing_full(self) -> None:
        # Already holds 10% of $100k -> at the cap; no room for more
        ctx = PortfolioContext(
            equity=100_000.0,
            existing_position_values_by_ticker={"AAPL": 10_000.0},
            existing_position_values_by_sector={"Tech": 10_000.0},
            high_correlation_count=0,
        )
        order = size_from_decision(
            decision=_decision("Overweight"),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=ctx,
            circuit_breaker=_cb(),
            portfolio_limits=PortfolioLimits(max_position_pct=0.10),
        )
        assert not order.risk_approved
        assert any("position_pct" in r or "trimmed_to_zero" in r for r in order.rejection_reasons)

    def test_no_entry_price_no_quantity(self) -> None:
        order = size_from_decision(
            decision=_decision("Buy", entry=None, stop=None),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_portfolio_empty(),
            circuit_breaker=_cb(),
        )
        assert not order.risk_approved
        assert order.quantity == 1  # placeholder from gt=0 constraint


class TestATRSizing:
    def test_atr_size_respects_risk_per_trade(self) -> None:
        # equity 100k, risk 0.5% = $500. atr=4 (from market_ctx), atr_mult=2 -> stop_distance=8.
        # shares = 500 / 8 = 62. With default 10% position cap (=$10k / $271 = 36),
        # would be trimmed. Raise cap to 20% so ATR formula dominates.
        order = size_from_decision(
            decision=_decision("Buy"),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_portfolio_empty(),
            circuit_breaker=_cb(),
            method="atr",
            risk_per_trade=0.005,
            portfolio_limits=PortfolioLimits(max_position_pct=0.20),
        )
        assert 60 <= order.quantity <= 64


class TestLLMPctSizing:
    def test_llm_pct_method_honors_suggested_size(self) -> None:
        # 4.5% of $100k = $4500 at $271 = 16 shares
        order = size_from_decision(
            decision=_decision("Buy", size=0.045),
            account_equity=100_000.0,
            market_ctx=_market(),
            portfolio_ctx=_portfolio_empty(),
            circuit_breaker=_cb(),
            method="llm_pct",
        )
        assert 14 <= order.quantity <= 17
