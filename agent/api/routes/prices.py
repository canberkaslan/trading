"""/v1/prices — daily OHLCV bars for charts.

Proxies Polygon so the API key never reaches the mobile client. Polygon's
free tier is 5 req/min, so results are cached in-process for a few minutes;
chart data doesn't need to be fresher than that.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tradingagents_us.dataflows.polygon import PolygonClient

from ..deps import require_token

router = APIRouter()

_CACHE_TTL_S = 300.0
_cache: dict[str, tuple[float, "PriceSeries"]] = {}
_lock = Lock()


class Bar(BaseModel):
    t: str  # ISO date
    o: float
    h: float
    l: float
    c: float
    v: float


class PriceSeries(BaseModel):
    ticker: str
    bars: list[Bar]
    first: float | None
    last: float | None
    change_pct: float | None


def _load(ticker: str, days: int) -> PriceSeries:
    to_d = date.today()
    # Pad the window so weekends/holidays still yield ~`days` trading bars.
    from_d = to_d - timedelta(days=int(days * 1.6) + 5)
    with PolygonClient() as pc:
        aggs = pc.aggregates(ticker, from_d, to_d, timespan="day")
    bars = [
        Bar(
            t=date.fromtimestamp(a.timestamp_ms / 1000).isoformat(),
            o=a.open,
            h=a.high,
            l=a.low,
            c=a.close,
            v=a.volume,
        )
        for a in aggs
    ][-days:]
    first = bars[0].c if bars else None
    last = bars[-1].c if bars else None
    change = (last / first - 1.0) * 100.0 if first and last else None
    return PriceSeries(ticker=ticker, bars=bars, first=first, last=last, change_pct=change)


@router.get("/{ticker}", response_model=PriceSeries)
async def get_prices(
    ticker: str,
    days: int = 60,
    user: str = Depends(require_token),
) -> PriceSeries:
    sym = ticker.strip().upper()
    if not sym.isalpha() or len(sym) > 6:
        raise HTTPException(422, f"invalid ticker: {ticker!r}")
    days = max(5, min(days, 365))
    key = f"{sym}:{days}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < _CACHE_TTL_S:
            return hit[1]
    series = _load(sym, days)
    with _lock:
        _cache[key] = (now, series)
    return series
