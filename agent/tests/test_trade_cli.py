"""Trade script exit code behavior for non-actionable ratings.

B-5: Hold decisions were exiting 1 (failure), causing daily_run.sh to count
them as failed tickers. Non-actionable ratings (Hold/Underweight) are policy,
not errors — the sizer rejects them by design, writes risk_approved=False, and
the run must exit 0.

Test strategy: mock all external dependencies (Alpaca, Polygon, filesystem) so
main() can run in isolation, then verify the exit code for each rating scenario.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

# Make the scripts package importable just like scripts/trade.py does
_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from tradingagents_us.schemas import AgentDecision, AgentReasoning  # noqa: E402


@pytest.fixture
def mock_dependencies():
    """Mock all external dependencies so main() runs without real APIs."""
    with mock.patch("scripts.trade._load_env"), \
         mock.patch("scripts.trade.AlpacaClient") as mock_alpaca, \
         mock.patch("scripts.trade.PolygonClient"), \
         mock.patch("scripts.trade._fetch_current_price", return_value=150.0), \
         mock.patch("scripts.trade.average_dollar_volume", return_value=5_000_000), \
         mock.patch("scripts.trade.rolling_price_stats", return_value=(150.0, 2.0)), \
         mock.patch("scripts.trade.count_correlated", return_value=0), \
         mock.patch("scripts.trade.recent_loss_streak", return_value=0), \
         mock.patch("scripts.trade.should_council") as mock_council, \
         mock.patch("scripts.trade.TradeLogRepository"), \
         mock.patch("scripts.trade.submit_order") as mock_submit:

        # Mock Alpaca account + positions
        mock_acct = mock.MagicMock()
        mock_acct.account_number = "PA12345"
        mock_acct.status = "ACTIVE"
        mock_acct.portfolio_value = 100_000.0
        mock_acct.cash = 50_000.0
        mock_acct.buying_power = 100_000.0
        mock_acct.pattern_day_trader = False
        mock_acct.last_equity = 99_000.0  # For circuit breaker drawdown check

        mock_alpaca_instance = mock_alpaca.return_value.__enter__.return_value
        mock_alpaca_instance.account.return_value = mock_acct
        mock_alpaca_instance.list_positions.return_value = []
        mock_alpaca_instance.list_orders.return_value = []

        # Council always says "run"
        mock_council.return_value = mock.MagicMock(run=True, reason="test gate")

        # submit_order returns a non-error result (exit 0)
        mock_result = mock.MagicMock()
        mock_result.error = None
        mock_result.dry_run = True
        mock_result.submitted = False
        mock_result.update.status = "DRY_RUN"
        mock_result.broker_order_id = None
        mock_result.refusal_reasons = []
        mock_submit.return_value = mock_result

        yield {
            "alpaca": mock_alpaca_instance,
            "council": mock_council,
            "submit": mock_submit,
        }


def test_hold_decision_exits_zero(mock_dependencies, monkeypatch, capsys):
    """Hold rating exits 0 even without entry/stop — non-actionable is policy."""
    # Mock argv so argparse reads our test args
    test_args = ["trade.py", "--ticker", "AAPL", "--use-cached", "--no-persist"]
    monkeypatch.setattr(sys, "argv", test_args)

    # Mock _decision_from_cached to return a Hold decision with no entry/stop
    hold_decision = AgentDecision(
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

    with mock.patch("scripts.trade._decision_from_cached", return_value=hold_decision):
        from scripts.trade import main
        exit_code = main()

    # Non-actionable Hold must exit 0, not 1
    assert exit_code == 0, "Hold decision should exit 0 (policy refusal, not error)"

    # Verify sizer was called (Hold will be rejected as non-actionable by sizer)
    assert mock_dependencies["submit"].called, "submit_order should be called for Hold"


def test_underweight_decision_exits_zero(mock_dependencies, monkeypatch, capsys):
    """Underweight (like Hold) is non-actionable — exit 0."""
    test_args = ["trade.py", "--ticker", "NVDA", "--use-cached", "--no-persist"]
    monkeypatch.setattr(sys, "argv", test_args)

    underweight_decision = AgentDecision(
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

    with mock.patch("scripts.trade._decision_from_cached", return_value=underweight_decision):
        from scripts.trade import main
        exit_code = main()

    assert exit_code == 0, "Underweight decision should exit 0 (non-actionable)"


def test_buy_without_entry_stop_exits_one(mock_dependencies, monkeypatch, capsys):
    """Buy missing entry+stop is malformed LLM output — exit 1 early."""
    test_args = ["trade.py", "--ticker", "MSFT", "--use-cached", "--no-persist"]
    monkeypatch.setattr(sys, "argv", test_args)

    # Malformed Buy: actionable rating but no entry/stop
    buy_decision = AgentDecision(
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

    with mock.patch("scripts.trade._decision_from_cached", return_value=buy_decision):
        from scripts.trade import main
        exit_code = main()

    # Buy without entry/stop should abort early with exit 1
    assert exit_code == 1, "Buy without entry/stop should exit 1 (malformed)"

    # submit_order should NOT have been called (aborted before risk layer)
    assert not mock_dependencies["submit"].called, (
        "submit_order should not be called for malformed Buy"
    )


def test_buy_with_entry_stop_proceeds(mock_dependencies, monkeypatch, capsys):
    """Buy WITH entry+stop proceeds through sizing — exit 0 on dry-run success."""
    test_args = ["trade.py", "--ticker", "TSLA", "--use-cached", "--no-persist"]
    monkeypatch.setattr(sys, "argv", test_args)

    # Well-formed Buy: actionable rating with entry+stop
    buy_decision = AgentDecision(
        ticker="TSLA",
        market="US",
        quote_currency="USD",
        rating="Buy",
        entry_price=250.0,
        stop_loss=240.0,
        price_target=280.0,
        time_horizon="3mo",
        suggested_size_pct=0.05,
        reasoning=[
            AgentReasoning(
                agent="portfolio_manager",
                model="claude-opus-4-7",
                summary="EV dominance.",
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        ],
        final_decision_text="RATING:Buy\nENTRY:250.0\nSTOP:240.0\n\nEV dominance.",
        timestamp_utc=datetime.now(UTC),
        decision_id="test-buy-complete-999",
    )

    with mock.patch("scripts.trade._decision_from_cached", return_value=buy_decision):
        from scripts.trade import main
        exit_code = main()

    # Complete Buy should proceed through sizing and exit 0 (dry-run, no error)
    assert exit_code == 0, "Well-formed Buy should exit 0 on dry-run success"

    # submit_order SHOULD be called
    assert mock_dependencies["submit"].called, "submit_order should be called for well-formed Buy"
