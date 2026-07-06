"""/v1/portfolio/history — cleaned equity curve serialization.

The Alpaca client is replaced via FastAPI dependency_overrides with a fake
that returns a canned portfolio_history payload, so the test exercises the
equity-series cleaning + drawdown math without touching the broker.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient


class _FakeAlpaca:
    def __init__(self, history: dict) -> None:
        self._history = history
        self.closed = False

    def portfolio_history(self, period: str, timeframe: str) -> dict:
        return self._history

    def close(self) -> None:
        self.closed = True


def _ts(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def _make_client(history: dict, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("EVAL_START_DATE", raising=False)
    from api.deps import get_alpaca
    from api.main import app

    app.dependency_overrides[get_alpaca] = lambda: _FakeAlpaca(history)
    client = TestClient(app)
    client._history = history  # type: ignore[attr-defined]
    return client


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    from api.main import app

    app.dependency_overrides.clear()


def test_equity_curve_returns_and_drawdown(monkeypatch: pytest.MonkeyPatch) -> None:
    # 100k baseline, up to 110k, dip to 104.5k (-5% dd), recover to 108k.
    history = {
        "timestamp": [_ts(2026, 6, 1), _ts(2026, 6, 2), _ts(2026, 6, 3), _ts(2026, 6, 4)],
        "equity": [100_000.0, 110_000.0, 104_500.0, 108_000.0],
    }
    client = _make_client(history, monkeypatch)
    b = client.get("/v1/portfolio/history").json()

    assert b["days"] == 4
    assert b["start_equity"] == 100_000.0
    assert b["end_equity"] == 108_000.0
    assert b["total_return_pct"] == 8.0
    # peak 110k -> 104.5k is exactly -5%
    assert b["max_drawdown_pct"] == -5.0
    pts = b["points"]
    assert pts[0]["return_pct"] == 0.0
    assert pts[0]["drawdown_pct"] == 0.0
    assert pts[1]["return_pct"] == 10.0
    assert pts[2]["drawdown_pct"] == -5.0
    assert pts[3]["drawdown_pct"] == -1.82  # 108k vs peak 110k
    assert pts[3]["date"] == "2026-06-04"


def test_drawdown_from_running_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    history = {
        "timestamp": [_ts(2026, 6, 1), _ts(2026, 6, 2), _ts(2026, 6, 3)],
        "equity": [100_000.0, 110_000.0, 99_000.0],
    }
    client = _make_client(history, monkeypatch)
    pts = client.get("/v1/portfolio/history").json()["points"]
    # 99k vs peak 110k = -10%
    assert pts[2]["drawdown_pct"] == -10.0


def test_eval_start_date_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    history = {
        "timestamp": [_ts(2026, 5, 30), _ts(2026, 6, 1), _ts(2026, 6, 2)],
        "equity": [50_000.0, 100_000.0, 101_000.0],
    }
    client = _make_client(history, monkeypatch)
    # env is read at request time, so set it after the helper (which clears it)
    monkeypatch.setenv("EVAL_START_DATE", "2026-06-01")
    b = client.get("/v1/portfolio/history").json()
    assert b["days"] == 2
    assert b["start_equity"] == 100_000.0
    assert b["total_return_pct"] == 1.0


def test_empty_history(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _make_client({"timestamp": [], "equity": []}, monkeypatch)
    b = client.get("/v1/portfolio/history").json()
    assert b["days"] == 0
    assert b["points"] == []
    assert b["total_return_pct"] == 0.0


def test_alpaca_error_502(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)

    class _Boom:
        def portfolio_history(self, period: str, timeframe: str) -> dict:
            raise RuntimeError("alpaca down")

        def close(self) -> None:
            pass

    from api.deps import get_alpaca
    from api.main import app

    app.dependency_overrides[get_alpaca] = lambda: _Boom()
    r = TestClient(app).get("/v1/portfolio/history")
    assert r.status_code == 502
