"""On-demand /v1/analyze route — job lifecycle without running the real pipeline."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.schemas import AgentDecision, AgentReasoning


def _fake_decision(ticker: str) -> AgentDecision:
    return AgentDecision(
        ticker=ticker,
        market="US",
        quote_currency="USD",
        rating="Overweight",
        entry_price=100.0,
        stop_loss=95.0,
        reasoning=[
            AgentReasoning(
                agent="portfolio_manager",
                model="claude-opus-4-7",
                summary="test",
                tokens_in=1,
                tokens_out=1,
                latency_ms=1,
            )
        ],
        timestamp_utc=datetime.now(timezone.utc),
        decision_id="test-123",
    )


class _NoOpRepo:
    def save_decision(self, decision: object) -> None:  # noqa: D401
        pass


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    # Don't touch the real DB or LLM pipeline. _run imports get_repo lazily,
    # so patching the name on api.deps is picked up at call time.
    monkeypatch.setattr("api.routes.analyze.propagate", lambda t, d: _fake_decision(t))
    monkeypatch.setattr("api.deps.get_repo", lambda: _NoOpRepo())
    import api.routes.analyze as mod

    monkeypatch.setattr(mod, "_jobs", {})
    from api.main import app

    return TestClient(app)


def _wait_done(client: TestClient, job_id: str, timeout_s: float = 5.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/v1/analyze/{job_id}")
        body = r.json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s")


def test_analyze_runs_and_returns_decision(client: TestClient) -> None:
    r = client.post("/v1/analyze", json={"ticker": "aapl"})
    assert r.status_code == 202
    job = r.json()
    assert job["ticker"] == "AAPL"  # normalized upper
    # status may already be done with an instant mock — any valid state is fine
    assert job["status"] in ("queued", "running", "done")

    done = _wait_done(client, job["job_id"])
    assert done["status"] == "done"
    assert done["decision"]["ticker"] == "AAPL"
    assert done["decision"]["rating"] == "Overweight"


def test_analyze_rejects_non_alpha_ticker(client: TestClient) -> None:
    r = client.post("/v1/analyze", json={"ticker": "A1!"})
    assert r.status_code == 422


def test_analyze_dedupes_inflight_same_ticker(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Make the pipeline slow so both POSTs land while the first is in-flight.
    def _slow(t: str, d: str) -> AgentDecision:
        time.sleep(0.3)
        return _fake_decision(t)

    monkeypatch.setattr("api.routes.analyze.propagate", _slow)
    a = client.post("/v1/analyze", json={"ticker": "MSFT"}).json()
    b = client.post("/v1/analyze", json={"ticker": "MSFT"}).json()
    assert a["job_id"] == b["job_id"]  # reused, not a second $1 run
    _wait_done(client, a["job_id"])


def test_get_unknown_job_404(client: TestClient) -> None:
    assert client.get("/v1/analyze/nope").status_code == 404


def test_pipeline_error_surfaces_as_error_status(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(t: str, d: str) -> AgentDecision:
        raise RuntimeError("polygon down")

    monkeypatch.setattr("api.routes.analyze.propagate", _boom)
    job = client.post("/v1/analyze", json={"ticker": "NVDA"}).json()
    done = _wait_done(client, job["job_id"])
    assert done["status"] == "error"
    assert "polygon down" in done["error"]
