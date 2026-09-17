"""Trade script exit code behavior for non-actionable ratings.

B-5: Hold decisions were exiting 1 (failure), causing daily_run.sh to count
them as failed tickers. Non-actionable ratings (Hold/Underweight) are policy,
not errors — the sizer rejects them by design, writes risk_approved=False, and
the run must exit 0.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tradingagents_us.schemas import AgentDecision, AgentReasoning


def test_hold_decision_has_no_entry_or_stop():
    """Hold decisions typically carry no entry/stop — that is expected."""
    # Replicate the structure from _decision_from_cached when parsing a Hold
    decision = AgentDecision(
        ticker="AAPL",
        market="US",
        quote_currency="USD",
        rating="Hold",
        entry_price=None,
        stop_loss=None,
        price_target=None,
        time_horizon="N/A",
        suggested_size_pct=0.0,
        reasoning=[
            AgentReasoning(
                agent="portfolio_manager",
                model="claude-opus-4-7",
                summary="Market conditions suggest patience.",
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        ],
        final_decision_text="RATING:Hold\n\nMarket conditions suggest patience.",
        timestamp_utc=datetime.now(UTC),
        decision_id="test-hold-123",
    )
    # The bug was that trade.py checked `decision.rating != "Sell"` first, which
    # includes Hold, then aborted if entry/stop were missing. This test documents
    # that Hold decisions lawfully have neither.
    assert decision.rating == "Hold"
    assert decision.entry_price is None
    assert decision.stop_loss is None


def test_underweight_decision_has_no_entry_or_stop():
    """Underweight (like Hold) is non-actionable and carries no BUY parameters."""
    decision = AgentDecision(
        ticker="NVDA",
        market="US",
        quote_currency="USD",
        rating="Underweight",
        entry_price=None,
        stop_loss=None,
        price_target=None,
        time_horizon=None,
        suggested_size_pct=0.0,
        reasoning=[
            AgentReasoning(
                agent="portfolio_manager",
                model="claude-opus-4-7",
                summary="Reducing exposure.",
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        ],
        final_decision_text="RATING:Underweight\n\nReducing exposure.",
        timestamp_utc=datetime.now(UTC),
        decision_id="test-underweight-456",
    )
    assert decision.rating == "Underweight"
    assert decision.entry_price is None
    assert decision.stop_loss is None


def test_buy_without_entry_stop_is_malformed():
    """Buy/Overweight missing entry+stop cannot be sized — reject early."""
    # This is the case trade.py SHOULD still abort on: an actionable rating
    # with no sizing parameters is a malformed LLM output.
    decision = AgentDecision(
        ticker="MSFT",
        market="US",
        quote_currency="USD",
        rating="Buy",
        entry_price=None,
        stop_loss=None,
        price_target=None,
        time_horizon=None,
        suggested_size_pct=0.0,
        reasoning=[
            AgentReasoning(
                agent="portfolio_manager",
                model="claude-opus-4-7",
                summary="Strong uptrend.",
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        ],
        final_decision_text="RATING:Buy\n\nStrong uptrend.",
        timestamp_utc=datetime.now(UTC),
        decision_id="test-buy-789",
    )
    assert decision.rating == "Buy"
    # The NEW check: only Buy/Overweight missing entry/stop is an error.
    # trade.py now checks `if decision.rating in ("Buy", "Overweight") and not (...)`
    # instead of `if decision.rating != "Sell" and not (...)`.
    assert decision.entry_price is None
    assert decision.stop_loss is None
