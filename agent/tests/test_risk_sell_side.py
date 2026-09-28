"""The sell side of the risk layer.

Almost every control here exists to bound how much risk may be taken ON, and
until now they were applied to sells as well — so the machinery built to stop
the book getting too concentrated was also what stopped it unwinding. The worst
shape: a name at or above its single-name cap has zero headroom to add, so the
exit was trimmed to zero on exactly the position that most needed exiting.

It was not only the caps. One layer up the circuit breaker halted the exit too:
on a daily drawdown, on a losing streak, and on a PAUSE_NEW kill switch whose
own docstring reads "no new entries; manage existing (honor stops)". Of the
risk gates only FLATTEN_ALL still refuses a sell, because the flatten path is
already selling the book; the gate that doubts the price itself, the anomaly
z-score, keeps both sides too. The API error-rate gate is not tested as a sell
control here: nothing in production feeds it, so it cannot fire (see sizer.py).

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
    """The book has to be far enough over the 1.5x cap that the sell's own
    notional pushes the (wrongly) added total past it — $150k gross on $100k
    equity, plus a $5k exit. Sized so it breaches only under the old both-sides
    reading, which is the whole point: an exit rejected for the gross exposure
    it removes."""
    order = _sell(
        50.0,
        existing_position_values_by_ticker={"MSFT": 5_000.0, "AAPL": 145_000.0},
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


# --------------------------------------------------------------------------
# The circuit breaker's risk-taking gates are entry-side too.
#
# The caps in the sizer were not the only controls written for entries and then
# applied to both sides — one layer up, the breaker halts on a drawdown, on a
# losing streak, and on a kill switch whose own docstring reads "no new entries;
# manage existing". Each of those refused the exit as well.
# --------------------------------------------------------------------------


def _sell_through(cb: CircuitBreaker, **kw):
    return size_from_decision(
        decision=_decision("Sell"),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(existing_position_values_by_ticker={"MSFT": 5_000.0}),
        circuit_breaker=cb,
        held_quantity=50.0,
        **kw,
    )


def test_a_drawdown_halt_does_not_strand_a_sell() -> None:
    """The sharpest case of all. The halt exists to stop the account taking on
    new risk after a bad day; blocking the exits is how the bad day compounds —
    the hazard test_risk_cash_budget.py already names for the cash cap."""
    order = _sell_through(_cb(), session_open_equity=104_200.0)  # -4% vs a 3% limit
    assert order.risk_approved, order.rejection_reasons
    assert order.quantity == 50


def test_a_buy_is_still_halted_by_a_drawdown() -> None:
    order = size_from_decision(
        decision=_decision("Buy"),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(),
        circuit_breaker=_cb(),
        session_open_equity=104_200.0,
    )
    assert not order.risk_approved
    assert any("daily_drawdown" in r for r in order.rejection_reasons)


def test_a_losing_streak_does_not_strand_a_sell() -> None:
    """A run of losses is a reason to stop opening positions. It is not a reason
    to keep holding the ones doing the losing."""
    cb = _cb()
    for _ in range(5):
        cb.record_trade_result(profitable=False)
    assert _sell_through(cb).risk_approved


def test_pause_new_does_not_block_a_sell() -> None:
    """PAUSE_NEW is documented as "no new entries; manage existing (honor stops)"
    and an exit is managing existing. It is also what FileKillSwitchReader
    returns for an unreadable, empty or corrupt flag file — so leaving it on the
    sell side let a failed read of a local file seal the exits."""

    class _Paused(KillSwitchReader):
        def read(self) -> str:
            return "PAUSE_NEW"

    assert _sell_through(CircuitBreaker(kill_switch=_Paused())).risk_approved


def test_flatten_all_still_blocks_a_sell() -> None:
    """FLATTEN_ALL is not a halt on risk-taking: it hands the book to
    execution/flatten.py, which is already selling every lot. A sell from the
    sizer would be a second writer on the same shares, sized off a holding read
    before the flatten ran — the double sell manage_positions skips its whole
    pass to avoid."""

    class _Flatten(KillSwitchReader):
        def read(self) -> str:
            return "FLATTEN_ALL"

    order = _sell_through(CircuitBreaker(kill_switch=_Flatten()))
    assert not order.risk_approved
    assert "kill_switch=FLATTEN_ALL" in order.rejection_reasons


def test_the_kill_switch_still_stops_a_buy() -> None:
    class _Paused(KillSwitchReader):
        def read(self) -> str:
            return "PAUSE_NEW"

    order = size_from_decision(
        decision=_decision("Buy"),
        account_equity=100_000.0,
        market_ctx=_market(),
        portfolio_ctx=_portfolio(),
        circuit_breaker=CircuitBreaker(kill_switch=_Paused()),
    )
    assert not order.risk_approved
    assert any("kill_switch" in r for r in order.rejection_reasons)


def test_a_bad_print_still_stops_a_sell() -> None:
    """The one class of gate that keeps both sides. A price 5 sigma off the mean
    is a reason to doubt the number, not a statement about exposure, and selling
    into a bad print is no safer than buying into one."""
    order = size_from_decision(
        decision=_decision("Sell"),
        account_equity=100_000.0,
        market_ctx=_market(current_price=150.0, rolling_mean=PRICE, rolling_std=10.0),
        portfolio_ctx=_portfolio(existing_position_values_by_ticker={"MSFT": 5_000.0}),
        circuit_breaker=_cb(),
        held_quantity=50.0,
    )
    assert not order.risk_approved
    assert any("z_score" in r for r in order.rejection_reasons)
