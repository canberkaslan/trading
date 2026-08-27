"""POST /v1/orders/{id}/cancel — honesty of the cancel path.

Cancel is the only user-initiated call that touches a live broker order, so
every branch here is about *not lying to the money screen*: never report a
cancellation that did not happen, never leave the local DB claiming an order is
still live after a successful cancel, and never swallow a broker refusal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.schemas import TradeOrder
from tradingagents_us.storage import TradeLogRepository


def _order(order_id: str = "ord-1") -> TradeOrder:
    return TradeOrder(
        order_id=order_id,
        decision_id="dec-1",
        ticker="AAPL",
        market="US",
        side="BUY",
        quantity=10,
        order_type="MARKET",
        stop_loss=250.0,
        risk_approved=True,
        rejection_reasons=[],
        submitted_at_utc=datetime.now(UTC),
    )


@pytest.fixture()
def repo(tmp_path: Path) -> TradeLogRepository:
    from sqlalchemy import create_engine

    return TradeLogRepository(engine=create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True))


@pytest.fixture()
def alpaca() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def client(
    repo: TradeLogRepository, alpaca: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)

    from api.deps import get_alpaca, get_repo
    from api.main import app

    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_alpaca] = lambda: alpaca
    yield TestClient(app)
    app.dependency_overrides.pop(get_repo, None)
    app.dependency_overrides.pop(get_alpaca, None)


def _updates(repo: TradeLogRepository, order_id: str) -> list:
    from sqlalchemy import select

    from tradingagents_us.storage.models import OrderUpdateRow

    with repo.session() as s:
        return list(
            s.execute(
                select(OrderUpdateRow).where(OrderUpdateRow.order_id == order_id)
            ).scalars().all()
        )


class TestCancelAtBroker:
    def test_cancels_and_persists_update(
        self, client: TestClient, repo: TradeLogRepository, alpaca: MagicMock
    ) -> None:
        repo.save_order(_order(), broker_order_id="bkr-123")

        r = client.post("/v1/orders/ord-1/cancel")

        assert r.status_code == 200
        assert r.json() == {
            "order_id": "ord-1",
            "broker_order_id": "bkr-123",
            "status": "CANCELLED",
        }
        alpaca.cancel_order.assert_called_once_with("bkr-123")
        alpaca.close.assert_called_once()

        rows = _updates(repo, "ord-1")
        assert [(u.status, u.error_message) for u in rows] == [("CANCELLED", "user_cancelled")]

    def test_broker_refusal_is_502_and_writes_no_update(
        self, client: TestClient, repo: TradeLogRepository, alpaca: MagicMock
    ) -> None:
        # Alpaca 422 "order is not cancelable" = it already filled. The order is
        # live; recording CANCELLED here would hide a real position.
        repo.save_order(_order(), broker_order_id="bkr-123")
        alpaca.cancel_order.side_effect = RuntimeError(
            "alpaca DELETE /orders/bkr-123 failed 422: order is not cancelable"
        )

        r = client.post("/v1/orders/ord-1/cancel")

        assert r.status_code == 502
        assert "not cancelable" in r.json()["detail"]
        assert _updates(repo, "ord-1") == []
        alpaca.close.assert_called_once()


class TestCancelGuards:
    def test_unknown_order_is_404(self, client: TestClient, alpaca: MagicMock) -> None:
        r = client.post("/v1/orders/nope/cancel")
        assert r.status_code == 404
        alpaca.cancel_order.assert_not_called()

    def test_order_not_at_broker_is_409(
        self, client: TestClient, repo: TradeLogRepository, alpaca: MagicMock
    ) -> None:
        # Held-for-approval order: nothing to cancel at the broker. The old
        # code answered 200 "cancelled" and left it sitting in /pending.
        repo.save_order(_order(), broker_order_id=None)

        r = client.post("/v1/orders/ord-1/cancel")

        assert r.status_code == 409
        assert "reject" in r.json()["detail"]
        alpaca.cancel_order.assert_not_called()
        assert _updates(repo, "ord-1") == []
