"""/v1/prices — daily OHLCV bars for charts.

Proxies Polygon so the API key never reaches the mobile client. Polygon's free
tier is 5 req/min, so bars are cached twice: in-process for a few minutes, and
in SQLite per (ticker, date) so a restart or deploy doesn't send one upstream
request per rendered chart into that budget.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tradingagents_us.dataflows.polygon import PolygonClient
from tradingagents_us.storage import price_cache

from ..deps import get_repo, require_token

log = logging.getLogger(__name__)

router = APIRouter()

_CACHE_TTL_S = 300.0
_cache: dict[str, tuple[float, PriceSeries]] = {}
_lock = Lock()

# One lock per cache key so that N simultaneous misses for the same ticker
# collapse into a single upstream fetch. Without it, a screen that renders a
# sparkline per position fires one Polygon call per row before any of them can
# populate the cache — which on the free tier's 5-requests-per-minute budget is
# an immediate 429 cascade.
_inflight: dict[str, Lock] = {}
_inflight_guard = Lock()

# The cache is keyed by (ticker, days) and days varies per client, so bound it
# rather than letting it grow with every window any caller ever asks for.
_CACHE_MAX = 256


def _key_lock(key: str) -> Lock:
    with _inflight_guard:
        lock = _inflight.get(key)
        if lock is None:
            lock = Lock()
            _inflight[key] = lock
        return lock


def _cache_get(key: str, now: float) -> PriceSeries | None:
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < _CACHE_TTL_S:
            return hit[1]
    return None


def _cache_put(key: str, now: float, series: PriceSeries) -> None:
    with _lock:
        if len(_cache) >= _CACHE_MAX:
            # Drop the oldest entries rather than unbounded growth.
            for stale in sorted(_cache, key=lambda k: _cache[k][0])[: _CACHE_MAX // 4]:
                _cache.pop(stale, None)
        _cache[key] = (now, series)


class Bar(BaseModel):
    t: str  # ISO date
    o: float
    h: float
    l: float  # noqa: E741 — Polygon's OHLCV key, and the mobile chart reads it by this name
    c: float
    v: float


class PriceSeries(BaseModel):
    ticker: str
    bars: list[Bar]
    first: float | None
    last: float | None
    change_pct: float | None


def _series(ticker: str, bars: list[Bar], days: int) -> PriceSeries:
    bars = bars[-days:]
    first = bars[0].c if bars else None
    last = bars[-1].c if bars else None
    change = (last / first - 1.0) * 100.0 if first and last else None
    return PriceSeries(ticker=ticker, bars=bars, first=first, last=last, change_pct=change)


def _load(ticker: str, days: int) -> PriceSeries:
    to_d = date.today()
    # Pad the window so weekends/holidays still yield ~`days` trading bars.
    from_d = to_d - timedelta(days=int(days * 1.6) + 5)

    # Serve from the persistent bar cache when the trailing bar is recent
    # enough. This is what survives a restart, so a deploy no longer sends one
    # Polygon request per rendered sparkline into a 5-per-minute budget.
    try:
        with get_repo().session() as s:
            cached = price_cache.read_bars(s, ticker, from_d, to_d)
            if price_cache.is_fresh(cached):
                return _series(
                    ticker,
                    [
                        Bar(t=r.bar_date, o=r.open, h=r.high, l=r.low, c=r.close, v=r.volume)
                        for r in cached
                    ],
                    days,
                )
    except Exception:  # noqa: BLE001 — a cache fault must never fail the request
        log.warning("price cache read failed for %s; falling through to Polygon", ticker)

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
    ]

    try:
        with get_repo().session() as s:
            price_cache.write_bars(s, ticker, [b.model_dump() for b in bars])
    except Exception:  # noqa: BLE001 — caching is best-effort
        log.warning("price cache write failed for %s", ticker)

    return _series(ticker, bars, days)


# Deliberately `def`, not `async def`: `_load` is synchronous and can block for
# tens of seconds (Polygon paginates with a 12s sleep between pages, and a 429
# backs off). On the event loop that would stall every other request in the
# process; as a plain def, FastAPI runs it in the threadpool instead.
@router.get("/{ticker}", response_model=PriceSeries)
def get_prices(
    ticker: str,
    days: int = 60,
    user: str = Depends(require_token),
) -> PriceSeries:
    sym = ticker.strip().upper()
    if not sym.isalpha() or len(sym) > 6:
        raise HTTPException(422, f"invalid ticker: {ticker!r}")
    days = max(5, min(days, 365))
    key = f"{sym}:{days}"

    cached = _cache_get(key, time.time())
    if cached is not None:
        return cached

    with _key_lock(key):
        # Whoever held the lock may have just filled it; re-check before paying
        # for a second identical fetch.
        cached = _cache_get(key, time.time())
        if cached is not None:
            return cached
        series = _load(sym, days)
        _cache_put(key, time.time(), series)
    return series
