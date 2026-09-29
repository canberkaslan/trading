"""A mobile approval takes the same submit lock as the daily run.

A BUY approved while a daily-run ticker is sizing would otherwise spend cash
that ticker has just counted as available. A SELL is never held up by it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.execution import submit_lock
from tradingagents_us.file_lock import exclusive


def _row(side: str) -> SimpleNamespace:
    return SimpleNamespace(
        order_id="ord-1", decision_id="dec-1", ticker="AAPL", market="US", side=side,
        quantity=5, order_type="MARKET", limit_price=None, stop_loss=140.0,
        risk_approved=True, rejection_reasons_json=[], broker_order_id=None,
        submitted_at_utc=datetime.now(UTC),
    )


@pytest.fixture()
def approve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", "1")
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.setenv("KILL_SWITCH_PATH", str(tmp_path / "kill.state"))

    from api.deps import get_repo
    from api.main import app
    from api.routes import orders

    monkeypatch.setattr(orders, "APPROVE_LOCK_TIMEOUT_S", 0.2)
    submitted: list[str] = []

    def fake_submit(order, **kw):
        submitted.append(order.side)
        return SimpleNamespace(
            broker_order_id="b-1", submitted=True, refusal_reasons=[],
            update=SimpleNamespace(status="ACCEPTED", error_message=None),
        )

    def run(side: str):
        repo = MagicMock()
        rows = {"ord-1": _row(side), "dec-1": object()}
        repo.session.return_value.__enter__.return_value.get.side_effect = (
            lambda _model, key: rows[key]
        )
        app.dependency_overrides[get_repo] = lambda: repo
        try:
            with patch.object(orders, "row_to_decision", return_value=MagicMock()), \
                 patch.object(orders, "submit_order", side_effect=fake_submit), \
                 patch("tradingagents_us.dataflows.polygon.PolygonClient",
                       side_effect=RuntimeError("offline")):
                return TestClient(app).post("/v1/orders/ord-1/approve")
        finally:
            app.dependency_overrides.pop(get_repo, None)

    return run, submitted


def test_a_buy_waits_for_nobody_when_the_lock_is_free(approve) -> None:
    run, submitted = approve
    assert run("BUY").status_code == 200
    assert submitted == ["BUY"]


def test_a_buy_during_a_sizing_is_refused_and_stays_pending(approve) -> None:
    run, submitted = approve
    with exclusive(submit_lock.lock_path()):
        r = run("BUY")
    assert r.status_code == 503
    assert submitted == []


def test_a_sell_during_a_sizing_still_goes_out(approve) -> None:
    run, submitted = approve
    with exclusive(submit_lock.lock_path()):
        r = run("SELL")
    assert r.status_code == 200
    assert submitted == ["SELL"]
