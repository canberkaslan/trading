"""Tests for the static GICS sector map (display-only, off trading path)."""

from tradingagents_us.dataflows.bulk_loader import US_UNIVERSE
from tradingagents_us.dataflows.sector_map import sector_for


def test_every_universe_ticker_has_a_sector():
    missing = [t for t in US_UNIVERSE if sector_for(t) is None]
    assert missing == [], f"universe tickers without a sector: {missing}"


def test_known_tickers():
    assert sector_for("AAPL") == "Information Technology"
    assert sector_for("JPM") == "Financials"
    assert sector_for("XOM") == "Energy"


def test_case_and_dash_normalization():
    assert sector_for("aapl") == "Information Technology"
    assert sector_for("brk-b") == sector_for("BRK.B") == "Financials"


def test_unknown_and_empty():
    assert sector_for("ZZZZ") is None
    assert sector_for("") is None
    assert sector_for(None) is None
