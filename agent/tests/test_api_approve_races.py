"""An approval racing the other taps on the same account.

The approve handler runs in FastAPI's threadpool, so it can interleave with any
other request. These drive it with a real sqlite trade log and the real
executor against a broker stand-in, and hold it at the one point that matters:
past its locked kill-switch check, before the broker has the order.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from tradingagents_us.execution import executor, submit_lock
from tradingagents_us.execution.flatten import FlattenResult
from tradingagents_us.file_lock import exclusive
from tradingagents_us.notifications.ops_channel import ChannelResult, Delivery
from tradingagents_us.schemas import AgentDecision, AgentReasoning, OrderUpdate, TradeOrder
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.models import KillSwitchEventRow, OrderUpdateRow, TradeOrderRow

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
        self.on_account: Callable[[], None] = lambda: None
        #: Round trip of a lookup that finds the order already live.
        self.duplicate_lookup_s = 0.0
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
        self.on_account()
        return SimpleNamespace(
            cash=100_000.0, portfolio_value=100_000.0, last_equity=100_000.0,
            trading_blocked=False, pattern_day_trader=False,
        )

    def list_positions(self) -> list:
        return []

    def get_order_by_client_order_id(self, coid: str) -> SimpleNamespace | None:
        oid = self.live.get(coid)
        if oid is not None:
            time.sleep(self.duplicate_lookup_s)
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


class _Pager:
    """Stands in for the ops alert channels (push + GitHub issue)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self._paged = threading.Event()

    def __call__(self, title: str, body: str, kind: str = "ops") -> Delivery:
        self.sent.append((title, body, kind))
        self._paged.set()
        return Delivery((ChannelResult("push", True, "sent to 1 device(s)"),))

    def wait(self, timeout_s: float) -> bool:
        return self._paged.wait(timeout_s)


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
    pages = _Pager()

    def fake_flatten(client: Any = None) -> FlattenResult:
        broker.events.append("FLATTEN_ALL")
        broker.cancel_all_orders()
        broker.on_flatten()
        return FlattenResult(ok=True, noop=True, summary="book already flat; open orders cancelled")

    monkeypatch.setattr(orders, "_previous_close", lambda ticker: 100.0)
    monkeypatch.setattr(orders, "_account_client", lambda: broker)
    monkeypatch.setattr(orders, "flatten_all", fake_flatten)
    monkeypatch.setattr(executor, "AlpacaClient", lambda: broker)
    monkeypatch.setattr(orders, "send_ops_alert", pages)
    app.dependency_overrides[get_repo] = lambda: repo
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, repo=repo, broker=broker, pages=pages)
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


class TestFlattenThatCouldNotWait:
    def test_a_buy_past_its_check_is_not_posted_after_the_flatten(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The approval is past its locked kill-switch check, then the broker is
        # slow on the reads before the POST. The flatten stops waiting for the
        # lock and runs first; the BUY must not go out behind it as a live gtc
        # bracket that outlives FLATTEN_ALL.
        monkeypatch.setattr(submit_lock, "EXIT_TIMEOUT_S", 0.3)
        past_check, flattened = threading.Event(), threading.Event()
        env.broker.on_account = lambda: (past_check.set(), flattened.wait(5))
        env.broker.on_flatten = flattened.set
        results: dict[str, int] = {}
        t = threading.Thread(target=lambda: results.__setitem__(
            "approve", env.client.post("/v1/orders/ord-1/approve").status_code
        ))
        t.start()
        assert past_check.wait(10), "the approval never got past its locked check"
        r = env.client.post("/v1/orders/kill-switch", json={"state": "FLATTEN_ALL"})
        t.join(20)
        assert r.status_code == 200
        assert env.broker.events == ["FLATTEN_ALL"]
        assert env.broker.live == {}
        assert results == {"approve": 422}


class TestRejectWhileAnApprovalIsInFlight:
    def test_a_reject_answered_during_the_wait_is_not_sent_after_it(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The approval waits on a run ticker's lock; the operator taps Reject
        # and is told "REJECTED". The approval must not send the order once it
        # gets the lock.
        from api.routes import orders

        monkeypatch.setattr(orders, "REJECT_LOCK_TIMEOUT_S", 0.3, raising=False)
        results: dict[str, int] = {}
        with exclusive(submit_lock.lock_path()):
            t = threading.Thread(target=lambda: results.__setitem__(
                "approve", env.client.post("/v1/orders/ord-1/approve").status_code
            ))
            t.start()
            time.sleep(0.3)  # the approval now waits on the lock
            results["reject"] = env.client.post("/v1/orders/ord-1/reject").status_code
        t.join(20)
        assert env.broker.events == []
        assert results["approve"] == 409
        assert _statuses(env.repo) == ["PENDING", "REJECTED"]

    def test_a_reject_waits_for_the_approval_already_past_its_checks(self, env) -> None:
        release = threading.Event()
        t, results = _approve_held_at_the_broker(env, release)
        r = env.client.post("/v1/orders/ord-1/reject")
        t.join(20)
        assert results == {"approve": 200}
        # Not "REJECTED" for an order that is live at the broker: /cancel.
        assert r.status_code == 409
        assert "/cancel" in r.json()["detail"]
        assert _statuses(env.repo) == ["PENDING", "ACCEPTED"]


#: The app gives up on a request after this long (ky `timeout`,
#: mobile/app/src/api/client.ts), and builds already installed keep it.
_APP_TIMEOUT_S = 10.0


class TestAnswersBeforeTheAppGivesUp:
    """The submit lock can stay held for minutes (a time exit's close)."""

    def test_a_pause_is_answered_while_the_lock_stays_held(self, env, tmp_path) -> None:
        with exclusive(submit_lock.lock_path()):
            started = time.monotonic()
            r = env.client.post("/v1/orders/kill-switch", json={"state": "PAUSE_NEW"})
            elapsed = time.monotonic() - started
        assert elapsed < _APP_TIMEOUT_S, f"answered after {elapsed:.1f}s"
        assert r.status_code == 202
        assert (tmp_path / "kill.state").read_text() == "PAUSE_NEW"

    def test_an_approval_is_answered_while_the_lock_stays_held(self, env) -> None:
        with exclusive(submit_lock.lock_path()):
            started = time.monotonic()
            r = env.client.post("/v1/orders/ord-1/approve")
            elapsed = time.monotonic() - started
        assert elapsed < _APP_TIMEOUT_S, f"answered after {elapsed:.1f}s"
        assert r.status_code == 503
        assert env.broker.events == []

    def test_a_flatten_behind_a_long_hold_answers_then_runs_after_it(
        self, env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from api.routes import orders

        monkeypatch.setattr(orders, "KILL_SWITCH_ANSWER_S", 0.3)
        flattened = threading.Event()
        env.broker.on_flatten = flattened.set
        with exclusive(submit_lock.lock_path()):
            r = env.client.post("/v1/orders/kill-switch", json={"state": "FLATTEN_ALL"})
            assert r.status_code == 202
            assert "audit" in r.json()["pending"]
            # Not beside the order path that holds the lock: after it.
            assert not flattened.wait(0.3)
        assert flattened.wait(10), "the flatten never ran once the lock was let go"
        assert env.broker.events == ["FLATTEN_ALL"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _flatten_recorded(env.repo):
            time.sleep(0.05)
        assert _flatten_recorded(env.repo), "the late flatten left no audit row"


def _flatten_recorded(repo: TradeLogRepository) -> bool:
    with repo.session() as s:
        details = s.execute(select(KillSwitchEventRow.detail)).scalars().all()
    return any(d and d.startswith("noop: ") for d in details)


def _statuses(repo: TradeLogRepository) -> list[str]:
    with repo.session() as s:
        rows = s.execute(
            select(OrderUpdateRow).where(OrderUpdateRow.order_id == "ord-1")
            .order_by(OrderUpdateRow.id)
        ).scalars().all()
        return [r.status for r in rows]


class TestDoubleTapApprove:
    def test_the_second_tap_never_unrecords_the_live_buy(self, env) -> None:
        # A run ticker holds the lock, so both taps pass the unlocked "already
        # submitted?" check before either can submit. The broker is slow to
        # answer the second tap's duplicate lookup, as a network round trip is.
        env.broker.duplicate_lookup_s = 0.2
        results: list[int] = []

        def tap() -> None:
            results.append(env.client.post("/v1/orders/ord-1/approve").status_code)

        with exclusive(submit_lock.lock_path()):
            taps = [threading.Thread(target=tap) for _ in range(2)]
            for t in taps:
                t.start()
            time.sleep(0.5)  # both are now waiting on the lock
        for t in taps:
            t.join(20)

        assert sorted(results) == [200, 409]
        assert list(env.broker.live.values()) == ["b-1"]
        # The trade log still knows the order is at the broker, so /cancel
        # can reach it, and it does not read as rejected.
        with env.repo.session() as s:
            assert s.get(TradeOrderRow, "ord-1").broker_order_id == "b-1"
        assert _statuses(env.repo) == ["PENDING", "ACCEPTED"]


#: What a 207 with one refused close turns into (execution/flatten.py).
_PARTIAL = FlattenResult(
    ok=False,
    summary="PARTIAL flatten: 1/2 submitted, FAILED: MSFT: status=403 — failed positions "
    "may be UNPROTECTED (stop legs were cancelled)",
    submitted=["AAPL"], failed=["MSFT: status=403"],
)


def _failing_flatten(broker: _Broker, failure: str, delay_s: float = 0.0) -> Callable[..., Any]:
    def flatten(client: Any = None) -> FlattenResult:
        broker.events.append("FLATTEN_ALL")
        time.sleep(delay_s)
        if failure == "raises":
            raise RuntimeError("DELETE /v2/positions timed out")
        return _PARTIAL

    return flatten


class TestAFlattenThatFailsAfterItsAnswer:
    """The request answered 202 and is gone; the failure must still reach a human."""

    @pytest.mark.parametrize(("failure", "words"), [
        ("partial", "UNPROTECTED"), ("raises", "timed out"),
    ])
    def test_it_is_paged(
        self, env, monkeypatch: pytest.MonkeyPatch, failure: str, words: str
    ) -> None:
        # A time exit's close holds the lock for minutes; the flatten runs, and
        # fails, long after the app was told it was sent.
        from api.routes import orders

        monkeypatch.setattr(orders, "KILL_SWITCH_ANSWER_S", 0.3)
        monkeypatch.setattr(orders, "flatten_all", _failing_flatten(env.broker, failure))
        with exclusive(submit_lock.lock_path()):
            r = env.client.post("/v1/orders/kill-switch", json={"state": "FLATTEN_ALL"})
            assert r.status_code == 202
        assert env.pages.wait(5), "the failed flatten reached nobody"
        assert env.broker.events == ["FLATTEN_ALL"]
        [(title, body, kind)] = env.pages.sent
        assert "FLATTEN_ALL" in title
        assert words in body
        assert kind == "kill_switch"
