"""GICS sector lookup: static map first, Polygon fallback, DB cache.

Static map covers US_UNIVERSE (S&P 500 subset). Screener-sourced tickers
(from /v1/market's ~12,500 names) are not in that map, so sector cap checks
would be skipped. This module now fetches from Polygon's ticker_details on
cache miss and stores in sector_cache table to avoid re-fetching.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

# ticker -> GICS sector. Tickers are stored upper-cased with dots kept
# (e.g. "BRK.B") to match how Alpaca reports symbols.
_SECTOR_MAP: dict[str, str] = {
    "SPY": "Broad Market",
    "AAPL": "Information Technology",
    "MSFT": "Information Technology",
    "NVDA": "Information Technology",
    "AVGO": "Information Technology",
    "V": "Financials",
    "MA": "Financials",
    "JPM": "Financials",
    "BRK.B": "Financials",
    "GOOGL": "Communication Services",
    "META": "Communication Services",
    "AMZN": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "HD": "Consumer Discretionary",
    "COST": "Consumer Staples",
    "PG": "Consumer Staples",
    "LLY": "Health Care",
    "UNH": "Health Care",
    "JNJ": "Health Care",
    "ABBV": "Health Care",
    "XOM": "Energy",
}


log = logging.getLogger(__name__)


def sector_for(ticker: str | None) -> str | None:
    """Return the GICS sector for ``ticker`` or ``None`` if unknown.

    Lookup order:
    1. Static map (US_UNIVERSE tickers)
    2. DB cache (sector_cache table)
    3. Polygon ticker_details API (writes to cache for next time)

    Normalizes case and treats Alpaca's dash form (``BRK-B``) as the dot form
    (``BRK.B``) so either spelling resolves.
    """
    if not ticker:
        return None
    key = ticker.strip().upper().replace("-", ".")

    # 1. Static map
    if key in _SECTOR_MAP:
        return _SECTOR_MAP[key]

    # 2. DB cache
    try:
        from ..storage import TradeLogRepository
        from ..storage.models import SectorCacheRow

        repo = TradeLogRepository()
        with repo.session() as s:
            row = s.query(SectorCacheRow).filter_by(ticker=key).first()
            if row:
                return row.sector
    except Exception:  # noqa: BLE001
        log.warning("sector cache read failed for %s", key, exc_info=True)
        # Continue to Polygon rather than returning None on DB error

    # 3. Polygon API
    try:
        from .polygon import PolygonClient

        client = PolygonClient()
        resp = client.ticker_details(key)
        client.close()
        sector = resp.get("results", {}).get("sic_description")
        if not sector:
            log.info("Polygon returned no sector for %s", key)
            return None

        # Write to cache (re-create repo in case DB read failed earlier)
        try:
            from ..storage import TradeLogRepository
            from ..storage.models import SectorCacheRow

            cache_repo = TradeLogRepository()
            with cache_repo.session() as s:
                s.merge(
                    SectorCacheRow(
                        ticker=key, sector=sector, fetched_at_utc=datetime.now(UTC)
                    )
                )
                s.commit()
        except Exception:  # noqa: BLE001
            log.warning("sector cache write failed for %s", key, exc_info=True)

        return sector
    except Exception:  # noqa: BLE001
        log.warning("Polygon sector lookup failed for %s", key, exc_info=True)
        return None
