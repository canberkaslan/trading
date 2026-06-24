"""/v1/eval route — scorecard serialization with build_scorecard mocked."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scripts.eval_report import Scorecard


def _sc(**kw) -> Scorecard:
    base = dict(
        days=20,
        start_equity=100_000.0,
        end_equity=112_000.0,
        total_return=0.12,
        sharpe=1.6,
        sortino=1.9,
        max_dd=-0.04,
        dd_duration=3,
        calmar=1.3,
        var95=-0.01,
        cvar95=-0.02,
        positive_days_pct=62.0,
        spy_return=0.05,
    )
    base.update(kw)
    return Scorecard(**base)


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    import api.routes.eval as mod

    monkeypatch.setattr(mod, "_cache", {})
    from api.main import app

    return TestClient(app)


def test_go_verdict_serialized(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.routes.eval.build_scorecard", lambda period, benchmark: _sc())
    r = client.get("/v1/eval")
    assert r.status_code == 200
    b = r.json()
    assert b["verdict"] == "GO"
    assert b["sharpe"] == 1.6
    assert b["max_dd_pct"] == -4.0
    assert b["total_return_pct"] == 12.0
    assert b["spy_return_pct"] == 5.0
    assert b["gate_sharpe"] == 1.0


def test_no_go_surfaces_reasons(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.routes.eval.build_scorecard", lambda period, benchmark: _sc(sharpe=0.3))
    b = client.get("/v1/eval").json()
    assert b["verdict"] == "NO-GO"
    assert any("Sharpe" in r for r in b["reasons"])


def test_too_little_history_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(period, benchmark):
        raise SystemExit("not enough history")

    monkeypatch.setattr("api.routes.eval.build_scorecard", _boom)
    assert client.get("/v1/eval").status_code == 409
