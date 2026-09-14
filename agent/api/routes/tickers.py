"""Ticker search, so a name can be found rather than remembered.

Analysing any US stock has always worked — /v1/analyze never checked a
universe, only the shape of the symbol. What did not work was FINDING one: the
Ask screen is a text box, so the operator had to already know the ticker. For
the eleven names in the daily run that is fine; for the other twelve thousand
it is the whole obstacle.

The catalogue comes from Polygon, which we already pay for, and is held in
memory for a day. It is a list of about 12,000 rows that changes on listings
and delistings — fetching it per keystroke would be absurd, and fetching it
per process start would make the first search pay for everyone's.

Search is a prefix match on the symbol, then a substring match on the company
name, ranked in that order. Someone typing "AAP" means AAPL far more often
than they mean a company with "aap" in the middle of its name, and a search
that buries the obvious answer teaches the operator to stop using it.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..deps import require_token

log = logging.getLogger(__name__)

router = APIRouter()

# A listing does not change intraday. One fetch a day is plenty, and it keeps
# a restarted process from re-paying immediately.
_TTL_S = 24 * 3600
_MAX_RESULTS = 20

_lock = threading.Lock()
# A one-slot box rather than a module global, so the cache can be replaced
# under the lock without `global` and a test can reach in to clear it.
_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


class TickerHit(BaseModel):
    ticker: str
    name: str


def _load() -> list[dict[str, str]]:
    """The catalogue, fetched at most once a day. Raises if it cannot be had."""
    with _lock:
        held = _cache.get("rows")
        if held and (time.time() - held[0]) < _TTL_S:
            return held[1]

    from tradingagents_us.dataflows.polygon import PolygonClient

    with PolygonClient() as client:
        raw: list[dict[str, Any]] = client.list_tickers(market="stocks", active=True)

    rows = [
        {"ticker": str(r.get("ticker", "")).upper(), "name": str(r.get("name", ""))}
        for r in raw
        if r.get("ticker")
    ]
    with _lock:
        _cache["rows"] = (time.time(), rows)
    return rows


def search(rows: list[dict[str, str]], q: str, limit: int = _MAX_RESULTS) -> list[dict[str, str]]:
    """Symbol prefix first, then name substring. Pure, so it can be tested."""
    needle = q.strip().upper()
    if not needle:
        return []

    by_symbol = [r for r in rows if r["ticker"].startswith(needle)]
    # Shorter symbols first: "AAPL" should beat "AAPLW" for the query "AAPL".
    by_symbol.sort(key=lambda r: (len(r["ticker"]), r["ticker"]))
    if len(by_symbol) >= limit:
        return by_symbol[:limit]

    seen = {r["ticker"] for r in by_symbol}
    by_name = [
        r for r in rows if needle in r["name"].upper() and r["ticker"] not in seen
    ]
    by_name.sort(key=lambda r: r["ticker"])
    return (by_symbol + by_name)[:limit]


# Names that mark an instrument as something other than a common share. A
# volume ranking is otherwise topped by leveraged ETFs, and "give me a stock"
# does not mean those.
_NOT_A_SHARE = (
    " ETF", "ETF ", " FUND", "TRUST", " ETN", "WARRANT", " UNITS", " UNIT ",
    "PREFERRED", " NOTES", " BOND", "INDEX",
)

# A dot means a class, warrant or series line (BRK.B, AAC.WS). The primary
# listing is what a picker should offer.
def _is_common_share(row: dict[str, str]) -> bool:
    if "." in row["ticker"]:
        return False
    name = row["name"].upper()
    return not any(marker in name for marker in _NOT_A_SHARE)


def _most_traded(limit: int) -> list[dict[str, str]]:
    """Catalogue rows for the highest dollar-volume common shares."""
    from tradingagents_us.dataflows.polygon import PolygonClient

    with _lock:
        held = _cache.get("volume")
    if held and (time.time() - held[0]) < _TTL_S:
        ranked = held[1]
    else:
        with PolygonClient() as client:
            bars = client.grouped_daily_previous()
        # close x volume: what actually changed hands, not share count. A $3
        # stock trading ten million shares is not more liquid than a $500 one
        # trading a million.
        ranked = sorted(
            (
                {
                    "ticker": str(b.get("T", "")).upper(),
                    "dv": float(b.get("c", 0)) * float(b.get("v", 0)),
                }
                for b in bars
                if b.get("T")
            ),
            key=lambda r: r["dv"],
            reverse=True,
        )
        with _lock:
            _cache["volume"] = (time.time(), ranked)

    names = {r["ticker"]: r["name"] for r in _load()}
    out: list[dict[str, str]] = []
    for r in ranked:
        name = names.get(r["ticker"])
        if name is None:
            continue
        row = {"ticker": r["ticker"], "name": name}
        if _is_common_share(row):
            out.append(row)
        if len(out) >= limit:
            break
    return out


@router.get("/browse", response_model=list[TickerHit])
async def browse_tickers(
    limit: int = Query(default=60, ge=1, le=200),
    _user: str = Depends(require_token),
) -> list[TickerHit]:
    """The most traded US stocks, for picking rather than searching.

    A list of thirteen thousand symbols is not a list anyone reads, so this is
    ranked by the previous session's dollar volume — the names an operator
    would plausibly ask about, in the order the market itself traded them.

    ETFs, warrants and units are filtered out. They dominate a raw
    volume ranking and none of them is what "give me a stock to analyse"
    means; a picker whose first screen is leveraged ETFs is a picker the
    operator scrolls past rather than uses.
    """
    try:
        return [TickerHit(**r) for r in _most_traded(limit)]
    except Exception as exc:  # noqa: BLE001
        log.warning("browse list unavailable", exc_info=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"ticker catalogue unavailable: {exc}",
        ) from exc


@router.get("", response_model=list[TickerHit])
async def search_tickers(
    q: str = Query(min_length=1, max_length=32, description="Symbol or company name"),
    _user: str = Depends(require_token),
) -> list[TickerHit]:
    """Matching US stocks, symbol matches first."""
    try:
        rows = _load()
    except Exception as exc:  # noqa: BLE001
        # The catalogue being unreachable is our problem, not a bad query, and
        # an empty list would read as "no such stock" — a false statement about
        # the market rather than a true one about us.
        log.warning("ticker catalogue unavailable", exc_info=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"ticker catalogue unavailable: {exc}",
        ) from exc

    return [TickerHit(**r) for r in search(rows, q)]
