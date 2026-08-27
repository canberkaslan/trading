"""Order-flow actionability: submitted vs refused, and why."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradingagents_us.execution.actionability import (
    OrderRecord,
    build_report,
    normalize_reason,
)

BASE = datetime(2026, 8, 3, 3, 0, tzinfo=UTC)  # a Monday


def order(
    *,
    day_offset: int = 0,
    ticker: str = "AAPL",
    side: str = "BUY",
    approved: bool = False,
    reasons: list[str] | None = None,
    broker_id: str | None = None,
) -> OrderRecord:
    return OrderRecord(
        ticker=ticker,
        side=side,
        submitted_at_utc=BASE + timedelta(days=day_offset),
        risk_approved=approved,
        rejection_reasons=reasons or [],
        broker_order_id=broker_id,
    )


# --------------------------- reason normalization ---------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("trimmed_to_zero_by_cash_cap (spendable=$0.00)", "trimmed_to_zero_by_cash_cap"),
        ("trimmed_to_zero_by_cash_cap (spendable=$1,204.50)", "trimmed_to_zero_by_cash_cap"),
        ("trimmed_to_zero_by_portfolio_caps", "trimmed_to_zero_by_portfolio_caps"),
        ("non-actionable rating=Hold", "non-actionable rating=Hold"),
        ("  padded reason  ", "padded reason"),
    ],
)
def test_normalize_reason_strips_only_the_live_value_suffix(raw: str, expected: str) -> None:
    assert normalize_reason(raw) == expected


def test_same_cause_with_different_balances_is_one_bucket() -> None:
    """The whole point: a persistent blocker must not scatter into one-offs."""
    report = build_report(
        [
            order(reasons=["trimmed_to_zero_by_cash_cap (spendable=$0.00)"]),
            order(day_offset=1, reasons=["trimmed_to_zero_by_cash_cap (spendable=$12.30)"]),
            order(day_offset=2, reasons=["trimmed_to_zero_by_cash_cap (spendable=$0.00)"]),
        ]
    )
    assert report.by_reason == {"trimmed_to_zero_by_cash_cap": 3}
    assert report.dominant_reason == "trimmed_to_zero_by_cash_cap"


# ------------------------------- counting -----------------------------------


def test_empty_window_is_idle_not_inert() -> None:
    """No rows means the run may not have happened — never blame the strategy."""
    report = build_report([])
    assert report.verdict() == "idle"
    assert (report.orders, report.submitted, report.refused) == (0, 0, 0)
    assert report.inert_run_days == 0
    assert report.dominant_reason is None


def test_submitted_is_broker_ack_not_self_approval() -> None:
    """risk_approved is the agent grading itself; only a broker id is execution."""
    report = build_report(
        [
            order(approved=True, broker_id=None, reasons=["broker rejected"]),
            order(day_offset=1, approved=True, broker_id="abc-123"),
        ]
    )
    assert report.submitted == 1
    assert report.refused == 1
    assert report.last_submitted_at_utc == BASE + timedelta(days=1)


def test_multi_reason_order_counts_once_per_reason() -> None:
    report = build_report(
        [order(reasons=["trimmed_to_zero_by_portfolio_caps", "sector_cap_exceeded"])]
    )
    assert report.refused == 1
    assert report.by_reason == {
        "trimmed_to_zero_by_portfolio_caps": 1,
        "sector_cap_exceeded": 1,
    }
    # Reason counts sum above `refused` by design — asserted so nobody "fixes"
    # it into a percentage of orders later.
    assert sum(report.by_reason.values()) > report.refused


def test_blank_reasons_are_not_buckets() -> None:
    report = build_report([order(reasons=["", "   ", "real_reason"])])
    assert report.by_reason == {"real_reason": 1}


def test_by_reason_is_ordered_most_frequent_first() -> None:
    report = build_report(
        [
            order(reasons=["rare"]),
            order(day_offset=1, reasons=["common"]),
            order(day_offset=1, ticker="MSFT", reasons=["common"]),
            order(day_offset=2, reasons=["common"]),
        ]
    )
    assert list(report.by_reason) == ["common", "rare"]


# ----------------------------- inert run days -------------------------------


def test_inert_counts_run_days_not_calendar_days() -> None:
    """A weekend gap must not read as two inert days on Monday morning."""
    report = build_report(
        [
            order(day_offset=0, broker_id="filled-monday"),
            # Fri (+4), then Mon (+7): the weekend produced no rows at all.
            order(day_offset=4, reasons=["non-actionable rating=Hold"]),
            order(day_offset=7, reasons=["non-actionable rating=Hold"]),
        ]
    )
    assert report.run_days == 3
    assert report.inert_run_days == 2  # Fri + Mon, not Fri..Mon
    assert report.verdict() == "active"  # 2 < threshold of 3


def test_inert_streak_breaks_at_the_most_recent_submission() -> None:
    report = build_report(
        [
            order(day_offset=0, reasons=["non-actionable rating=Hold"]),
            order(day_offset=1, broker_id="the-last-fill"),
            order(day_offset=2, reasons=["trimmed_to_zero_by_portfolio_caps"]),
            order(day_offset=3, reasons=["trimmed_to_zero_by_portfolio_caps"]),
            order(day_offset=4, reasons=["trimmed_to_zero_by_portfolio_caps"]),
        ]
    )
    # Only the streak SINCE the last submission counts; day 0 is behind it.
    assert report.inert_run_days == 3
    assert report.verdict() == "inert"
    assert report.last_submitted_at_utc == BASE + timedelta(days=1)


def test_a_day_with_one_submission_among_many_refusals_is_not_inert() -> None:
    """The daily run writes ~11 rows a day; one broker ack makes the day active."""
    same_day = [order(ticker=t, reasons=["non-actionable rating=Hold"]) for t in "ABCDE"]
    same_day.append(order(ticker="NVDA", broker_id="one-good-one"))
    report = build_report(same_day)
    assert report.run_days == 1
    assert report.inert_run_days == 0
    assert report.verdict() == "active"


def test_verdict_threshold_is_caller_tunable() -> None:
    report = build_report(
        [order(day_offset=i, reasons=["trimmed_to_zero_by_portfolio_caps"]) for i in range(2)]
    )
    assert report.inert_run_days == 2
    assert report.verdict(inert_threshold=3) == "active"
    assert report.verdict(inert_threshold=2) == "inert"


def test_unsorted_input_still_yields_the_right_streak_and_bounds() -> None:
    """Rows arrive newest-first from SQL; the report must not depend on order."""
    rows = [
        order(day_offset=3, reasons=["trimmed_to_zero_by_portfolio_caps"]),
        order(day_offset=0, broker_id="oldest-fill"),
        order(day_offset=4, reasons=["trimmed_to_zero_by_portfolio_caps"]),
        order(day_offset=2, reasons=["trimmed_to_zero_by_portfolio_caps"]),
    ]
    report = build_report(rows)
    assert report.inert_run_days == 3
    assert report.first_order_at_utc == BASE
    assert report.last_order_at_utc == BASE + timedelta(days=4)


def test_the_live_2026_08_shape_reads_as_inert() -> None:
    """Regression guard for the book this was written to catch.

    Ten names, all at the per-position cap or rated Hold, nothing reaching the
    broker for days — while the equity curve keeps posting the tape's return.
    """
    rows: list[OrderRecord] = []
    for day in range(5):
        for t in ["MSFT", "NVDA", "GOOGL", "JPM", "V"]:
            rows.append(
                order(day_offset=day, ticker=t, reasons=["trimmed_to_zero_by_portfolio_caps"])
            )
        for t in ["AAPL", "AMZN", "META", "XOM", "UNH"]:
            rows.append(order(day_offset=day, ticker=t, reasons=["non-actionable rating=Hold"]))

    report = build_report(rows)
    assert report.orders == 50
    assert report.submitted == 0
    assert report.verdict() == "inert"
    assert report.inert_run_days == 5
    assert report.dominant_reason in {
        "trimmed_to_zero_by_portfolio_caps",
        "non-actionable rating=Hold",
    }
    assert report.last_submitted_at_utc is None
