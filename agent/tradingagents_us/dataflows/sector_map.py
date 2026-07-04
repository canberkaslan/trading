"""Static GICS sector lookup for the fixed US universe.

Display-only metadata for the portfolio snapshot / mobile UI — this is off the
trading path and never feeds a decision. Kept static (no network) so the
snapshot endpoint stays fast and offline-safe. Source: GICS sector per
S&P 500 constituent, aligned with ``bulk_loader.US_UNIVERSE``.
"""

from __future__ import annotations

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


def sector_for(ticker: str | None) -> str | None:
    """Return the GICS sector for ``ticker`` or ``None`` if unknown.

    Normalizes case and treats Alpaca's dash form (``BRK-B``) as the dot form
    (``BRK.B``) so either spelling resolves.
    """
    if not ticker:
        return None
    key = ticker.strip().upper().replace("-", ".")
    return _SECTOR_MAP.get(key)
