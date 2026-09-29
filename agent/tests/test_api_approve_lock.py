"""A mobile approval takes the same submit lock as the daily run.

A BUY approved while a daily-run ticker is sizing would otherwise spend cash
that ticker has just counted as available. A SELL is never held up by it. The
wait must not stall the API (the kill switch lives there), and what may have
changed while it waited — the switch, the cash — is checked again under it.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.execution import submit_lock
from tradingagents_us.file_lock import acquire, exclusive, release


def _row(side: str, qty: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        order_id="ord-1", decision_id="dec-1", ticker="AAPL", market="US", side=side,
        quantity=qty, order_type="MARKET", limit_price=None, stop_loss=140.0,
        risk_approved=True, rejection_reasons_json=[], broker_order_id=None,
        submitted_at_utc=datetime.now(UTC),
    )


class _Broker:
    """Alpaca stand-in: settled cash and a list of open orders."""

    base_url = "https://paper.invalid/v2"

    def __init__(self, cash: float, open_orders: list[dict] | None = None) -> None:
        self.cash = cash
        orders = open_orders or []
        self._http = SimpleNamespace(get=lambda url: SimpleNamespace(json=lambda: orders))

    def account(self) -> SimpleNamespace:
        return SimpleNamespace(cash=self.cash)

    def close(self) -> None:
        pass


def _lock_held() -> bool:
    try:
        release(acquire(submit_lock.lock_path(), 0.0))
    except Exception:  # noqa: BLE001 — refused: someone holds it
        return True
    return False


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", "1")
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.setenv("KILL_SWITCH_PATH", str(tmp_path / "kill.state"))

    from api.deps import get_repo
    from api.main import app
    from api.routes import orders

    monkeypatch.setattr(orders, "APPROVE_LOCK_TIMEOUT_S", 0.3)
    state = SimpleNamespace(
        side="BUY", qty=5, broker=_Broker(cash=10_000.0), submitted=[],
        price_calls_under_lock=[],
    )

    def fake_submit(order, **kw):
        state.submitted.append(order.side)
        return SimpleNamespace(
            broker_order_id="b-1", submitted=True, refusal_reasons=[],
            update=SimpleNamespace(status="ACCEPTED", error_message=None),
        )

    def fake_close(ticker: str) -> float:
        state.price_calls_under_lock.append(_lock_held())
        return 100.0

    repo = MagicMock()
    repo.session.return_value.__enter__.return_value.get.side_effect = (
        lambda _model, key: _row(state.side, state.qty) if key == "ord-1" else object()
    )
    app.dependency_overrides[get_repo] = lambda: repo
    monkeypatch.setattr(orders, "row_to_decision", lambda _row: MagicMock(entry_price=100.0))
    monkeypatch.setattr(orders, "submit_order", fake_submit)
    monkeypatch.setattr(orders, "_previous_close", fake_close)
    monkeypatch.setattr(orders, "_account_client", lambda: state.broker)
    with TestClient(app) as client:
        state.client = client
        state.approve = lambda: client.post("/v1/orders/ord-1/approve")
        yield state
    app.dependency_overrides.pop(get_repo, None)


def test_a_buy_goes_out_when_the_lock_is_free_and_cash_covers_it(api) -> None:
    assert api.approve().status_code == 200
    assert api.submitted == ["BUY"]


def test_a_buy_during_a_sizing_is_refused_and_stays_pending(api) -> None:
    with exclusive(submit_lock.lock_path()):
        r = api.approve()
    assert r.status_code == 503
    assert api.submitted == []


def test_a_sell_during_a_sizing_still_goes_out(api) -> None:
    api.side = "SELL"
    with exclusive(submit_lock.lock_path()):
        r = api.approve()
    assert r.status_code == 200
    assert api.submitted == ["SELL"]


def test_a_buy_the_cash_no_longer_covers_is_refused(api) -> None:
    # $10k settled, but tonight's run left a $9.8k BUY pending: $200 remains.
    api.broker = _Broker(cash=10_000.0, open_orders=[
        {"symbol": "MSFT", "side": "buy", "qty": "98", "filled_qty": "0",
         "limit_price": None, "submitted_at": "2026-09-29T22:31:00Z"},
    ])
    r = api.approve()  # 5 x $100 = $500
    assert r.status_code == 409
    assert "spendable now" in r.json()["detail"]
    assert api.submitted == []


def test_no_market_data_call_is_made_under_the_lock(api) -> None:
    api.broker = _Broker(cash=10_000.0, open_orders=[
        {"symbol": "MSFT", "side": "buy", "qty": "1", "filled_qty": "0",
         "limit_price": None, "submitted_at": "2026-09-29T22:31:00Z"},
    ])
    assert api.approve().status_code == 200
    assert api.price_calls_under_lock == [False, False]  # AAPL, then MSFT


def test_a_kill_switch_flipped_during_the_wait_blocks_the_buy(
    api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api.routes import orders

    monkeypatch.setattr(orders, "APPROVE_LOCK_TIMEOUT_S", 5.0)
    results: list[int] = []
    with exclusive(submit_lock.lock_path()):
        t = threading.Thread(target=lambda: results.append(api.approve().status_code))
        t.start()
        time.sleep(0.3)  # the request is now waiting for the lock
        (tmp_path / "kill.state").write_text("PAUSE_NEW")
    t.join(10)
    assert results == [409]
    assert api.submitted == []


def test_other_requests_are_served_while_an_approval_waits(
    api, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The handler waits in the threadpool, not on the event loop, so the kill
    # switch (and everything else) keeps answering.
    from api.routes import orders

    monkeypatch.setattr(orders, "APPROVE_LOCK_TIMEOUT_S", 3.0)
    done = threading.Event()
    with exclusive(submit_lock.lock_path()):
        t = threading.Thread(target=lambda: (api.approve(), done.set()))
        t.start()
        time.sleep(0.3)
        started = time.monotonic()
        r = api.client.get("/v1/orders/kill-switch")
        elapsed = time.monotonic() - started
        assert r.status_code == 200
        assert elapsed < 1.5, f"kill switch took {elapsed:.1f}s behind a waiting approval"
        assert not done.is_set(), "the approval should still be waiting"
    t.join(10)
