"""The sell side of the risk layer.

Every cap in this module exists to bound how much exposure may be taken on, and
until now they were applied to sells as well — so the machinery built to stop
the book getting too concentrated was also what stopped it unwinding. The worst
shape: a name at or above its single-name cap has zero headroom to add, so the
exit was trimmed to zero on exactly the position that most needed exiting.

Grepping tests/ before this file, "SELL" appeared once, against an empty book,
so the trim never engaged and none of it was visible to CI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradingagents_us.risk.circuit_breaker import CircuitBreaker
from tradingagents_us.risk.kill_switch import KillSwitchReader
from tradingagents_us.risk.portfolio_limits import PortfolioContext
from tradingagents_us.risk.sizer import MarketContext, size_from_decision
from tradingagents_us.schemas import AgentDecision

PRICE = 100.0


class _RunSwitch(KillSwitchReader):
    def read(self) -> str:
        return "RUN"


def _cb() -> CircuitBreaker:
    return CircuitBreaker(kill_switch=_RunSwitch())


def _decision(rating: str, *, entry: float | None = PRICE, stop: float | None = 95.0):
    return AgentDecision(
        ticker="MSFT",
        market="US",
        quote_currency="USD",
        rating=rating,  # type: ignore[arg-type]
        entry_price=entry,
        stop_loss=stop,
        reasoning=[],
        timestamp_utc=datetime.now(UTC),
        decision_id="d1",
    )


def _market(**kw) -> MarketContext:
    base = dict(
        current_price=PRICE,
        rolling_mean=PRICE,
        rolling_std=2.0,
        avg_daily_volume_usd=500_000_000.0,
        sector="Information Technology",
    )
    return MarketContext(**{**base, **kw})


def _portfolio(**kw) -> PortfolioContext:
    base = dict(
        equity=100_000.0,
        existing_position_values_by_ticker={},
        existing_position_values_by_sector={},
        high_correlation_count=0,
        available_cash=50_000.0,
    )
    return PortfolioContext(**{**base, **kw})


def _sell(held_qty: float, **portfolio_kw):
    return size_from_decision(
        decision=_decision("Sell"),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(**portfolio_kw),
        circuit_breaker=_cb(),
        held_quantity=held_qty,
    )


# --------------------------------------------------------------------------
# The exit must not be blocked by the caps that bound entries.
# --------------------------------------------------------------------------


def test_a_sell_on_an_over_cap_position_is_approved() -> None:
    """The headline case. A name at 12% of equity against a 10% cap has zero
    headroom to add, which used to trim the sell to zero and reject it as
    `trimmed_to_zero_by_portfolio_caps` — stranding the position."""
    order = _sell(
        120.0,
        existing_position_values_by_ticker={"MSFT": 12_000.0},
        existing_position_values_by_sector={"Information Technology": 12_000.0},
    )
    assert order.risk_approved, order.rejection_reasons
    assert order.side == "SELL"
    assert order.quantity == 120


def test_a_sell_is_not_blocked_by_a_full_sector() -> None:
    """A sell reduces sector exposure; rejecting it for `sector_pct` rejected the
    order for the very concentration it would have relieved."""
    order = _sell(
        50.0,
        existing_position_values_by_ticker={"MSFT": 5_000.0},
        existing_position_values_by_sector={"Information Technology": 45_000.0},
    )
    assert order.risk_approved, order.rejection_reasons


def test_a_sell_is_not_blocked_by_gross_exposure() -> None:
    order = _sell(
        50.0,
        existing_position_values_by_ticker={"MSFT": 5_000.0, "AAPL": 140_000.0},
    )
    assert order.risk_approved, order.rejection_reasons


def test_a_sell_is_not_blocked_by_a_crowded_correlation_book() -> None:
    order = _sell(
        50.0,
        existing_position_values_by_ticker={"MSFT": 5_000.0},
        high_correlation_count=9,
    )
    assert order.risk_approved, order.rejection_reasons


def test_a_sell_is_not_blocked_by_an_empty_cash_balance() -> None:
    """Selling raises cash rather than spending it."""
    order = _sell(
        50.0,
        existing_position_values_by_ticker={"MSFT": 5_000.0},
        available_cash=0.0,
    )
    assert order.risk_approved, order.rejection_reasons


# --------------------------------------------------------------------------
# A sell closes a position. It must never open one.
# --------------------------------------------------------------------------


def test_a_sell_never_exceeds_the_holding() -> None:
    """The naked-short case: sizing a sell off the risk budget produced an
    approved order larger than the position, and the executor attaches a
    protective leg to buys only — so the surplus was an unstopped short."""
    held = 30.0
    order = size_from_decision(
        decision=_decision("Sell", stop=101.0),  # a tight stop -> a large budget qty
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(existing_position_values_by_ticker={"MSFT": 3_000.0}),
        circuit_breaker=_cb(),
        held_quantity=held,
    )
    assert order.quantity <= held, f"{order.quantity} shares sold against {held} held"
    assert order.quantity == 30


def test_a_sell_with_nothing_held_is_refused() -> None:
    order = _sell(0.0)
    assert not order.risk_approved
    assert "nothing_held_to_sell" in order.rejection_reasons


def test_a_sell_needs_no_entry_price() -> None:
    """The trader agent has no reason to quote an entry for a name it wants out
    of, and requiring one dropped the exit before any row was written."""
    order = size_from_decision(
        decision=_decision("Sell", entry=None, stop=None),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(existing_position_values_by_ticker={"MSFT": 4_000.0}),
        circuit_breaker=_cb(),
        held_quantity=40.0,
    )
    assert order.risk_approved, order.rejection_reasons
    assert order.quantity == 40


# --------------------------------------------------------------------------
# The buy side must keep every cap it had.
# --------------------------------------------------------------------------


def test_a_buy_is_still_trimmed_by_the_single_name_cap() -> None:
    order = size_from_decision(
        decision=_decision("Buy"),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(existing_position_values_by_ticker={"MSFT": 12_000.0}),
        circuit_breaker=_cb(),
        held_quantity=120.0,
    )
    assert not order.risk_approved
    assert any("trimmed_to_zero_by_portfolio_caps" in r for r in order.rejection_reasons)


def test_a_buy_is_still_blocked_by_a_full_sector() -> None:
    order = size_from_decision(
        decision=_decision("Buy"),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(
            existing_position_values_by_sector={"Information Technology": 29_500.0}
        ),
        circuit_breaker=_cb(),
    )
    assert not order.risk_approved
    assert any("sector_pct" in r for r in order.rejection_reasons)


def test_a_buy_is_still_blocked_by_a_thin_name() -> None:
    order = size_from_decision(
        decision=_decision("Buy"),
        account_equity=100_000.0,
        market_ctx=_market(avg_daily_volume_usd=50_000.0),
        portfolio_ctx=_portfolio(),
        circuit_breaker=_cb(),
    )
    assert not order.risk_approved
    assert any("adv_usd" in r for r in order.rejection_reasons)


def test_a_buy_is_still_bounded_by_cash() -> None:
    order = size_from_decision(
        decision=_decision("Buy"),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(available_cash=0.0),
        circuit_breaker=_cb(),
    )
    assert not order.risk_approved
    assert any("cash_cap" in r for r in order.rejection_reasons)


def test_the_kill_switch_still_stops_a_sell() -> None:
    """Exposure caps stop applying to sells; the kill switch must not. FLATTEN_ALL
    has its own path, and an operator halting the system means halting all of it."""

    class _Paused(KillSwitchReader):
        def read(self) -> str:
            return "PAUSE_NEW"

    order = size_from_decision(
        decision=_decision("Sell"),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(existing_position_values_by_ticker={"MSFT": 5_000.0}),
        circuit_breaker=CircuitBreaker(kill_switch=_Paused()),
        held_quantity=50.0,
    )
    assert not order.risk_approved
    assert any("kill_switch" in r for r in order.rejection_reasons)
