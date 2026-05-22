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
