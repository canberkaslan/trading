"""LLM backtest scoring — pure logic, no network/LLM."""

from __future__ import annotations

from backtest.llm_backtest import PointResult, score_rating, summarize


class TestScore:
    def test_bullish_hit_on_up(self) -> None:
        assert score_rating("Overweight", 0.05) == "hit"
        assert score_rating("Buy", -0.03) == "miss"

    def test_bearish_hit_on_down(self) -> None:
        assert score_rating("Sell", -0.04) == "hit"
        assert score_rating("Underweight", 0.02) == "miss"

    def test_hold_neutral_or_hit(self) -> None:
        assert score_rating("Hold", 0.005) == "hit"      # stayed flat -> correct
        assert score_rating("Hold", 0.08) == "neutral"   # big move -> not scored


def test_summarize_hit_rate() -> None:
    rs = [
        PointResult("AAPL", "2025-01-01", "Overweight", 0.05, "hit"),
        PointResult("NVDA", "2025-01-01", "Buy", -0.02, "miss"),
        PointResult("XOM", "2025-01-01", "Sell", -0.03, "hit"),
        PointResult("V", "2025-01-01", "Hold", 0.10, "neutral"),
    ]
    s = summarize(rs)
    assert s["directional_calls"] == 3   # the Hold is excluded
    assert s["hits"] == 2
    assert s["hit_rate_pct"] == round(2 / 3 * 100, 1)
    assert s["neutral_holds"] == 1


def test_summarize_empty() -> None:
    assert summarize([])["hit_rate_pct"] is None
