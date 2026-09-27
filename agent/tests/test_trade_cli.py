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


# --- cap flags reach the sizer ------------------------------------------------
#
# Run through main() with the real sizer, and read the order main() hands to
# submit_order: a flag that parses but is never passed on (the state
# MAX_POSITION_PCT was in for months, one layer up) has no effect on it.
#
# The book: $100k equity, $50k spendable, nothing held. A Buy at 250 with a
# stop at 240 risks $10/share, so 0.5% risk = 50 shares; the 10% single-name
# cap ($10k) trims that to 40, which is what today's defaults produce.


def _sized_buy(mock_dependencies, monkeypatch, *flags: str):
    monkeypatch.setattr(
        sys, "argv", ["trade.py", "--ticker", "TSLA", "--use-cached", "--no-persist", *flags]
    )
    buy = AgentDecision(
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
                summary="caps",
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        ],
        final_decision_text="RATING:Buy\nENTRY:250.0\nSTOP:240.0",
        timestamp_utc=datetime.now(UTC),
        decision_id="test-caps",
    )
    # Quiet the price-anomaly breaker so the only thing sizing the order is caps.
    with mock.patch("scripts.trade._decision_from_cached", return_value=buy), \
         mock.patch("scripts.trade._fetch_current_price", return_value=250.0), \
         mock.patch("scripts.trade.rolling_price_stats", return_value=(250.0, 2.0)):
        from scripts.trade import main
        assert main() == 0
    return mock_dependencies["submit"].call_args_list[-1].args[0]


def _gate_utilization(mock_dependencies) -> float:
    return mock_dependencies["council"].call_args.kwargs["max_cash_utilization"]


def test_default_caps_size_exactly_as_before(mock_dependencies, monkeypatch):
    order = _sized_buy(mock_dependencies, monkeypatch)
    assert order.risk_approved, order.rejection_reasons
    assert order.quantity == 40
    assert _gate_utilization(mock_dependencies) == 1.0


def test_max_cash_utilization_flag_reaches_the_sizer_and_the_gate(
    mock_dependencies, monkeypatch
):
    # 10% of $50k spendable = $5k = 20 shares at 250, below the position cap's 40.
    order = _sized_buy(mock_dependencies, monkeypatch, "--max-cash-utilization", "0.1")
    assert order.risk_approved, order.rejection_reasons
    assert order.quantity == 20
    # The pre-council gate budgets with the same figure the sizer will use, or
    # it councils names the sizer then cannot afford.
    assert _gate_utilization(mock_dependencies) == pytest.approx(0.1)


def test_max_position_pct_flag_reaches_the_sizer(mock_dependencies, monkeypatch):
    order = _sized_buy(mock_dependencies, monkeypatch, "--max-position-pct", "0.05")
    assert order.risk_approved, order.rejection_reasons
    assert order.quantity == 20


def test_max_sector_pct_flag_reaches_the_portfolio_check(mock_dependencies, monkeypatch):
    # 40 shares = $10k = 10% of equity, over a 5% sector cap.
    order = _sized_buy(mock_dependencies, monkeypatch, "--max-sector-pct", "0.05")
    assert not order.risk_approved
    assert any(
        r.startswith("sector_pct=") and r.endswith("exceeds 5%") for r in order.rejection_reasons
    ), order.rejection_reasons


@pytest.mark.parametrize(
    "flag", ["--max-position-pct", "--max-sector-pct", "--max-cash-utilization"]
)
@pytest.mark.parametrize("value", ["10", "1.5", "0", "-0.1", "nan", "inf"])
def test_a_cap_outside_zero_to_one_is_refused_before_the_broker(
    mock_dependencies, monkeypatch, capsys, flag, value
):
    # `--max-position-pct 10` meaning ten percent would be a 1000% cap, and a
    # cash utilization above 1 spends cash the account does not have. Both used
    # to be accepted; now the run dies in argparse, before any broker call or
    # model spend. (daily_run.sh passes none of these flags.)
    monkeypatch.setattr(
        sys, "argv", ["trade.py", "--ticker", "TSLA", "--use-cached", "--no-persist", flag, value]
    )
    from scripts.trade import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "fraction in (0, 1]" in capsys.readouterr().err
    assert not mock_dependencies["alpaca"].account.called


def test_a_cap_of_exactly_one_is_allowed(mock_dependencies, monkeypatch):
    order = _sized_buy(
        mock_dependencies,
        monkeypatch,
        "--max-position-pct", "1",
        "--max-sector-pct", "1",
        "--max-cash-utilization", "1",
    )
    # Uncapped by name and sector, so the 0.5% risk budget sizes it: 50 shares.
    assert order.risk_approved, order.rejection_reasons
    assert order.quantity == 50
