"""GICS sector lookup: static map first, Polygon fallback, DB cache.

Static map covers US_UNIVERSE (S&P 500 subset). Screener-sourced tickers
(from /v1/market's ~12,500 names) are not in that map, so sector cap checks
would be skipped. This module now fetches from Polygon's ticker_details on
cache miss and stores in sector_cache table to avoid re-fetching.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import lru_cache

from ..storage import TradeLogRepository
from ..storage.models import SectorCacheRow
from .polygon import PolygonClient

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


def _sic_to_gics(sic_code: int | None) -> str | None:
    """Map SIC code to GICS sector name.

    SIC (Standard Industrial Classification) is what Polygon's ticker_details
    returns; GICS is what the static map and the risk layer use. Mapping by
    the SIC's first two digits (major group).

    Unmapped codes return None rather than a placeholder — an unknown sector
    must not be bucketed with other unknowns, as that would invent concentration
    between unrelated names.
    """
    if sic_code is None:
        return None

    major = sic_code // 100  # first two digits
    if 10 <= major <= 14:
        return "Energy"
    if 15 <= major <= 17:
        return "Industrials"
    if major in (20, 21, 54):  # Food & tobacco, grocery stores
        return "Consumer Staples"
    if major in (22, 23, 25, 26, 27, 31, 39, 50, 51, 52, 53, 55, 56, 57, 59):
        # Apparel, furniture, lumber, paper, print, leather, misc mfg, wholesale, retail
        return "Consumer Discretionary"
    if major in (28, 29, 32, 33):  # Chemicals, petroleum, stone/glass, primary metals
        return "Materials"
    if major in (35, 36, 37, 38, 73):  # Machinery, electronics, transport equip, instruments, business services (software)
        return "Information Technology"
    if 40 <= major <= 49:
        if major in (44, 45, 46, 47, 48, 49):  # Water, air, pipelines, communications
            return "Communication Services"
        return "Utilities"  # Electric, gas, transit
    if 60 <= major <= 67:
        return "Financials"
    if major in (70, 75, 76, 78, 79):  # Hotels, auto services, misc repair, recreation
        return "Consumer Discretionary"
    if major == 80:
        return "Health Care"
    if major == 87:  # Engineering/accounting services
        return "Industrials"
    # Unmapped: return None so the concentration cap skips rather than inventing
    # a shared bucket.
    return None


@lru_cache(maxsize=1)
def _get_repo() -> TradeLogRepository:
    """Singleton repo to avoid opening a new DB connection per sector_for call."""
    return TradeLogRepository()


def sector_for(ticker: str | None) -> str | None:
    """Return the GICS sector for ``ticker`` or ``None`` if unknown.

    Lookup order:
    1. Static map (US_UNIVERSE tickers)
    2. DB cache (sector_cache table)
    3. Polygon ticker_details API (SIC code → GICS mapping, writes to cache)

    Normalizes case and treats Alpaca's dash form (``BRK-B``) as the dot form
    (``BRK.B``) so either spelling resolves.
    """
    if not ticker:
        return None
    key = ticker.strip().upper().replace("-", ".")

    # 1. Static map
    if key in _SECTOR_MAP:
        return _SECTOR_MAP[key]

    # 2. DB cache (safe against table-not-found if production hasn't restarted)
    try:
        repo = _get_repo()
        with repo.session() as s:
            row = s.query(SectorCacheRow).filter_by(ticker=key).first()
            if row:
                return row.sector
    except Exception:  # noqa: BLE001
        # Catch both connection errors and OperationalError: no such table.
        # The table is created on TradeLogRepository init, but a running
        # production instance that predates this code won't have it until restart.
        log.warning("sector cache read failed for %s", key, exc_info=True)
        # Continue to Polygon rather than returning None on DB error

    # 3. Polygon API — fetch SIC code, map to GICS
    client = PolygonClient()
    try:
        resp = client.ticker_details(key)
        sic_code = resp.get("results", {}).get("sic_code")
        if sic_code is None:
            log.info("Polygon returned no SIC code for %s", key)
            return None

        sector = _sic_to_gics(int(sic_code))
        if sector is None:
            log.info("SIC %s (ticker %s) has no GICS mapping", sic_code, key)
            return None

        # Write to cache
        try:
            cache_repo = _get_repo()
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
    finally:
        client.close()
