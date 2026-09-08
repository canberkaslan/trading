"""Real market inputs for the risk checks that used to be fed placeholders.

Three of the caps in `portfolio_limits` were structurally unable to fire, not
because the checks were wrong but because the caller handed them constants: a
hardcoded $1B average daily volume, an empty per-sector book, and a correlation
count of zero. This module supplies the two that need price history, reading
from the same SQLite bar cache the prices route fills so a daily run does not
spend the Polygon free tier's five-requests-a-minute budget on them.

Every function here degrades to None rather than to a convenient number. A
liquidity figure that silently means "infinite" is exactly how the floor went
quiet in the first place; the caller decides what an unknown should mean.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from ..storage import price_cache
from ..storage.repository import TradeLogRepository

log = logging.getLogger(__name__)

# Enough history for a stable average without asking for a year of bars.
_ADV_WINDOW_DAYS = 20
# Correlation over a quarter: long enough to be meaningful, short enough to
# reflect the regime the book is actually in.
_CORR_WINDOW_DAYS = 90
# |rho| above this counts as "these two move together".
_CORR_THRESHOLD = 0.7
# Below this many overlapping sessions a correlation is noise, not signal.
_MIN_OVERLAP = 20


def _bars(repo: TradeLogRepository, ticker: str, days: int) -> list:
    start = date.today() - timedelta(days=int(days * 1.6) + 5)
    with repo.session() as s:
        return price_cache.read_bars(s, ticker.upper(), start, date.today())


def average_dollar_volume(
    ticker: str,
    *,
    repo: TradeLogRepository | None = None,
    window: int = _ADV_WINDOW_DAYS,
) -> float | None:
    """Mean daily dollar volume over the recent window, or None if unknown.

    Dollar volume rather than share count, because the liquidity floor is
    denominated in dollars and a $5 stock and a $500 stock trading the same
    number of shares are not equally liquid.
    """
    try:
        repo = repo or TradeLogRepository()
        rows = _bars(repo, ticker, window)
    except Exception:  # noqa: BLE001 — a data gap must not break the trading run
        log.warning("ADV lookup failed for %s", ticker, exc_info=True)
        return None
    recent = rows[-window:]
    if not recent:
        return None
    return sum(r.close * r.volume for r in recent) / len(recent)


def _returns(rows: list) -> dict[str, float]:
    """Daily returns keyed by bar date, so two series can be aligned on dates
    rather than on position — a name that did not trade on a holiday would
    otherwise shift the whole series and correlate against the wrong days."""
    out: dict[str, float] = {}
    prev = None
    for r in rows:
        if prev is not None and prev.close > 0:
            out[r.bar_date] = r.close / prev.close - 1.0
        prev = r
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < _MIN_OVERLAP:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    if dx == 0 or dy == 0:  # a flat series has no correlation to speak of
        return None
    return num / (dx * dy)


def count_correlated(
    candidate: str,
    held: list[str],
    *,
    repo: TradeLogRepository | None = None,
    threshold: float = _CORR_THRESHOLD,
) -> int:
    """How many currently-held names move with `candidate` above `threshold`.

    Returns 0 when the candidate's own history is unavailable — the same value
    the caller used to hardcode, but reached honestly: with no series there is
    nothing to correlate, and refusing every order on a data gap would be worse
    than letting the other caps carry it.
    """
    others = [h for h in {h.upper() for h in held} if h.upper() != candidate.upper()]
    if not others:
        return 0
    try:
        repo = repo or TradeLogRepository()
        base = _returns(_bars(repo, candidate, _CORR_WINDOW_DAYS))
        if len(base) < _MIN_OVERLAP:
            return 0
        n = 0
        for sym in others:
            other = _returns(_bars(repo, sym, _CORR_WINDOW_DAYS))
            shared = sorted(set(base) & set(other))
            rho = _pearson([base[d] for d in shared], [other[d] for d in shared])
            if rho is not None and abs(rho) >= threshold:
                n += 1
        return n
    except Exception:  # noqa: BLE001 — never break the run on a data gap
        log.warning("correlation count failed for %s", candidate, exc_info=True)
        return 0
