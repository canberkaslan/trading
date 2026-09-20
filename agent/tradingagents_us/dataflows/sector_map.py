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


UNKNOWN_SECTOR = "Unknown"
"""Bucket for names steps 1-3 could not resolve. Never None — see sector_for."""

log = logging.getLogger(__name__)


# SIC major group (the code's first two digits) -> GICS sector.
#
# A table rather than an if-chain: the chain had fourteen returns and thirteen
# branches, which is both over the linter's limit and harder to audit against
# the SIC manual — a wrong number hides in a conditional, but stands out in a
# row. Ranges are expanded so lookup is a single dict hit.
_SIC_MAJOR_TO_GICS: dict[int, str] = {
    **dict.fromkeys(range(10, 15), "Energy"),
    **dict.fromkeys(range(15, 18), "Industrials"),
    # Food & tobacco, grocery stores
    **dict.fromkeys((20, 21, 54), "Consumer Staples"),
    # Apparel, furniture, lumber, paper, print, leather, misc mfg, wholesale, retail
    **dict.fromkeys(
        (22, 23, 25, 26, 27, 31, 39, 50, 51, 52, 53, 55, 56, 57, 59),
        "Consumer Discretionary",
    ),
    # Chemicals, petroleum, stone/glass, primary metals
    **dict.fromkeys((28, 29, 32, 33), "Materials"),
    # Machinery, electronics, transport equipment, instruments, and business
    # services (73 is where software sits in SIC).
    **dict.fromkeys((35, 36, 37, 38, 73), "Information Technology"),
    # Transport & utilities split: electric/gas/transit vs communications.
    **dict.fromkeys((40, 41, 42, 43), "Utilities"),
    **dict.fromkeys((44, 45, 46, 47, 48, 49), "Communication Services"),
    **dict.fromkeys(range(60, 68), "Financials"),
    # Hotels, auto services, misc repair, recreation
    **dict.fromkeys((70, 75, 76, 78, 79), "Consumer Discretionary"),
    80: "Health Care",
    # Engineering/accounting services
    87: "Industrials",
}


def _sic_to_gics(sic_code: int | None) -> str | None:
    """Map a SIC code to a GICS sector name.

    SIC (Standard Industrial Classification) is what Polygon's ticker_details
    returns; GICS is what the static map and the risk layer use. Mapped on the
    SIC's major group — its first two digits.

    Unmapped codes return None, meaning "this mapper could not classify it" —
    NOT "skip the cap". `sector_for` turns that into ``"Unknown"`` so the cap
    still applies; see its docstring for why that trade is taken.
    """
    if sic_code is None:
        return None
    return _SIC_MAJOR_TO_GICS.get(sic_code // 100)


@lru_cache(maxsize=1)
def _get_repo() -> TradeLogRepository:
    """Singleton repo to avoid opening a new DB connection per sector_for call."""
    return TradeLogRepository()


def _cached_sector(key: str) -> str | None:
    """The sector_cache row for ``key``, or None when there is no usable answer.

    Never raises: a missing table (a production instance that predates this
    code and has not restarted) or a dead connection must fall through to
    Polygon, not take down the caller.
    """
    try:
        repo = _get_repo()
        with repo.session() as s:
            row = s.query(SectorCacheRow).filter_by(ticker=key).first()
            return row.sector if row else None  # None = miss, caller continues
    except Exception:  # noqa: BLE001
        log.warning("sector cache read failed for %s", key, exc_info=True)
        return None


def _polygon_sector(key: str) -> str | None:
    """Ask Polygon for ``key``'s SIC code and map it to GICS. Never raises.

    The client is constructed INSIDE the try. Its __init__ reads
    os.environ["POLYGON_API_KEY"] and raises KeyError when that is unset, so
    constructing it outside made a missing key an exception out of `sector_for`
    — which is called from the position-sizing path in scripts/trade.py and from
    api/routes/portfolio.py. Every test covering this block mocked PolygonClient,
    so none of them ever ran the constructor.
    """
    client = None
    try:
        client = PolygonClient()
        resp = client.ticker_details(key)
        sic_code = resp.get("results", {}).get("sic_code")
        if sic_code is None:
            log.info("Polygon returned no SIC code for %s", key)
            return None
        sector = _sic_to_gics(int(sic_code))
        if sector is None:
            log.info("SIC %s (ticker %s) has no GICS mapping", sic_code, key)
        return sector
    except Exception:  # noqa: BLE001
        log.warning("Polygon sector lookup failed for %s", key, exc_info=True)
        return None
    finally:
        if client is not None:
            client.close()


def _cache_sector(key: str, sector: str) -> None:
    """Best-effort write-through. A failed cache write is not a failed lookup."""
    try:
        repo = _get_repo()
        with repo.session() as s:
            s.merge(SectorCacheRow(ticker=key, sector=sector, fetched_at_utc=datetime.now(UTC)))
            s.commit()
    except Exception:  # noqa: BLE001
        log.warning("sector cache write failed for %s", key, exc_info=True)


def sector_for(ticker: str | None) -> str:
    """Return the GICS sector for ``ticker``, or ``"Unknown"`` if unresolved.

    Lookup order:
    1. Static map (US_UNIVERSE tickers)
    2. DB cache (sector_cache table)
    3. Polygon ticker_details API (SIC code -> GICS mapping, written back to cache)
    4. ``"Unknown"``

    Never returns None, and step 4 is the point. `check_limits` used to carry
    `if sector:`, so an unresolved name SKIPPED the 30% sector cap entirely —
    unlimited concentration in exactly the names we know least about (B-8).
    Bucketing them together can reject a safe trade when the bucket is full;
    that false positive is the accepted price of the cap never being silently
    off. Steps 1-3 exist to keep the bucket small.

    Normalizes case and treats Alpaca's dash form (``BRK-B``) as the dot form
    (``BRK.B``) so either spelling resolves.
    """
    if not ticker:
        return UNKNOWN_SECTOR
    key = ticker.strip().upper().replace("-", ".")

    if key in _SECTOR_MAP:
        return _SECTOR_MAP[key]

    cached = _cached_sector(key)
    if cached:
        return cached

    sector = _polygon_sector(key)
    if sector:
        _cache_sector(key, sector)
    return sector or UNKNOWN_SECTOR
