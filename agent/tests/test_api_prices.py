"""/v1/prices route — bars proxying + cache, with Polygon mocked."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient


@dataclass
class _Agg:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    transactions: int | None = None


class _FakePolygon:
    calls = 0

    def __enter__(self) -> "_FakePolygon":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def aggregates(self, ticker, from_date, to_date, timespan="day", **kw):  # noqa: ANN001
        type(self).calls += 1
        base = 1_700_000_000_000
        return [
            _Agg(base + i * 86_400_000, 100 + i, 102 + i, 99 + i, 101 + i, 1e6)
            for i in range(5)
        ]


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.setattr("api.routes.prices.PolygonClient", _FakePolygon)
    _FakePolygon.calls = 0
    import api.routes.prices as mod

    monkeypatch.setattr(mod, "_cache", {})
    from api.main import app

    return TestClient(app)


def test_returns_bars_and_change(client: TestClient) -> None:
    r = client.get("/v1/prices/AAPL?days=5")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "AAPL"
    assert len(body["bars"]) == 5
    assert body["first"] == 101.0  # close of first bar
    assert body["last"] == 105.0  # close of last bar
    assert round(body["change_pct"], 2) == round((105 / 101 - 1) * 100, 2)
    assert body["bars"][0]["t"].count("-") == 2  # ISO date


def test_caches_within_ttl(client: TestClient) -> None:
    client.get("/v1/prices/MSFT?days=5")
    client.get("/v1/prices/MSFT?days=5")
    assert _FakePolygon.calls == 1  # second served from cache


def test_rejects_bad_ticker(client: TestClient) -> None:
    assert client.get("/v1/prices/A1B2").status_code == 422


def test_normalizes_lowercase(client: TestClient) -> None:
    assert client.get("/v1/prices/nvda?days=5").json()["ticker"] == "NVDA"
