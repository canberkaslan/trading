"""An approval racing the other taps on the same account.

The approve handler runs in FastAPI's threadpool, so it can interleave with any
other request. These drive it with a real sqlite trade log and the real
executor against a broker stand-in, and hold it at the one point that matters:
past its locked kill-switch check, before the broker has the order.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from tradingagents_us.execution import executor
from tradingagents_us.execution.flatten import FlattenResult
from tradingagents_us.schemas import AgentDecision, AgentReasoning, OrderUpdate, TradeOrder
from tradingagents_us.storage import TradeLogRepository

#: How long the approval waits at the broker for a racing tap to land first.
_RACE_WINDOW_S = 1.0


class _Broker:
    """One paper account: client_order_id is unique, as Alpaca enforces it."""

    base_url = "https://paper.invalid/v2"

    def __init__(self) -> None:
        self.live: dict[str, str] = {}  # client_order_id -> broker order id
        self.events: list[str] = []
        self.before_submit: Callable[[], None] = lambda: None
        self.on_flatten: Callable[[], None] = lambda: None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._http = SimpleNamespace(get=lambda url: SimpleNamespace(json=self._open_orders))

    def _open_orders(self) -> list[dict[str, Any]]:
        return [
            {"symbol": "AAPL", "side": "buy", "qty": "5", "filled_qty": "0",
             "limit_price": None, "submitted_at": "2026-10-06T22:31:00Z"}
            for _ in self.live
        ]

    def account(self) -> SimpleNamespace:
        return SimpleNamespace(
            cash=100_000.0, portfolio_value=100_000.0, last_equity=100_000.0,
            trading_blocked=False, pattern_day_trader=False,
        )

    def list_positions(self) -> list:
        return []

    def get_order_by_client_order_id(self, coid: str) -> SimpleNamespace | None:
        oid = self.live.get(coid)
        return None if oid is None else SimpleNamespace(id=oid, status="accepted", filled_qty=0.0)

    def submit_order(self, *, symbol: str, qty: int, side: str, client_order_id: str,
                     time_in_force: str, **_: Any) -> SimpleNamespace:
        self.before_submit()
        with self._lock:
            if client_order_id in self.live:
                raise RuntimeError("client_order_id must be unique")
            oid = f"b-{next(self._ids)}"
            self.live[client_order_id] = oid
        self.events.append(f"broker accepts {side.upper()} {symbol} x{qty} tif={time_in_force}")
        return SimpleNamespace(id=oid, status="accepted", filled_qty=0.0, filled_avg_price=None)

    def cancel_all_orders(self) -> None:
        self.live.clear()

    def close(self) -> None:
        pass


def _decision() -> AgentDecision:
    return AgentDecision(
        ticker="AAPL", market="US", quote_currency="USD", rating="Buy",
        entry_price=100.0, stop_loss=90.0, price_target=130.0,
        reasoning=[AgentReasoning(agent="pm", model="m", summary="x",
                                  tokens_in=0, tokens_out=0, latency_ms=0)],
        timestamp_utc=datetime.now(UTC), decision_id="dec-1",
    )


def _held_order() -> TradeOrder:
    return TradeOrder(
        order_id="ord-1", decision_id="dec-1", ticker="AAPL", market="US", side="BUY",
        quantity=5, order_type="MARKET", stop_loss=90.0, risk_approved=True,
        rejection_reasons=[], submitted_at_utc=datetime.now(UTC),
    )


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", "1")
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.setenv("KILL_SWITCH_PATH", str(tmp_path / "kill.state"))

    from api.deps import get_repo
    from api.main import app
    from api.routes import orders

    repo = TradeLogRepository(
        engine=create_engine(f"sqlite:///{tmp_path / 'trades.db'}", future=True)
    )
    repo.save_decision(_decision())
    repo.save_order(_held_order(), broker_order_id=None)
    repo.append_update(OrderUpdate(
        order_id="ord-1", status="PENDING", error_message="awaiting_mobile_approval",
        timestamp_utc=datetime.now(UTC),
    ))

    broker = _Broker()

    def fake_flatten(client: Any = None) -> FlattenResult:
        broker.events.append("FLATTEN_ALL")
        broker.cancel_all_orders()
        broker.on_flatten()
        return FlattenResult(ok=True, noop=True, summary="book already flat; open orders cancelled")

    monkeypatch.setattr(orders, "APPROVE_LOCK_TIMEOUT_S", 10.0)
    monkeypatch.setattr(orders, "_previous_close", lambda ticker: 100.0)
    monkeypatch.setattr(orders, "_account_client", lambda: broker)
    monkeypatch.setattr(orders, "flatten_all", fake_flatten)
    monkeypatch.setattr(executor, "AlpacaClient", lambda: broker)
    app.dependency_overrides[get_repo] = lambda: repo
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, repo=repo, broker=broker)
    app.dependency_overrides.pop(get_repo, None)


def _approve_held_at_the_broker(
    env: SimpleNamespace, release: threading.Event
) -> tuple[threading.Thread, dict[str, int]]:
    """Start an approval and return once it is past its checks, at the broker."""
    at_broker = threading.Event()

    def held() -> None:
        at_broker.set()
        release.wait(_RACE_WINDOW_S)

    env.broker.before_submit = held
    results: dict[str, int] = {}
    t = threading.Thread(target=lambda: results.__setitem__(
        "approve", env.client.post("/v1/orders/ord-1/approve").status_code
    ))
    t.start()
    assert at_broker.wait(10), "the approval never reached the broker"
    return t, results


class TestKillSwitchTappedMidApprove:
    def test_a_flatten_never_leaves_the_approved_buy_behind(self, env) -> None:
        release = threading.Event()
        env.broker.on_flatten = release.set
        t, results = _approve_held_at_the_broker(env, release)
        r = env.client.post("/v1/orders/kill-switch", json={"state": "FLATTEN_ALL"})
        t.join(20)
        assert r.status_code == 200
        assert results == {"approve": 200}
        # The BUY is in before the flatten runs, so the flatten cancels it.
        assert env.broker.events == ["broker accepts BUY AAPL x5 tif=gtc", "FLATTEN_ALL"]
        assert env.broker.live == {}

    def test_a_pause_answers_only_once_no_new_buy_can_follow(self, env) -> None:
        release = threading.Event()
        t, results = _approve_held_at_the_broker(env, release)
        r = env.client.post("/v1/orders/kill-switch", json={"state": "PAUSE_NEW"})
        sent_before_the_answer = list(env.broker.events)
        release.set()
        t.join(20)
        assert r.status_code == 200
        assert results == {"approve": 200}
        # "paused" means no BUY goes out after it: the one already past its
        # check is in before the tap is answered.
        assert sent_before_the_answer == ["broker accepts BUY AAPL x5 tif=gtc"]
