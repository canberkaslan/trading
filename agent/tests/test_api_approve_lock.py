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
    """Alpaca stand-in: settled cash, equity, positions and open orders."""

    base_url = "https://paper.invalid/v2"

    def __init__(
        self, cash: float, open_orders: list[dict] | None = None, *,
        equity: float | None = None, positions: list[SimpleNamespace] | None = None,
    ) -> None:
        self.cash = cash
        self.equity = cash if equity is None else equity
        self.positions = positions or []
        orders = open_orders or []
        self._http = SimpleNamespace(get=lambda url: SimpleNamespace(json=lambda: orders))

    def account(self) -> SimpleNamespace:
        return SimpleNamespace(cash=self.cash, portfolio_value=self.equity)

    def list_positions(self) -> list[SimpleNamespace]:
        return list(self.positions)

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
    session = repo.session.return_value.__enter__.return_value
    session.get.side_effect = (
        lambda _model, key: _row(state.side, state.qty) if key == "ord-1" else object()
    )
    session.execute.return_value.scalar_one_or_none.return_value = None  # never rejected
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


def test_a_buy_the_name_cap_no_longer_has_room_for_is_refused(api) -> None:
    # Tonight's run filled AAPL to its 10% cap: an open 100-share BUY at about
    # $100 on $100k. The held 60-share AAPL BUY was sized before it, and the
    # run could not see it.
    api.broker = _Broker(cash=100_000.0, open_orders=[
        {"symbol": "AAPL", "side": "buy", "qty": "100", "filled_qty": "0",
         "limit_price": None, "submitted_at": "2026-10-06T22:31:00Z"},
    ])
    api.qty = 60
    r = api.approve()
    assert r.status_code == 409
    assert "position_pct" in r.json()["detail"]
    assert api.submitted == []


def test_a_buy_its_sector_no_longer_has_room_for_is_refused(api) -> None:
    # MSFT and NVDA hold 28% of $100k in Information Technology; another 5% of
    # AAPL would take the sector past its 30% cap.
    api.broker = _Broker(cash=72_000.0, equity=100_000.0, positions=[
        SimpleNamespace(symbol="MSFT", market_value=14_000.0),
        SimpleNamespace(symbol="NVDA", market_value=14_000.0),
    ])
    api.qty = 50
    r = api.approve()
    assert r.status_code == 409
    assert "sector_pct" in r.json()["detail"]
    assert api.submitted == []


def test_a_name_that_filled_during_the_wait_is_not_counted_as_unknown(api) -> None:
    # MSFT holds 20% of $100k in Information Technology. While this tap waited
    # on the lock, another tap's NVDA BUY filled: $9k more of the sector that
    # the pre-lock read never saw. Bucketed as "Unknown", it would let 90 AAPL
    # take the sector to 38% against its 30% cap.
    msft = SimpleNamespace(symbol="MSFT", market_value=20_000.0)
    nvda = SimpleNamespace(symbol="NVDA", market_value=9_000.0)
    api.broker = _Broker(cash=71_000.0, equity=100_000.0)
    api.broker.list_positions = lambda: [msft, nvda] if _lock_held() else [msft]
    api.qty = 90
    r = api.approve()
    assert r.status_code == 409
    assert "NVDA" in r.json()["detail"]
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


class _FillingBroker:
    """$50k settled and a 100-share MSFT market BUY open, which may fill at the
    open between the cash re-check's two broker reads: the lock covers our
    order paths, not the broker's fills."""

    base_url = "https://paper.invalid/v2"

    def __init__(self, *, fills_mid_read: bool) -> None:
        self.cash = 50_000.0
        self.open_orders = [
            {"symbol": "MSFT", "side": "buy", "qty": "100", "filled_qty": "0",
             "limit_price": None, "submitted_at": "2026-10-07T13:30:00Z"},
        ]
        self.fills_mid_read = fills_mid_read
        self.reads = 0
        self._http = SimpleNamespace(get=lambda url: SimpleNamespace(json=self._orders))

    def _read(self) -> None:
        self.reads += 1
        if self.reads == 1 and self.fills_mid_read:
            self.cash -= 100 * 400.0
            self.open_orders = []

    def account(self) -> SimpleNamespace:
        acct = SimpleNamespace(cash=self.cash, portfolio_value=100_000.0)
        self._read()
        return acct

    def _orders(self) -> list[dict]:
        page = list(self.open_orders)
        self._read()
        return page


@pytest.mark.parametrize("fills_mid_read", [False, True])
def test_a_pending_buy_that_fills_mid_re_check_still_counts(fills_mid_read: bool) -> None:
    # The MSFT BUY holds $40k of the $50k, so a $30k AAPL BUY is refused,
    # whether or not the MSFT order fills while the cash is re-checked.
    from fastapi import HTTPException

    from api.routes import orders

    broker = _FillingBroker(fills_mid_read=fills_mid_read)
    held = SimpleNamespace(ticker="AAPL", side="BUY", quantity=100)
    with pytest.raises(HTTPException) as refused:
        orders._refuse_if_unaffordable(
            broker, held, 300.0, {"AAPL": 300.0, "MSFT": 400.0},  # type: ignore[arg-type]
        )
    assert refused.value.status_code == 409
    assert "spendable now" in refused.value.detail
