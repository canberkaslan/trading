"""Persistent daily-bar cache in front of Polygon.

Polygon's free tier allows five requests a minute. The in-process TTL cache in
the prices route covers repeat views inside one process lifetime, but it dies on
every restart — and `install.sh` restarts the service on every deploy, so the
cold-cache stampede lands exactly when a deploy does. These rows survive that.

The unit stored is the bar, not the window. A closed session's OHLCV never
changes, so past rows need no expiry at all; only the trailing bar can still be
moving, and `fetched_at_utc` on that row is what decides whether to refresh.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import PriceBarRow

# How stale the most recent bar may be before we go back to Polygon. Fifteen
# minutes is well inside a trading session's usefulness while still collapsing
# a burst of sparkline requests into one upstream call.
TRAILING_BAR_TTL = timedelta(minutes=15)


def read_bars(session: Session, ticker: str, start: date, end: date) -> list[PriceBarRow]:
    """Cached bars for the window, oldest first."""
    stmt = (
        select(PriceBarRow)
        .where(
            PriceBarRow.ticker == ticker,
            PriceBarRow.bar_date >= start.isoformat(),
            PriceBarRow.bar_date <= end.isoformat(),
        )
        .order_by(PriceBarRow.bar_date)
    )
    return list(session.scalars(stmt))


def is_fresh(rows: list[PriceBarRow], *, now: datetime | None = None) -> bool:
    """True when the cached window can be served without calling Polygon.

    Only the newest row's age matters: everything behind it is a closed session
    and immutable. An empty window is never fresh — that is a cache miss, not a
    ticker with no history, and the two must not be conflated.
    """
    if not rows:
        return False
    now = now or datetime.now(UTC)
    newest = max(rows, key=lambda r: r.bar_date)
    fetched = newest.fetched_at_utc
    if fetched.tzinfo is None:  # SQLite hands back naive datetimes
        fetched = fetched.replace(tzinfo=UTC)
    return (now - fetched) < TRAILING_BAR_TTL


def write_bars(session: Session, ticker: str, bars: list[dict]) -> int:
    """Upsert bars keyed by (ticker, date). Returns how many rows were touched.

    `bars` entries carry `t` (ISO date), `o`, `h`, `l`, `c`, `v` — the shape the
    prices route already speaks, so the caller does not have to translate twice.
    """
    if not bars:
        return 0
    now = datetime.now(UTC)
    existing = {
        r.bar_date: r
        for r in session.scalars(
            select(PriceBarRow).where(
                PriceBarRow.ticker == ticker,
                PriceBarRow.bar_date.in_([b["t"] for b in bars]),
            )
        )
    }
    for b in bars:
        row = existing.get(b["t"])
        if row is None:
            row = PriceBarRow(ticker=ticker, bar_date=b["t"])
            session.add(row)
        row.open, row.high, row.low = b["o"], b["h"], b["l"]
        row.close, row.volume = b["c"], b.get("v", 0.0)
        row.fetched_at_utc = now
    session.commit()
    return len(bars)
