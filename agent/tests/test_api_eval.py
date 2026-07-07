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
    assert b["provisional_verdict"] is None  # past min-days, real verdict rules
    assert b["sharpe"] == 1.6
    assert b["max_dd_pct"] == -4.0
    assert b["total_return_pct"] == 12.0
    assert b["spy_return_pct"] == 5.0
    assert b["gate_sharpe"] == 1.0


def test_provisional_trend_below_min_days(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # still TOO EARLY, but current metrics project a GO trend
    monkeypatch.setattr(
        "api.routes.eval.build_scorecard", lambda period, benchmark: _sc(days=6)
    )
    b = client.get("/v1/eval").json()
    assert b["verdict"] == "TOO EARLY"
    assert b["provisional_verdict"] == "GO"


def test_no_go_surfaces_reasons(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("api.routes.eval.build_scorecard", lambda period, benchmark: _sc(sharpe=0.3))
    b = client.get("/v1/eval").json()
    assert b["verdict"] == "NO-GO"
    assert any("Sharpe" in r for r in b["reasons"])


def test_gates_and_countdown_serialized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("api.routes.eval.build_scorecard", lambda period, benchmark: _sc())
    b = client.get("/v1/eval").json()
    assert b["days_required"] == 10
    assert b["days_remaining"] == 0  # 20 days done
    gates = {g["name"]: g for g in b["gates"]}
    assert gates["Trading days"]["passed"] is True
    assert gates["Sharpe"]["passed"] is True
    assert gates["Max drawdown"]["passed"] is True
    assert gates["Beats SPY"]["passed"] is True  # 12% vs 5%


def test_gates_pending_below_min_days(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "api.routes.eval.build_scorecard", lambda period, benchmark: _sc(days=6)
    )
    b = client.get("/v1/eval").json()
    assert b["days_remaining"] == 4
    gates = {g["name"]: g for g in b["gates"]}
    assert gates["Trading days"]["passed"] is False
    assert gates["Sharpe"]["passed"] is None  # not evaluable yet
    assert gates["Trading days"]["detail"] == "6/10"


def test_too_little_history_409(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(period, benchmark):
        raise SystemExit("not enough history")

    monkeypatch.setattr("api.routes.eval.build_scorecard", _boom)
    assert client.get("/v1/eval").status_code == 409


def test_benchmark_on_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mobile calls /v1/eval with no query params — SPY benchmark must be
    requested by default so the GO/NO-GO scorecard shows the 'Beats SPY' gate."""
    seen: dict[str, bool] = {}

    def _spy(period, benchmark):
        seen["benchmark"] = benchmark
        return _sc()

    monkeypatch.setattr("api.routes.eval.build_scorecard", _spy)
    b = client.get("/v1/eval").json()
    assert seen["benchmark"] is True
    assert b["gates"][-1]["name"] == "Beats SPY"
    assert b["gates"][-1]["passed"] is True
