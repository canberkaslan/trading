"""The risk layer as the caller actually wires it.

Every control fixed here was already covered by a unit test that passed. The
units were right; the wiring was not — `scripts/trade.py` handed the checkers
constants, so four caps could never fire and the sizing was 2.5x its setting.
A test that only feeds a checker a breaching input proves the checker works,
never that anything reaches it. These exercise the properties that broke.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tradingagents_us.risk.circuit_breaker import CircuitBreaker
from tradingagents_us.risk.kill_switch import KillSwitchReader
from tradingagents_us.risk.portfolio_limits import (
    PortfolioContext,
    PortfolioLimits,
    check_limits,
)
from tradingagents_us.risk.position_sizing import risk_based_size
from tradingagents_us.risk.sizer import MarketContext, size_from_decision
from tradingagents_us.schemas import AgentDecision


class _RunSwitch(KillSwitchReader):
    def read(self) -> str:
        return "RUN"


def _decision(entry: float = 100.0, stop: float = 95.0) -> AgentDecision:
    return AgentDecision(
        ticker="AAPL",
        market="US",
        quote_currency="USD",
        rating="Buy",
        entry_price=entry,
        stop_loss=stop,
        reasoning=[],
        timestamp_utc=datetime.now(UTC),
        decision_id="d1",
    )


def _market(**kw) -> MarketContext:
    base = dict(current_price=100.0, rolling_mean=100.0, rolling_std=2.0)
    return MarketContext(**{**base, **kw})


def _portfolio(**kw) -> PortfolioContext:
    base = dict(
        equity=100_000.0,
        existing_position_values_by_ticker={},
        existing_position_values_by_sector={},
        high_correlation_count=0,
        available_cash=100_000.0,
    )
    return PortfolioContext(**{**base, **kw})


# --------------------------------------------------------------------------
# 1. Per-trade risk. This is the property that silently broke.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "stop", "equity", "risk"),
    [
        (100.0, 95.0, 100_000.0, 0.005),
        (100.0, 99.0, 100_000.0, 0.005),   # tight stop -> more shares, same loss
        (250.0, 200.0, 250_000.0, 0.01),
        (50.0, 45.0, 40_000.0, 0.002),
    ],
)
def test_a_stop_out_costs_the_configured_risk(entry, stop, equity, risk) -> None:
    """Whatever the stop distance, being stopped out costs `risk` of equity.

    This is the invariant the ATR proxy broke: sizing assumed a stop at 40% of
    the real distance, so the position came out 2.5x and a stop-out cost 1.25%
    of equity against a 0.5% setting. Nothing asserted it, so nothing noticed.
    """
    shares = risk_based_size(equity=equity, entry=entry, stop=stop, risk_per_trade=risk)
    loss = shares * abs(entry - stop)
    budget = equity * risk
    assert loss == pytest.approx(budget, rel=0.01), (
        f"{shares} shares lose ${loss:,.2f} at the stop, budget is ${budget:,.2f}"
    )


def test_tightening_the_stop_does_not_change_the_risk() -> None:
    """A tighter stop buys more shares, not more risk — the point of the rule."""
    wide = risk_based_size(equity=100_000.0, entry=100.0, stop=90.0)
    tight = risk_based_size(equity=100_000.0, entry=100.0, stop=95.0)
    assert tight > wide
    assert tight * 5.0 == pytest.approx(wide * 10.0, rel=0.02)


def test_sizer_risks_the_configured_amount_end_to_end() -> None:
    """Through `size_from_decision`, not just the helper — the sizer is where
    the two conflicting distance factors used to meet."""
    order = size_from_decision(
        decision=_decision(entry=100.0, stop=95.0),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(),
        circuit_breaker=CircuitBreaker(kill_switch=_RunSwitch()),
        risk_per_trade=0.005,
    )
    # 0.5% of 100k = $500 budget, $5 stop distance -> 100 shares.
    assert order.quantity == 100
    assert order.quantity * 5.0 == pytest.approx(500.0, rel=0.01)


def test_a_supplied_atr_no_longer_overrides_the_real_stop() -> None:
    """The regression itself: a caller passing an ATR must not resize the trade.

    `scripts/trade.py` used to pass `|entry - stop| / 5`, and that one argument
    is what made every position 2.5x. Sizing now measures the stop directly, so
    the field cannot reintroduce it.
    """
    with_atr = size_from_decision(
        decision=_decision(entry=100.0, stop=95.0),
        account_equity=100_000.0,
        market_ctx=_market(atr=1.0),  # the old bogus |entry-stop|/5
        portfolio_ctx=_portfolio(),
        circuit_breaker=CircuitBreaker(kill_switch=_RunSwitch()),
        risk_per_trade=0.005,
    )
    without = size_from_decision(
        decision=_decision(entry=100.0, stop=95.0),
        account_equity=100_000.0,
        market_ctx=_market(atr=None),
        portfolio_ctx=_portfolio(),
        circuit_breaker=CircuitBreaker(kill_switch=_RunSwitch()),
        risk_per_trade=0.005,
    )
    assert with_atr.quantity == without.quantity == 100


# --------------------------------------------------------------------------
# 2. The circuit breaker's daily-drawdown halt.
# --------------------------------------------------------------------------


def test_daily_drawdown_halt_fires_when_the_session_is_down() -> None:
    order = size_from_decision(
        decision=_decision(),
        account_equity=95_000.0,          # down 5% on the session
        market_ctx=_market(),
        portfolio_ctx=_portfolio(equity=95_000.0),
        circuit_breaker=CircuitBreaker(kill_switch=_RunSwitch(), max_daily_dd=0.03),
        session_open_equity=100_000.0,
    )
    assert not order.risk_approved
    assert any("daily_drawdown" in r for r in order.rejection_reasons)


def test_daily_drawdown_halt_is_quiet_on_a_flat_session() -> None:
    order = size_from_decision(
        decision=_decision(),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(),
        circuit_breaker=CircuitBreaker(kill_switch=_RunSwitch(), max_daily_dd=0.03),
        session_open_equity=100_000.0,
    )
    assert not any("daily_drawdown" in r for r in order.rejection_reasons)


def test_passing_current_equity_as_the_session_open_cannot_halt() -> None:
    """The exact shape of the old bug, kept as a test so it reads as a mistake
    rather than an option: equity_now == equity_open makes the drawdown 0.0
    however far the account has actually fallen."""
    cb = CircuitBreaker(kill_switch=_RunSwitch(), max_daily_dd=0.03)
    ok, reasons = cb.check(
        equity_now=50_000.0,
        equity_open=50_000.0,  # what the sizer used to pass
        price=100.0,
        rolling_mean=100.0,
        rolling_std=2.0,
    )
    assert ok and not reasons, "an account halved on the day still looks flat"


# --------------------------------------------------------------------------
# 3. The sector cap.
# --------------------------------------------------------------------------


def test_sector_cap_rejects_once_the_sector_is_full() -> None:
    ok, reasons = check_limits(
        ticker="MSFT",
        sector="Information Technology",
        new_position_value=8_000.0,
        avg_daily_volume_usd=500_000_000.0,
        ctx=_portfolio(existing_position_values_by_sector={"Information Technology": 25_000.0}),
        limits=PortfolioLimits(),
    )
    assert not ok
    assert any("sector_pct" in r for r in reasons)


def test_an_empty_sector_book_can_never_breach_the_sector_cap() -> None:
    """The old wiring: with an empty per-sector map the only thing in the
    sector is the new order, already trimmed under the 10% single-name cap, so
    30% was unreachable by construction."""
    ok, reasons = check_limits(
        ticker="MSFT",
        sector="Unknown",
        new_position_value=10_000.0,      # the most a single name can be
        avg_daily_volume_usd=500_000_000.0,
        ctx=_portfolio(existing_position_values_by_sector={}),
        limits=PortfolioLimits(),
    )
    assert ok and not reasons


def test_unmapped_sectors_are_not_bucketed_together() -> None:
    """Two unrelated names whose sector is unknown must not accumulate into one
    bucket — that would invent concentration and reject on it."""
    from tradingagents_us.dataflows.sector_map import sector_for

    assert sector_for("ZZZZ") is None
    ok, _ = check_limits(
        ticker="ZZZZ",
        sector=None,
        new_position_value=10_000.0,
        avg_daily_volume_usd=500_000_000.0,
        ctx=_portfolio(existing_position_values_by_sector={"Information Technology": 25_000.0}),
        limits=PortfolioLimits(),
    )
    assert ok


# --------------------------------------------------------------------------
# 4. The liquidity floor.
# --------------------------------------------------------------------------


def test_liquidity_floor_rejects_a_thin_name() -> None:
    ok, reasons = check_limits(
        ticker="THIN",
        sector=None,
        new_position_value=5_000.0,
        avg_daily_volume_usd=50_000.0,     # under the 100k floor
        ctx=_portfolio(),
        limits=PortfolioLimits(),
    )
    assert not ok
    assert any("liquidity" in r or "adv" in r.lower() for r in reasons)


def test_the_cold_cache_fallback_does_not_block_every_order() -> None:
    """With no cached bars the caller substitutes the floor itself, so this
    must pass. It only does because the check is strictly `<` — flip it to
    `<=` and a freshly deployed box, whose bar cache is empty, would reject
    every order it ever sized while looking like a liquidity problem.
    """
    limits = PortfolioLimits()
    ok, reasons = check_limits(
        ticker="ANY",
        sector=None,
        new_position_value=5_000.0,
        avg_daily_volume_usd=limits.min_liquidity_adv,
        ctx=_portfolio(),
        limits=limits,
    )
    assert ok, f"cold-cache fallback rejected the order: {reasons}"


def test_a_hardcoded_billion_makes_every_name_liquid() -> None:
    """What the caller used to pass, pinned as the mistake it was."""
    ok, _ = check_limits(
        ticker="THIN",
        sector=None,
        new_position_value=5_000.0,
        avg_daily_volume_usd=1_000_000_000.0,
        ctx=_portfolio(),
        limits=PortfolioLimits(),
    )
    assert ok


# --------------------------------------------------------------------------
# 5. The correlation cap.
# --------------------------------------------------------------------------


def test_correlation_cap_rejects_a_crowded_book() -> None:
    ok, reasons = check_limits(
        ticker="MSFT",
        sector=None,
        new_position_value=5_000.0,
        avg_daily_volume_usd=500_000_000.0,
        ctx=_portfolio(high_correlation_count=3),
        limits=PortfolioLimits(),
    )
    assert not ok
    assert any("correlated" in r for r in reasons)


def test_a_hardcoded_zero_can_never_breach_the_correlation_cap() -> None:
    ok, _ = check_limits(
        ticker="MSFT",
        sector=None,
        new_position_value=5_000.0,
        avg_daily_volume_usd=500_000_000.0,
        ctx=_portfolio(high_correlation_count=0),
        limits=PortfolioLimits(),
    )
    assert ok
