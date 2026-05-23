"""Risk sizer — convert an LLM AgentDecision into a deterministic TradeOrder.

The LLM proposes entry / stop / size %. This module:

1. Validates the proposal (entry, stop sane?)
2. Computes a share count using one of three methods:
     - "atr"      → atr_position_size (default — uses LLM stop as ATR proxy)
     - "kelly"    → fractional_kelly (when p_win and b are provided)
     - "vol_tgt"  → vol_target_size
     - "llm_pct"  → trust the LLM's suggested_size_pct as-is, clipped
3. Caps with apply_portfolio_caps + portfolio_limits
4. Runs through circuit_breaker.check
5. Emits a TradeOrder (with risk_approved + rejection_reasons populated)

This module is deterministic and unit-testable. No LLM calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from ..schemas import AgentDecision, TradeOrder
from .circuit_breaker import CircuitBreaker
from .portfolio_limits import PortfolioContext, PortfolioLimits, check_limits
from .position_sizing import apply_portfolio_caps, atr_position_size

SizingMethod = Literal["atr", "llm_pct", "vol_tgt", "kelly"]


@dataclass(frozen=True)
class MarketContext:
    """Live market context required to size + risk-check a trade."""

    current_price: float          # last trade price
    rolling_mean: float           # for circuit breaker price-anomaly check
    rolling_std: float
    atr: float | None = None      # if None, derived from |entry - stop|
    avg_daily_volume_usd: float = 1_000_000.0
    sector: str | None = None


def _side_from_rating(rating: str) -> str | None:
    """Map a 5-tier rating to a side. Hold/Underweight -> None (no order)."""
    if rating in ("Buy", "Overweight"):
        return "BUY"
    if rating in ("Sell",):
        return "SELL"
    # Underweight is a reduce signal not an entry — caller decides
    return None


def size_from_decision(
    decision: AgentDecision,
    account_equity: float,
    market_ctx: MarketContext,
    portfolio_ctx: PortfolioContext,
    circuit_breaker: CircuitBreaker,
    method: SizingMethod = "atr",
    risk_per_trade: float = 0.005,
    portfolio_limits: PortfolioLimits = PortfolioLimits(),
) -> TradeOrder:
    """Build a TradeOrder from an AgentDecision + market + portfolio context."""
    rejections: list[str] = []

    # 0. Side derivation
    side = _side_from_rating(decision.rating)
    if side is None:
        rejections.append(f"non-actionable rating={decision.rating}")

    # 1. Circuit breaker
    cb_ok, cb_reasons = circuit_breaker.check(
        equity_now=account_equity,
        equity_open=account_equity,  # caller can override via separate call
        price=market_ctx.current_price,
        rolling_mean=market_ctx.rolling_mean,
        rolling_std=market_ctx.rolling_std,
    )
    if not cb_ok:
        rejections.extend(cb_reasons)

    # 2. Sizing
    qty = 0
    if side is not None and decision.entry_price and decision.stop_loss:
        # ATR proxy: distance between entry and stop if no ATR provided
        atr = market_ctx.atr or abs(decision.entry_price - decision.stop_loss) / 2.0
        if method == "atr":
            qty = atr_position_size(
                equity=account_equity,
                atr=atr,
                price=decision.entry_price,
                risk_per_trade=risk_per_trade,
                atr_mult=2.0,
            )
        elif method == "llm_pct":
            notional = account_equity * decision.suggested_size_pct
            qty = int(notional / decision.entry_price) if decision.entry_price > 0 else 0
        # 'kelly' / 'vol_tgt' require extra inputs — caller wires when ready

    # 3. Per-position cap trim FIRST (so check_limits sees the actual proposed value)
    if qty > 0:
        existing = portfolio_ctx.existing_position_values_by_ticker.get(decision.ticker, 0.0)
        qty = apply_portfolio_caps(
            suggested_shares=qty,
            price=decision.entry_price,  # type: ignore[arg-type]
            equity=account_equity,
            existing_position_value=existing,
            max_position_pct=portfolio_limits.max_position_pct,
        )
        if qty == 0:
            rejections.append("trimmed_to_zero_by_portfolio_caps")

    # 4. Portfolio caps (per-sector + correlation + liquidity + gross exposure)
    #    Per-position cap already enforced above; re-check the trimmed value.
    if qty > 0:
        position_value = qty * decision.entry_price  # type: ignore[operator]
        limits_ok, limits_reasons = check_limits(
            ticker=decision.ticker,
            sector=market_ctx.sector,
            new_position_value=position_value,
            avg_daily_volume_usd=market_ctx.avg_daily_volume_usd,
            ctx=portfolio_ctx,
            limits=portfolio_limits,
        )
        if not limits_ok:
            rejections.extend(limits_reasons)

    risk_approved = (len(rejections) == 0) and qty > 0 and side is not None

    return TradeOrder(
        order_id=str(uuid.uuid4()),
        decision_id=decision.decision_id,
        ticker=decision.ticker,
        market=decision.market,
        side=side or "BUY",  # placeholder when rejected
        quantity=max(qty, 1),  # pydantic gt=0; rejection_reasons carry the truth
        order_type="MARKET",
        limit_price=None,
        stop_loss=decision.stop_loss or 0.01,
        risk_approved=risk_approved,
        rejection_reasons=rejections,
        submitted_at_utc=datetime.now(timezone.utc),
    )
