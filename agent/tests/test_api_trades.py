"""GET /v1/trades — the realized-P&L ledger as the app sees it.

The scorecard already shows Sharpe and drawdown off the equity curve; this
endpoint is the first thing that answers "does it actually win trades". The
tests below guard the two ways that answer can be dishonest: stats computed
over a different set than the rows returned, and an empty/unreconciled ledger
rendering as a confident zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.execution.reconcile import ClosedTrade
from tradingagents_us.storage import TradeLogRepository

T0 = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)


def _trade(
    tid: str, symbol: str, pnl: float, day: int = 0, qty: float = 10.0
) -> ClosedTrade:
    entry = 100.0
    return ClosedTrade(
        trade_id=tid,
        symbol=symbol,
        direction="LONG",
        quantity=qty,
        entry_price=entry,
        exit_price=entry + pnl / qty,
        opened_at_utc=T0 + timedelta(days=day),
        closed_at_utc=T0 + timedelta(days=day + 2),
        realized_pnl=pnl,
        realized_pnl_pct=pnl / (entry * qty),
        holding_days=2.0,
        open_activity_id=f"o-{tid}",
        close_activity_id=f"c-{tid}",
    )


@pytest.fixture()
def repo(tmp_path: Path) -> TradeLogRepository:
    from sqlalchemy import create_engine

    return TradeLogRepository(
        engine=create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True)
    )


@pytest.fixture()
def client(repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)

    from api.deps import get_repo
    from api.main import app

    app.dependency_overrides[get_repo] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.pop(get_repo, None)


class TestTradesList:
    def test_returns_closed_trades_newest_first_with_stats(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        repo.upsert_closed_trades([
            _trade("t1", "AAPL", 100.0, day=0),
            _trade("t2", "MSFT", -40.0, day=1),
            _trade("t3", "NVDA", 60.0, day=2),
        ])

        r = client.get("/v1/trades")
        assert r.status_code == 200
        body = r.json()

        assert [t["ticker"] for t in body["trades"]] == ["NVDA", "MSFT", "AAPL"]
        assert body["stats"]["trades"] == 3
        assert body["stats"]["wins"] == 2
        assert body["stats"]["losses"] == 1
        assert body["stats"]["net_pnl"] == 120.0
        assert body["stats"]["profit_factor"] == 4.0
        assert body["reconciled_at_utc"] is not None

    def test_ticker_filter_scopes_the_stats_too(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        # A filtered list carrying whole-account stats is the bug this guards:
        # per-name win rate must describe that name.
        repo.upsert_closed_trades([
            _trade("t1", "AAPL", 100.0),
            _trade("t2", "AAPL", 50.0, day=1),
            _trade("t3", "MSFT", -500.0, day=2),
        ])

        body = client.get("/v1/trades", params={"ticker": "AAPL"}).json()

        assert [t["ticker"] for t in body["trades"]] == ["AAPL", "AAPL"]
        assert body["stats"]["trades"] == 2
        assert body["stats"]["win_rate"] == 1.0
        assert body["stats"]["net_pnl"] == 150.0

    def test_limit_scopes_the_stats_too(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        repo.upsert_closed_trades([
            _trade("t1", "AAPL", -100.0, day=0),
            _trade("t2", "MSFT", 200.0, day=1),
        ])

        body = client.get("/v1/trades", params={"limit": 1}).json()

        assert len(body["trades"]) == 1
        assert body["stats"]["trades"] == 1
        assert body["stats"]["net_pnl"] == 200.0

    def test_empty_ledger_is_zeroed_with_null_reconciled_at(
        self, client: TestClient
    ) -> None:
        # Never-reconciled must be distinguishable from "reconciled, no trades"
        # — the app shows the former as a setup gap, not as a flat record.
        body = client.get("/v1/trades").json()

        assert body["trades"] == []
        assert body["stats"]["trades"] == 0
        assert body["stats"]["profit_factor"] is None
        assert body["reconciled_at_utc"] is None

    def test_limit_is_bounded(self, client: TestClient) -> None:
        assert client.get("/v1/trades", params={"limit": 0}).status_code == 422
        assert client.get("/v1/trades", params={"limit": 5000}).status_code == 422


class TestLedgerIdempotency:
    def test_replaying_the_same_trades_does_not_duplicate(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        # The reconciler replays the full fill history every run; a second pass
        # must converge on the same ledger, not append to it.
        trades = [_trade("t1", "AAPL", 100.0), _trade("t2", "MSFT", -40.0, day=1)]

        assert repo.upsert_closed_trades(trades) == 2
        assert repo.upsert_closed_trades(trades) == 0  # nothing NEW closed

        body = client.get("/v1/trades").json()
        assert body["stats"]["trades"] == 2
