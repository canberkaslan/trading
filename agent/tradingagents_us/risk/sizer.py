"""Risk sizer — convert an LLM AgentDecision into a deterministic TradeOrder.

The LLM proposes entry / stop / size %. This module:

1. Validates the proposal (entry, stop sane?)
2. Computes a share count using one of three methods:
     - "atr"      → risk_based_size (default — risks a fixed % of equity at the LLM stop)
     - "kelly"    → fractional_kelly (when p_win and b are provided)
     - "vol_tgt"  → vol_target_size
     - "llm_pct"  → trust the LLM's suggested_size_pct as-is, clipped
3. Caps with position_cap_headroom (single name) + apply_cash_cap + portfolio_limits
4. Runs through circuit_breaker.check
5. Emits a TradeOrder (with risk_approved + rejection_reasons populated)

This module is deterministic and unit-testable. No LLM calls.

Which controls apply to which side
----------------------------------
Nearly every control here was written for entries and then applied to both
sides, which inverts each one: an exit is refused by the machinery meant to
stop the book getting too big, on the position most in need of exiting. The
audit, settled once so the branches below can just point at it:

    control                    BUY   SELL   why
    ------------------------------------------------------------------------
    single-name cap            yes   no     headroom to ADD; zero at the cap
    sector cap                 yes   no     a sell relieves concentration
    gross exposure             yes   no     a sell reduces gross
    correlation cap            yes   no     a sell uncrowds the book
    liquidity floor (ADV)      yes   no     else a thin position is permanent
    cash cap                   yes   no     a sell raises cash, never spends it
    kill switch PAUSE_NEW      yes   no     "no new entries; manage existing"
    daily drawdown halt        yes   no     stranding in a drawdown
    consecutive-loss halt      yes   no     a streak is no reason to hold on
    price anomaly (z-score)    yes   yes    a bad print misprices either side
    kill switch FLATTEN_ALL    yes   yes    the flatten owns the book

The anomaly row is the tell: it is the only one that is not about how much risk
to carry. It asks whether the number in front of us is real, and that question
has no side. Everything above it bounds risk-taking, and a sell takes none — it
is bounded by the holding instead (see `_size_for_side`). FLATTEN_ALL is the
exception on purpose: execution/flatten.py is already selling every lot, and a
second seller is how one gets sold twice. The kill-switch, drawdown and
loss-streak rows live in circuit_breaker.py.

Opening those three rows to sells lets a sell through on exactly the days
stops fire, and it is sized off a holding read before the council ran. So the
executor reads the holding again just before it submits, and refuses a sell
the book no longer covers as `position_changed_since_sizing`: one that lands
after its lot's stop has filled is a short with nothing protecting it.

The anomaly gate needs ten cached closes and is skipped without them. A sell
whose price is missing altogether is refused here as `no_reference_price`.

There is no API-error-rate row, on purpose. The breaker has that gate, but it
cannot fire: nothing calls `record_api_call`, and each ticker's trade.py is its
own process making a handful of calls, never the 21 the gate needs before it
measures anything. It protects neither side, so it is not counted as a control.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from ..schemas import AgentDecision, TradeOrder
from .circuit_breaker import CircuitBreaker
from .portfolio_limits import PortfolioContext, PortfolioLimits, check_limits
from .position_sizing import (
    apply_cash_cap,
    describe_position_cap_trim,
    position_cap_headroom,
    risk_based_size,
)

SizingMethod = Literal["atr", "llm_pct", "vol_tgt", "kelly"]


@dataclass(frozen=True)
class MarketContext:
    """Live market context required to size + risk-check a trade."""

    current_price: float          # last trade price
    rolling_mean: float           # for circuit breaker price-anomaly check
    rolling_std: float
    atr: float | None = None      # if None, derived from |entry - stop|
    avg_daily_volume_usd: float = 1_000_000.0
    sector: str = "Unknown"       # GICS sector or "Unknown" for unmapped tickers


def _side_from_rating(rating: str) -> str | None:
    """Map a 5-tier rating to a side. Hold/Underweight -> None (no order)."""
    if rating in ("Buy", "Overweight"):
        return "BUY"
    if rating in ("Sell",):
        return "SELL"
    # Underweight is a reduce signal not an entry — caller decides
    return None


def _size_for_side(
    *,
    side: str | None,
    decision: AgentDecision,
    account_equity: float,
    method: SizingMethod,
    risk_per_trade: float,
    held_quantity: float,
) -> int:
    """Share count before any cap is applied.

    The two sides answer different questions. A buy asks how much to put at
    stake, so it is sized from the risk budget and the distance to its stop. A
    sell closes a position, which puts nothing at stake — it takes something
    off — so it is bounded by the holding. Sizing a sell off the budget is what
    let a 30-share position produce an approved 70-share order: 40 shares short,
    on which the executor attaches no protective leg at all.
    """
    if side == "SELL":
        return int(max(0.0, held_quantity))
    if side != "BUY" or not (decision.entry_price and decision.stop_loss):
        return 0
    if method == "atr":
        # The stop that will ride on the order is known here, so size off the
        # real distance to it rather than an ATR standing in for that distance.
        # The proxy is what let the intended 0.5% become 1.25%.
        return risk_based_size(
            equity=account_equity,
            entry=decision.entry_price,
            stop=decision.stop_loss,
            risk_per_trade=risk_per_trade,
        )
    if method == "llm_pct":
        notional = account_equity * decision.suggested_size_pct
        return int(notional / decision.entry_price) if decision.entry_price > 0 else 0
    # 'kelly' / 'vol_tgt' require extra inputs — caller wires when ready
    return 0


def size_from_decision(
    decision: AgentDecision,
    account_equity: float,
    market_ctx: MarketContext,
    portfolio_ctx: PortfolioContext,
    circuit_breaker: CircuitBreaker,
    method: SizingMethod = "atr",
    risk_per_trade: float = 0.005,
    portfolio_limits: PortfolioLimits = PortfolioLimits(),
    session_open_equity: float | None = None,
    held_quantity: float = 0.0,
) -> TradeOrder:
    """Build a TradeOrder from an AgentDecision + market + portfolio context.

    `session_open_equity` is the equity the session opened at (Alpaca reports it
    as the previous close). It is what the circuit breaker measures the day's
    drawdown against; omit it and that one check is skipped rather than run
    against a ratio of 1.0, which can never trip.

    `held_quantity` is how many shares of this ticker the account actually holds.
    It bounds a sell: this system is long-only — no agent produces a short thesis
    and the executor attaches a protective leg to buys only — so a sell closes a
    position and can never open one. Leave it at 0 and a sell is refused rather
    than sized off the risk budget, which is how an approved order once came out
    larger than the holding it was meant to close.
    """
    rejections: list[str] = []

    # 0. Side derivation
    side = _side_from_rating(decision.rating)
    if side is None:
        rejections.append(f"non-actionable rating={decision.rating}")

    # 1. Circuit breaker
    #    equity_open must be the session's OPENING equity. Passing account_equity
    #    for both is what silently disabled the daily-drawdown halt: the ratio is
    #    then 1.0 and the computed drawdown is always 0.0, so the threshold could
    #    never be crossed. Callers that cannot supply an opening figure leave it
    #    None, and the breaker skips that check rather than pretending to run it.
    #
    #    The side goes in too. The breaker's risk-taking gates — kill switch
    #    PAUSE_NEW, drawdown halt, losing streak — are entry-side, for the same
    #    reason the exposure caps below are; see circuit_breaker.py. A None side (a
    #    non-actionable rating) is already rejected above, and defaults to the
    #    strict BUY reading here.
    cb_ok, cb_reasons = circuit_breaker.check(
        equity_now=account_equity,
        equity_open=session_open_equity if session_open_equity else 0.0,
        price=market_ctx.current_price,
        rolling_mean=market_ctx.rolling_mean,
        rolling_std=market_ctx.rolling_std,
        side=side or "BUY",
    )
    if not cb_ok:
        rejections.extend(cb_reasons)

    # 2. Sizing
    qty = _size_for_side(
        side=side,
        decision=decision,
        account_equity=account_equity,
        method=method,
        risk_per_trade=risk_per_trade,
        held_quantity=held_quantity,
    )
    if side == "SELL" and qty == 0:
        rejections.append("nothing_held_to_sell")
    # A sell priced at nothing. trade.py passes 0.0 when no entry, no Polygon
    # print and no broker mark exist, so the refusal still lands as a row; with
    # too few cached closes the anomaly gate is skipped as well, and without this
    # the whole holding was approved as a market sell nothing had priced or
    # checked. `not > 0` so a NaN is refused too.
    if side == "SELL" and not market_ctx.current_price > 0:
        rejections.append("no_reference_price")

    # 3. Per-position cap trim FIRST (so check_limits sees the actual proposed value)
    #    BUY only. Every cap from here down bounds how much EXPOSURE may be taken
    #    on, so applying them to a sell inverts them: the headroom calculation
    #    returns zero for a name already at its cap, which trimmed the exit to
    #    zero on precisely the position that most needed exiting. The cash cap
    #    below already reasoned this through for its own case; the same holds for
    #    all of them.
    if qty > 0 and side == "BUY":
        existing = portfolio_ctx.existing_position_values_by_ticker.get(decision.ticker, 0.0)
        headroom = position_cap_headroom(
            price=decision.entry_price,  # type: ignore[arg-type]
            equity=account_equity,
            existing_position_value=existing,
            max_position_pct=portfolio_limits.max_position_pct,
        )
        qty = min(qty, headroom.max_new_shares)
        if qty == 0:
            # The bucket key stays byte-identical: the actionability report groups
            # on the reason with its trailing parenthetical stripped, and the inert
            # alert re-pages when the dominant blocker *changes*. Renaming this key
            # to carry the detail would have read as a new cause and paged about a
            # freeze that had not moved.
            detail = describe_position_cap_trim(
                decision.ticker,
                decision.entry_price,  # type: ignore[arg-type]
                headroom,
            )
            rejections.append(f"trimmed_to_zero_by_portfolio_caps ({detail})")

    # 3b. Cash cap — only caps a BUY, which is the only side that *spends* cash.
    #     Every cap above is a fraction of equity, and equity of a fully-invested
    #     long book keeps rising with the marks, so equity-only sizing walks the
    #     account into margin one buy at a time. Skipped when the caller supplies
    #     no cash figure: a missing input must not silently reject every order.
    if qty > 0 and side == "BUY" and portfolio_ctx.available_cash is not None:
        qty = apply_cash_cap(
            suggested_shares=qty,
            price=decision.entry_price,  # type: ignore[arg-type]
            available_cash=portfolio_ctx.available_cash,
            cash_utilization=portfolio_limits.max_cash_utilization,
        )
        if qty == 0:
            # "spendable", not "cash": the caller nets pending BUYs out of settled
            # cash and floors at zero, so this figure is the budget handed to the
            # cap — not the raw account balance, which may well be negative.
            rejections.append(
                f"trimmed_to_zero_by_cash_cap (spendable=${portfolio_ctx.available_cash:,.2f})"
            )

    # 4. Portfolio caps (per-sector + correlation + liquidity + gross exposure)
    #    BUY only. `check_limits` takes the order as `new_position_value` and can
    #    only ever add it, so a sell arrives as though it grew the book: exits were
    #    rejected for the very sector and gross concentration they would relieve.
    #    Auditing the five it enforces, none survives on the sell side:
    #      - sector / gross / per-name: exposure caps, and a sell reduces exposure.
    #      - correlation: a cap on how many correlated bets to hold at once. An
    #        exit makes a crowded book less crowded, not more.
    #      - liquidity (ADV floor): the one that is not about exposure — but
    #        refusing to exit a thin name is how a position becomes permanent.
    #        Thin liquidity is a reason to work the order, which belongs in
    #        execution, not a reason to keep holding it.
    if qty > 0 and side == "BUY":
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
        submitted_at_utc=datetime.now(UTC),
    )
