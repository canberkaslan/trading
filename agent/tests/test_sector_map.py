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


def test_unknown_and_empty_fall_into_the_unknown_bucket():
    """Nothing resolves to None any more — see sector_for's docstring for why.

    `check_limits` no longer guards on a truthy sector, so a None here would be
    a dict key of None in the sector-exposure map, not a skipped cap.
    """
    # Empty/None inputs short-circuit before any lookup
    assert sector_for("") == "Unknown"
    assert sector_for(None) == "Unknown"

    # ZZZZ not in static map → tries Polygon → no SIC code → None
    mock_client = Mock()
    mock_client.ticker_details.return_value = {"results": {}}  # no sic_code
    mock_client.close = Mock()
    mock_repo = Mock()
    mock_session = Mock()
    mock_session.__enter__ = Mock(return_value=mock_session)
    mock_session.__exit__ = Mock(return_value=None)
    mock_session.query.return_value.filter_by.return_value.first.return_value = None
    mock_repo.session.return_value = mock_session

    with patch("tradingagents_us.dataflows.sector_map.PolygonClient", return_value=mock_client), \
         patch("tradingagents_us.dataflows.sector_map._get_repo", return_value=mock_repo):
        assert sector_for("ZZZZ") == "Unknown"


def test_screener_ticker_gets_sector_from_polygon():
    """Non-universe tickers (from screener) should map SIC → GICS via Polygon."""
    # INTC is a real ticker not in US_UNIVERSE static map.
    # SIC 3674 = Semiconductors → GICS Information Technology
    mock_client = Mock()
    mock_client.ticker_details.return_value = {
        "results": {"sic_code": 3674}
    }
    mock_client.close = Mock()

    # Mock TradeLogRepository._get_repo to skip DB cache layer
    mock_repo = Mock()
    mock_session = Mock()
    mock_session.__enter__ = Mock(return_value=mock_session)
    mock_session.__exit__ = Mock(return_value=None)
    mock_session.query.return_value.filter_by.return_value.first.return_value = None
    mock_repo.session.return_value = mock_session

    with patch("tradingagents_us.dataflows.sector_map.PolygonClient", return_value=mock_client), \
         patch("tradingagents_us.dataflows.sector_map._get_repo", return_value=mock_repo):
        sector = sector_for("INTC")

    assert sector is not None, "INTC should have a sector from Polygon"
    assert sector == "Information Technology", \
        "SIC 3674 (semiconductors) should map to Information Technology"
    mock_client.ticker_details.assert_called_once_with("INTC")


def test_screener_and_static_map_agree_on_same_sector():
    """INTC (screener, SIC 3674) and NVDA (static map) must return the SAME GICS sector.

    This is the core contract: concentration caps only work if tickers in the
    same industry return identical strings, whether from the static map or
    Polygon. NVDA is in the static map as "Information Technology"; INTC comes
    from Polygon with SIC 3674 (semiconductors), which must map to the same string.
    """
    # NVDA is in the static map
    nvda_sector = sector_for("NVDA")
    assert nvda_sector == "Information Technology"

    # INTC fetched from Polygon, SIC 3674
    mock_client = Mock()
    mock_client.ticker_details.return_value = {"results": {"sic_code": 3674}}
    mock_client.close = Mock()
    mock_repo = Mock()
    mock_session = Mock()
    mock_session.__enter__ = Mock(return_value=mock_session)
    mock_session.__exit__ = Mock(return_value=None)
    mock_session.query.return_value.filter_by.return_value.first.return_value = None
    mock_repo.session.return_value = mock_session

    with patch("tradingagents_us.dataflows.sector_map.PolygonClient", return_value=mock_client), \
         patch("tradingagents_us.dataflows.sector_map._get_repo", return_value=mock_repo):
        intc_sector = sector_for("INTC")

    assert intc_sector == nvda_sector, \
        f"INTC (Polygon SIC 3674) returned {intc_sector!r}, NVDA (static) is {nvda_sector!r}. " \
        "Concentration cap requires identical strings."
