"""Concentration analytics — pure metric math and snapshot-trend parsing."""

from __future__ import annotations

from tradingagents_us.risk.concentration import (
    PositionWeight,
    compute_concentration,
    parse_trend,
)


def _pw(ticker: str, mv: float) -> PositionWeight:
    return PositionWeight(ticker=ticker, market_value=mv)


def test_equal_weight_book_has_effective_n_equal_to_count() -> None:
    # 10 equal $6k positions, $100k equity (60k invested, 40k cash) — each 6% < cap.
    tickers = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "JPM", "V", "TSLA", "AVGO")
    positions = [_pw(t, 6_000.0) for t in tickers]
    m = compute_concentration(positions, equity=100_000.0)

    assert m.n_positions == 10
    assert m.hhi == 0.1  # 10 * (1/10)^2
    assert m.effective_n == 10.0
    assert m.gross_exposure_pct == 60.0  # 60k / 100k
    assert m.cash_pct == 40.0
    assert m.top_weight_pct == 6.0  # 6k / 100k
    assert m.flags == []  # no name above 10% cap, effective_n >= 3


def test_concentrated_book_flags_dominant_name() -> None:
    # One name dwarfs the rest -> flag + low effective_n.
    positions = [_pw("TSLA", 60_000.0), _pw("AAPL", 5_000.0), _pw("MSFT", 5_000.0)]
    m = compute_concentration(positions, equity=100_000.0)

    assert m.top_weight_pct == 60.0
    assert m.effective_n < 3.0
    assert any("TSLA" in f and "single-name cap" in f for f in m.flags)
    assert any("effective_n" in f for f in m.flags)


def test_top3_weight_sums_three_largest() -> None:
    positions = [
        _pw("A", 40_000.0),
        _pw("B", 30_000.0),
        _pw("C", 20_000.0),
        _pw("D", 10_000.0),
    ]
    m = compute_concentration(positions, equity=100_000.0)
    assert m.top3_weight_pct == 90.0  # 40 + 30 + 20


def test_empty_and_zero_equity_are_safe() -> None:
    assert compute_concentration([], equity=100_000.0).n_positions == 0
    m = compute_concentration([_pw("AAPL", 1_000.0)], equity=0.0)
    assert m.hhi == 1.0  # single name dominates invested weights
    assert m.top_weight_pct == 0.0  # no equity -> no equity-relative weight, no div-by-zero


def test_parse_trend_skips_malformed_and_respects_limit() -> None:
    records = [
        {"ts": "2026-06-26T00:00:00Z", "n_positions": 5, "top_weight_pct": 12.0, "equity": 99_000.0},
        {"bad": "row"},  # missing ts -> skipped
        {"ts": "2026-06-27T00:00:00Z", "n_positions": 8, "top_weight_pct": 9.5, "equity": 100_000.0},
    ]
    points = parse_trend(records, limit=2)
    assert [p.n_positions for p in points] == [5, 8]
    assert points[-1].top_weight_pct == 9.5
