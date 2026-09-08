"""The persistent bar cache, and the two rate-limit bugs it sits behind.

Polygon's free tier is five requests a minute, so each of these guards a way we
used to spend that budget on nothing: a 404 retried five times, a burst of
identical requests fetched N times, and a restart re-fetching what we already
had on disk.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tradingagents_us.dataflows.polygon import PolygonClient
from tradingagents_us.storage import price_cache
from tradingagents_us.storage.models import Base, PriceBarRow


@pytest.fixture
def session(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'cache.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        yield s


def _bar(day: str, close: float) -> dict:
    return {"t": day, "o": close, "h": close, "l": close, "c": close, "v": 1000.0}


def test_write_then_read_round_trips(session: Session) -> None:
    price_cache.write_bars(session, "AAPL", [_bar("2026-09-01", 220.0), _bar("2026-09-02", 221.5)])
    rows = price_cache.read_bars(session, "AAPL", date(2026, 9, 1), date(2026, 9, 2))
    assert [r.bar_date for r in rows] == ["2026-09-01", "2026-09-02"]
    assert rows[1].close == 221.5


def test_rewriting_a_day_updates_in_place(session: Session) -> None:
    """A day re-fetched must correct the row, not append a duplicate."""
    price_cache.write_bars(session, "AAPL", [_bar("2026-09-01", 220.0)])
    price_cache.write_bars(session, "AAPL", [_bar("2026-09-01", 224.0)])
    rows = price_cache.read_bars(session, "AAPL", date(2026, 9, 1), date(2026, 9, 1))
    assert len(rows) == 1
    assert rows[0].close == 224.0


def test_windows_share_storage(session: Session) -> None:
    """A 60-day and a 30-day request read the same rows — one fetch, not two."""
    price_cache.write_bars(
        session, "MSFT", [_bar(f"2026-08-{d:02d}", 400.0 + d) for d in range(1, 29)]
    )
    wide = price_cache.read_bars(session, "MSFT", date(2026, 8, 1), date(2026, 8, 28))
    narrow = price_cache.read_bars(session, "MSFT", date(2026, 8, 20), date(2026, 8, 28))
    assert len(wide) == 28
    assert len(narrow) == 9
    assert {r.bar_date for r in narrow} <= {r.bar_date for r in wide}


def test_tickers_are_isolated(session: Session) -> None:
    price_cache.write_bars(session, "AAPL", [_bar("2026-09-01", 220.0)])
    price_cache.write_bars(session, "MSFT", [_bar("2026-09-01", 400.0)])
    rows = price_cache.read_bars(session, "AAPL", date(2026, 9, 1), date(2026, 9, 1))
    assert len(rows) == 1 and rows[0].close == 220.0


def test_empty_window_is_not_fresh() -> None:
    """A miss must not read as 'a ticker with no history' — that would cache a
    permanently empty chart for a symbol whose bars we simply haven't fetched."""
    assert price_cache.is_fresh([]) is False


def test_a_just_written_window_is_fresh(session: Session) -> None:
    price_cache.write_bars(session, "AAPL", [_bar("2026-09-01", 220.0)])
    rows = price_cache.read_bars(session, "AAPL", date(2026, 9, 1), date(2026, 9, 1))
    assert price_cache.is_fresh(rows) is True


def test_window_goes_stale_past_the_ttl(session: Session) -> None:
    price_cache.write_bars(session, "AAPL", [_bar("2026-09-01", 220.0)])
    rows = price_cache.read_bars(session, "AAPL", date(2026, 9, 1), date(2026, 9, 1))
    later = datetime.now(UTC) + price_cache.TRAILING_BAR_TTL + timedelta(minutes=1)
    assert price_cache.is_fresh(rows, now=later) is False


def test_an_old_backfill_does_not_make_the_window_stale(session: Session) -> None:
    """Freshness follows the NEWEST bar, not the oldest. Backfilling history
    behind an already-current trailing bar must not force a refetch — that is
    the case that would re-spend the budget every time the window widened."""
    price_cache.write_bars(session, "AAPL", [_bar("2026-09-02", 221.0)])
    rows = price_cache.read_bars(session, "AAPL", date(2026, 9, 1), date(2026, 9, 2))
    # Age the OLDER row well past the TTL; the newest one stays current.
    old = price_cache.read_bars(session, "AAPL", date(2026, 9, 2), date(2026, 9, 2))[0]
    aged = PriceBarRow(
        ticker="AAPL",
        bar_date="2026-08-01",
        open=200.0,
        high=200.0,
        low=200.0,
        close=200.0,
        volume=1.0,
        fetched_at_utc=datetime.now(UTC) - timedelta(days=30),
    )
    assert price_cache.is_fresh([aged, old]) is True
    assert rows


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.request = httpx.Request("GET", "https://api.polygon.io/x")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=self.request, response=self)

    def json(self) -> dict:
        return {}


def test_client_error_is_not_retried(monkeypatch) -> None:
    """An unknown ticker answers 404 however many times you ask. Retrying it
    used to burn five attempts, ~30s of sleep, and the whole per-minute budget."""
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        return _Resp(404)

    slept: list[float] = []
    monkeypatch.setattr("tradingagents_us.dataflows.polygon.time.sleep", slept.append)

    pc = PolygonClient(api_key="test")
    monkeypatch.setattr(pc._http, "get", fake_get)

    with pytest.raises(httpx.HTTPStatusError):
        pc._get("/v2/aggs/x", {})

    assert calls["n"] == 1, "a 4xx must cost exactly one request"
    assert slept == [], "and must not sleep before giving up"


def test_server_error_is_still_retried(monkeypatch) -> None:
    """The opposite guard: a 5xx is transient and must keep its backoff."""
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        return _Resp(503)

    monkeypatch.setattr("tradingagents_us.dataflows.polygon.time.sleep", lambda _s: None)

    pc = PolygonClient(api_key="test")
    monkeypatch.setattr(pc._http, "get", fake_get)

    with pytest.raises(RuntimeError):
        pc._get("/v2/aggs/x", {})

    assert calls["n"] == 5
