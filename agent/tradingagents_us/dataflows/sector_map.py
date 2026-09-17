"""GICS sector lookup for the US equity universe.

Dynamically loads sector metadata from Wikipedia's S&P 500 constituent list and
caches it in memory. Tickers not in the scraped map are bucketed as "Unknown"
rather than None, so portfolio sector caps apply to them: the cap prevents
unlimited concentration in unmapped tickers.

Source: S&P 500 GICS sector per constituent, via sp500_history.fetch_current_constituents().
"""

from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)

# Static fallback for the core 20-ticker universe, used when Wikipedia scrape
# fails (network down, table schema changed). Kept so the system degrades
# gracefully rather than refusing every trade when offline.
_FALLBACK_SECTOR_MAP: dict[str, str] = {
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


_CACHED_SECTOR_MAP: dict[str, str] | None = None


def _load_sector_map() -> dict[str, str]:
    """Fetch current S&P 500 constituents from Wikipedia and build sector map.

    Cached in-process only when the scrape succeeds. A failed scrape is retried
    on the next call rather than permanently caching the fallback — a transient
    network blip during deployment must not degrade the system for the rest of
    the process lifetime.

    Starts with fallback map (ETFs + core tickers), then merges Wikipedia data.
    """
    global _CACHED_SECTOR_MAP
    if _CACHED_SECTOR_MAP is not None:
        return _CACHED_SECTOR_MAP

    sector_map = _FALLBACK_SECTOR_MAP.copy()
    try:
        from .sp500_history import fetch_current_constituents

        df = fetch_current_constituents()
        for _, row in df.iterrows():
            sym = str(row.get("symbol", "")).strip().upper().replace(".", "-")
            sector = str(row.get("gics_sector", "")).strip()
            if sym and sector and sector.lower() != "nan":
                # Normalize Alpaca's dash form (BRK-B) to match either spelling
                sector_map[sym] = sector
                # Also store dot form so lookups work both ways
                if "-" in sym:
                    sector_map[sym.replace("-", ".")] = sector
        log.info("loaded %d sectors from S&P 500 constituents + fallback", len(sector_map))
        _CACHED_SECTOR_MAP = sector_map  # Cache only on success
    except Exception as e:
        log.warning("sector map scrape failed (%s), using fallback only — will retry next call", e)
        # Do NOT cache the fallback: a transient network error should not
        # permanently degrade the universe to 20 tickers
    return sector_map


def sector_for(ticker: str | None) -> str:
    """Return the GICS sector for ``ticker`` or "Unknown" if unmapped.

    Normalizes case and treats Alpaca's dash form (``BRK-B``) as the dot form
    (``BRK.B``) so either spelling resolves.

    Returns "Unknown" instead of None when the ticker is not in the map, so
    portfolio sector caps still operate: the cap prevents unlimited exposure to
    tickers that lack sector metadata rather than silently exempting them.
    """
    if not ticker:
        return "Unknown"
    key = ticker.strip().upper().replace("-", ".")
    sector_map = _load_sector_map()
    return sector_map.get(key, "Unknown")
