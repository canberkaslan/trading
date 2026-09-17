"""Tests for GICS sector map with Wikipedia scraping and Unknown bucket."""

from tradingagents_us.dataflows.bulk_loader import US_UNIVERSE
from tradingagents_us.dataflows.sector_map import sector_for


def test_every_universe_ticker_has_a_sector():
    """Core universe tickers must resolve to a known GICS sector, not Unknown."""
    unknown = [t for t in US_UNIVERSE if sector_for(t) == "Unknown"]
    assert unknown == [], f"universe tickers bucketed as Unknown: {unknown}"


def test_known_tickers():
    assert sector_for("AAPL") == "Information Technology"
    assert sector_for("JPM") == "Financials"
    assert sector_for("XOM") == "Energy"


def test_case_and_dash_normalization():
    assert sector_for("aapl") == "Information Technology"
    assert sector_for("brk-b") == sector_for("BRK.B") == "Financials"


def test_unknown_and_empty_return_unknown_bucket():
    """Unmapped tickers return "Unknown" so sector caps still operate."""
    assert sector_for("ZZZZ") == "Unknown"
    assert sector_for("") == "Unknown"
    assert sector_for(None) == "Unknown"
