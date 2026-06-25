"""Finnhub dataflow — parsing, tilt logic, graceful degradation (no network)."""

from __future__ import annotations

import pytest

from tradingagents_us.dataflows.finnhub import (
    Recommendation,
    _fmt_earnings,
    _fmt_recommendation,
    finnhub_block,
)


def _rec(sb: int, b: int, h: int, s: int, ss: int) -> Recommendation:
    return Recommendation("2026-06-01", sb, b, h, s, ss)


class TestTilt:
    def test_bullish(self) -> None:
        assert _rec(14, 24, 15, 2, 0).tilt() == "bullish"

    def test_bearish(self) -> None:
        assert _rec(0, 1, 5, 10, 6).tilt() == "bearish"

    def test_mixed(self) -> None:
        assert _rec(5, 5, 5, 5, 5).tilt() == "mixed"

    def test_total(self) -> None:
        assert _rec(14, 24, 15, 2, 0).total == 55


class TestFormatting:
    def test_recommendation_summary(self) -> None:
        out = _fmt_recommendation([_rec(14, 24, 15, 2, 0)])
        assert "BULLISH" in out
        assert "StrongBuy 14" in out
        assert "n=55" in out

    def test_recommendation_empty(self) -> None:
        assert "no analyst" in _fmt_recommendation([])

    def test_earnings_beat_miss(self) -> None:
        rows = [
            {"period": "2026-03-31", "actual": 2.01, "estimate": 1.98, "surprisePercent": 1.1},
            {"period": "2025-12-31", "actual": 1.0, "estimate": 1.2, "surprisePercent": -16.7},
        ]
        out = _fmt_earnings(rows)
        assert "BEAT" in out and "MISS" in out
        assert "+1.1%" in out

    def test_earnings_empty(self) -> None:
        assert "no earnings" in _fmt_earnings([])


class TestDegradation:
    def test_no_key_returns_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        out = finnhub_block("AAPL")
        assert "FINNHUB_API_KEY not set" in out
