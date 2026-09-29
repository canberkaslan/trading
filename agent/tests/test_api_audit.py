"""GET /v1/audit — reads back the trail the backend already writes, and no more."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.schemas import OrderUpdate, TradeOrder
from tradingagents_us.storage import TradeLogRepository


def _order(order_id: str, *, approved: bool = True, reasons: list[str] | None = None) -> TradeOrder:
    return TradeOrder(
        order_id=order_id, decision_id="dec-1", ticker="AAPL", market="US", side="BUY",
        quantity=7, order_type="MARKET", stop_loss=250.0, risk_approved=approved,
        rejection_reasons=reasons or [], submitted_at_utc=datetime.now(UTC) - timedelta(hours=2),
    )


@pytest.fixture()
def repo(tmp_path: Path) -> TradeLogRepository:
    from sqlalchemy import create_engine

    return TradeLogRepository(engine=create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True))


@pytest.fixture()
def client(repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", "1")
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)

    from api.deps import get_repo
    from api.main import app

    app.dependency_overrides[get_repo] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.pop(get_repo, None)


def test_empty_trail_is_empty_and_says_what_is_not_recorded(client: TestClient) -> None:
    body = client.get("/v1/audit").json()
    assert body["entries"] == []
    assert "approve" in body["not_recorded"]


def test_lists_kill_reject_cancel_and_refusal_newest_first(
    client: TestClient, repo: TradeLogRepository
) -> None:
    repo.save_order(_order("ord-rej"))
    repo.save_order(_order("ord-can"), broker_order_id="b-1")
    repo.save_order(_order("ord-risk", approved=False, reasons=["position_cap: 12%"]))
    now = datetime.now(UTC)
    repo.append_update(OrderUpdate(
        order_id="ord-rej", status="REJECTED", error_message="user_rejected",
        timestamp_utc=now - timedelta(minutes=30),
    ))
    repo.append_update(OrderUpdate(
        order_id="ord-can", status="CANCELLED", error_message="user_cancelled",
        timestamp_utc=now - timedelta(minutes=20),
    ))
    repo.append_kill_event(state="PAUSE_NEW", actor="operator-uid-123456", source="api")
    repo.append_kill_event(state="RUN", actor="system", source="daily_run", detail="pre-run check")

    entries = client.get("/v1/audit").json()["entries"]
    actions = [e["action"] for e in entries]
    assert sorted(actions) == ["cancel", "kill", "kill", "refuse", "reject"]
    stamps = [e["ts"] for e in entries]
    assert stamps == sorted(stamps, reverse=True)

    by_id = {e["id"]: e for e in entries}
    refuse = next(e for e in entries if e["action"] == "refuse")
    assert refuse["actor"] == "risk" and refuse["reasons"] == ["position_cap: 12%"]
    kills = [e for e in entries if e["action"] == "kill"]
    operator_kill = next(e for e in kills if e["actor"] == "operator")
    # The uid is shortened, never printed whole.
    assert "operator-uid-123456" not in operator_kill["where"]
    assert any(e["actor"] == "system" and e["where"] == "daily_run" for e in kills)
    assert len(by_id) == len(entries)


def test_a_plain_status_update_is_not_an_audit_row(
    client: TestClient, repo: TradeLogRepository
) -> None:
    """A submission / fill carries no actor, so it must not surface as an operator act."""
    repo.save_order(_order("ord-1"), broker_order_id="b-1")
    repo.append_update(OrderUpdate(
        order_id="ord-1", status="FILLED", timestamp_utc=datetime.now(UTC),
    ))
    assert client.get("/v1/audit").json()["entries"] == []


def test_audit_is_admin_only(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_ANONYMOUS_ADMIN", raising=False)
    assert client.get("/v1/audit").status_code == 403
