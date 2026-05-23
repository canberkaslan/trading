"""Smoke tests for dataflow clients.

Skipped if API keys / network unavailable. Run locally with .env loaded:

    cd agent && uv run pytest tests/test_dataflows_smoke.py -v -s
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest


def _load_env_at_import() -> None:
    """Load .env before pytest evaluates skipif markers (collection time)."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"')
            if v:  # don't override real env with empty placeholders
                os.environ.setdefault(k, v)


_load_env_at_import()


@pytest.mark.skipif(not os.environ.get("POLYGON_API_KEY"), reason="POLYGON_API_KEY not set")
def test_polygon_prev_close_aapl() -> None:
    from tradingagents_us.dataflows.polygon import PolygonClient

    with PolygonClient() as poly:
        resp = poly.previous_close("AAPL")
    assert resp["status"] == "OK"
    assert resp["results"][0]["c"] > 0


@pytest.mark.skipif(not os.environ.get("POLYGON_API_KEY"), reason="POLYGON_API_KEY not set")
def test_polygon_aggregates_aapl_last_week() -> None:
    from tradingagents_us.dataflows.polygon import PolygonClient

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=10)
    with PolygonClient() as poly:
        bars = poly.aggregates("AAPL", start, end)
    assert len(bars) >= 3  # at least a few trading days
    assert all(b.close > 0 for b in bars)


@pytest.mark.skipif(not os.environ.get("SEC_EDGAR_USER_AGENT"), reason="SEC_EDGAR_USER_AGENT not set")
def test_edgar_ticker_to_cik_aapl() -> None:
    from tradingagents_us.dataflows.sec_edgar import EdgarClient

    with EdgarClient() as e:
        cik = e.ticker_to_cik("AAPL")
    assert cik == "0000320193"


@pytest.mark.skipif(not os.environ.get("SEC_EDGAR_USER_AGENT"), reason="SEC_EDGAR_USER_AGENT not set")
def test_edgar_recent_10q_aapl() -> None:
    from tradingagents_us.dataflows.sec_edgar import EdgarClient

    with EdgarClient() as e:
        cik = e.ticker_to_cik("AAPL")
        filings = e.recent_filings(cik, forms=("10-Q", "10-K"), since=date(2024, 1, 1))
    assert len(filings) >= 1
    assert filings[0].form in {"10-K", "10-Q"}


@pytest.mark.skipif(
    not (os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET")),
    reason="ALPACA_API_KEY/SECRET not set",
)
def test_alpaca_account_active() -> None:
    from tradingagents_us.dataflows.alpaca_broker import AlpacaClient

    with AlpacaClient() as a:
        acct = a.account()
    assert acct.status == "ACTIVE"
    assert acct.currency == "USD"
    assert acct.cash >= 0


@pytest.mark.skipif(
    not (os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET")),
    reason="ALPACA_API_KEY/SECRET not set",
)
def test_alpaca_clock() -> None:
    from tradingagents_us.dataflows.alpaca_broker import AlpacaClient

    with AlpacaClient() as a:
        clock = a.clock()
    assert isinstance(clock.is_open, bool)
    assert clock.next_open and clock.next_close


def test_sp500_history_survivor_safe() -> None:
    """Survivor-safe S&P 500 universe reconstruction (Wikipedia scrape)."""
    from datetime import date as _date

    from tradingagents_us.dataflows.sp500_history import (
        fetch_changes,
        fetch_current_constituents,
        members_as_of,
    )

    try:
        current = fetch_current_constituents()
        changes = fetch_changes()
    except Exception as e:
        pytest.skip(f"network/wiki fetch failed: {e}")

    # Sanity: current list is ~500 stocks
    assert 480 <= len(current) <= 520
    assert "AAPL" in set(current["symbol"])

    # GFC-era removals should be captured
    casualties_2008 = {c.removed for c in changes if c.effective_date.year == 2008 and c.removed}
    assert "LEH" in casualties_2008, "Lehman 2008 removal must be in changes"
    assert "FNM" in casualties_2008 or "FRE" in casualties_2008

    # Point-in-time: LEH was a member at end of 2007, not at end of 2010
    m_2007 = members_as_of(_date(2007, 12, 31), changes, current)
    m_2010 = members_as_of(_date(2010, 12, 31), changes, current)
    assert "LEH" in m_2007
    assert "LEH" not in m_2010
    assert "AAPL" in m_2007 and "AAPL" in m_2010
