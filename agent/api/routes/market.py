"""The market as a table: price, change, volume — not just a list of names.

/v1/tickers/browse answered "which symbols exist" with sixty names and nothing
else. The question it was actually asked is the one every market screen
answers: what moved today, and by how much.

Everything here comes from two Polygon grouped-daily calls — one per session,
about a second each, ~12,300 symbols apiece. Change is measured against the
PREVIOUS SESSION'S CLOSE, which is what "today's change" means everywhere it
is quoted. Measuring against the same day's open would be a different number
wearing the same name, and it would disagree with every other screen the
operator looks at.

S&P 500 membership and sector come from the constituents table this repo
already maintains. That is what makes "only the big names" answerable without
a second vendor.

A liquidity floor is applied to the movers, and it is not a detail. Ranked raw,
the gainers list is penny stocks: a real session here had FTFT at +179% on an
$8 quote. Those are real prints and useless as a watchlist — a screen whose
top row is an $8 stock that doubled is one the operator learns to ignore.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ..deps import require_token

log = logging.getLogger(__name__)

router = APIRouter()

# Sessions do not change once closed; an hour is a compromise between a stale
# board and refetching 12,000 rows for every viewer.
_TTL_S = 3600

# Dollar volume a name must trade to appear among the movers. Below this a
# percentage move is a handful of shares changing hands, which is a fact about
# nobody's position.
_MIN_DOLLAR_VOLUME = 20_000_000

# Sub-dollar quotes make the percentage arithmetic meaningless — a two-cent
# tick on a $0.30 stock is +7%.
_MIN_PRICE = 1.0

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}

Sort = Literal["volume", "gainers", "losers"]
Universe = Literal["all", "sp500"]


class Mover(BaseModel):
    ticker: str
    name: str
    price: float
    change_pct: float
    dollar_volume: float
    sector: str | None = None
    in_sp500: bool = False


def _sessions() -> list[tuple[date, list[dict]]]:
    """The last two completed sessions, newest first.

    Walks back a day at a time because the endpoint answers empty on weekends
    and holidays — reporting a closed market as an empty one would put every
    price at zero rather than saying nothing is new.
    """
    with _lock:
        held = _cache.get("sessions")
    if held and (time.time() - held[0]) < _TTL_S:
        return held[1]

    from tradingagents_us.dataflows.polygon import PolygonClient

    out: list[tuple[date, list[dict]]] = []
    with PolygonClient() as client:
        day = date.today()
        for _ in range(10):
            day -= timedelta(days=1)
            resp = client._get(
                f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
                {"adjusted": "true", "apiKey": client.api_key},
            )
            results = resp.get("results") or []
            if results:
                out.append((day, results))
            if len(out) == 2:
                break

    with _lock:
        _cache["sessions"] = (time.time(), out)
    return out


def _sp500() -> dict[str, dict[str, str]]:
    """Ticker -> {name, sector} for current S&P 500 members. Empty on failure.

    Empty rather than raising: the board is still worth showing without index
    labels, and a market screen that goes dark because Wikipedia moved a table
    would be a poor trade.
    """
    with _lock:
        held = _cache.get("sp500")
    if held and (time.time() - held[0]) < _TTL_S * 6:
        return held[1]

    table: dict[str, dict[str, str]] = {}
    try:
        from tradingagents_us.dataflows.sp500_history import fetch_current_constituents

        df = fetch_current_constituents()
        for _, row in df.iterrows():
            sym = str(row.get("symbol", "")).strip().upper().replace(".", "-")
            if sym:
                table[sym] = {
                    "name": str(row.get("security", "")),
                    "sector": str(row.get("gics_sector", "")) or None,
                }
    except Exception:  # noqa: BLE001
        log.warning("S&P 500 constituents unavailable; board loses index labels", exc_info=True)

    with _lock:
        _cache["sp500"] = (time.time(), table)
    return table


def build_rows(
    current: list[dict],
    previous: list[dict],
    names: dict[str, str],
    members: dict[str, dict[str, str]],
) -> list[Mover]:
    """Join two sessions into priced rows. Pure, so the arithmetic is testable."""
    prev_close = {
        str(b.get("T", "")).upper(): b.get("c")
        for b in previous
        if b.get("T") and b.get("c")
    }

    rows: list[Mover] = []
    for bar in current:
        ticker = str(bar.get("T", "")).upper()
        close = bar.get("c")
        volume = bar.get("v") or 0
        before = prev_close.get(ticker)
        # No prior close means no change to report. Showing 0% would claim the
        # name was flat, which is a different statement from "it is new here".
        if not ticker or not close or not before or before <= 0:
            continue

        member = members.get(ticker)
        rows.append(
            Mover(
                ticker=ticker,
                name=names.get(ticker) or (member or {}).get("name") or ticker,
                price=float(close),
                change_pct=(float(close) - float(before)) / float(before) * 100.0,
                dollar_volume=float(close) * float(volume),
                sector=(member or {}).get("sector"),
                in_sp500=member is not None,
            )
        )
    return rows


def select(rows: list[Mover], sort: Sort, universe: Universe, limit: int) -> list[Mover]:
    """Filter and order. Pure."""
    pool = [r for r in rows if r.in_sp500] if universe == "sp500" else rows

    if sort == "volume":
        # Volume ranks itself; a liquidity floor would only remove rows that
        # could never reach the top of it anyway.
        return sorted(pool, key=lambda r: r.dollar_volume, reverse=True)[:limit]

    # Movers need the floor. Without it this is a penny-stock board.
    liquid = [
        r for r in pool if r.dollar_volume >= _MIN_DOLLAR_VOLUME and r.price >= _MIN_PRICE
    ]
    return sorted(
        liquid, key=lambda r: r.change_pct, reverse=(sort == "gainers")
    )[:limit]


@router.get("/movers", response_model=list[Mover])
async def movers(
    sort: Sort = Query(default="volume"),
    universe: Universe = Query(default="all"),
    limit: int = Query(default=50, ge=1, le=250),
    _user: str = Depends(require_token),
) -> list[Mover]:
    """Today's board: most traded, biggest gainers, biggest losers."""
    try:
        sessions = _sessions()
        if len(sessions) < 2:
            raise RuntimeError("fewer than two completed sessions available")
        from .tickers import _load

        names = {r["ticker"]: r["name"] for r in _load()}
        return select(
            build_rows(sessions[0][1], sessions[1][1], names, _sp500()),
            sort,
            universe,
            limit,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("market board unavailable", exc_info=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"market data unavailable: {exc}",
        ) from exc
