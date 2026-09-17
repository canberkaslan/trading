"""Tests for the static GICS sector map (display-only, off trading path)."""

from unittest.mock import Mock, patch

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


def test_screener_ticker_gets_sector_from_polygon():
    """Non-universe tickers (from screener) should fetch sector from Polygon."""
    # INTC is a real ticker not in US_UNIVERSE static map
    mock_client = Mock()
    mock_client.ticker_details.return_value = {
        "results": {"sic_description": "SEMICONDUCTORS & RELATED DEVICES"}
    }

    # Mock TradeLogRepository to skip DB cache layer
    mock_repo = Mock()
    mock_session = Mock()
    mock_session.__enter__ = Mock(return_value=mock_session)
    mock_session.__exit__ = Mock(return_value=None)
    mock_session.query.return_value.filter_by.return_value.first.return_value = None
    mock_repo.session.return_value = mock_session

    with patch("tradingagents_us.dataflows.polygon.PolygonClient", return_value=mock_client), \
         patch("tradingagents_us.storage.TradeLogRepository", return_value=mock_repo):
        sector = sector_for("INTC")

    assert sector is not None, "INTC should have a sector from Polygon"
    assert sector == "SEMICONDUCTORS & RELATED DEVICES"
    mock_client.ticker_details.assert_called_once_with("INTC")
