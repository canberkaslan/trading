"""GET /v1/trades — the realized-P&L ledger as the app sees it.

The scorecard already shows Sharpe and drawdown off the equity curve; this
endpoint is the first thing that answers "does it actually win trades". The
tests below guard the two ways that answer can be dishonest: stats computed
over a different set than the rows returned, and an empty/unreconciled ledger
rendering as a confident zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.execution.reconcile import ClosedTrade
from tradingagents_us.storage import TradeLogRepository

T0 = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)


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
    # Default to "no eval window" so the base cases below describe the whole
    # ledger regardless of what the developer's shell exports; the window
    # tests set the cutoff explicitly.
    monkeypatch.delenv("EVAL_START_DATE", raising=False)

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


class TestEvalWindow:
    """The ledger must measure the same book /v1/eval does.

    Live case this encodes: on 2026-06-24 the over-buying bug was fixed and the
    stacked AAPL/MSFT lots were flattened in one go — 24 round trips, −$657.
    The scorecard excludes them (EVAL_START_DATE=2026-06-24); the ledger did
    not, so "26.7% win rate, −$17.73 expectancy" was mostly a report on a bug
    that had already been fixed.
    """

    def test_default_window_drops_trades_entered_before_the_cutoff(
        self, client: TestClient, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_START_DATE", "2026-08-02")
        repo.upsert_closed_trades([
            _trade("old", "AAPL", -500.0, day=0),   # entered 08-01, pre-cutoff
            _trade("new", "MSFT", 100.0, day=2),    # entered 08-03
        ])

        body = client.get("/v1/trades").json()

        assert [t["ticker"] for t in body["trades"]] == ["MSFT"]
        assert body["stats"]["net_pnl"] == 100.0
        assert body["stats"]["win_rate"] == 1.0
        assert body["window"] == "eval"
        assert body["eval_start_utc"].startswith("2026-08-02T00:00:00")
        assert body["excluded_pre_eval"] == 1

    def test_cutoff_is_on_the_entry_not_the_exit(
        self, client: TestClient, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Entered 08-01, closed 08-03: the position was chosen and sized by the
        # pre-cutoff agent, so it says nothing about the current book.
        monkeypatch.setenv("EVAL_START_DATE", "2026-08-02")
        repo.upsert_closed_trades([_trade("straddler", "AAPL", -80.0, day=0)])

        body = client.get("/v1/trades").json()

        assert body["trades"] == []
        assert body["stats"]["trades"] == 0
        assert body["excluded_pre_eval"] == 1

    def test_window_all_returns_the_full_history(
        self, client: TestClient, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_START_DATE", "2026-08-02")
        repo.upsert_closed_trades([
            _trade("old", "AAPL", -500.0, day=0),
            _trade("new", "MSFT", 100.0, day=2),
        ])

        body = client.get("/v1/trades", params={"window": "all"}).json()

        assert body["stats"]["trades"] == 2
        assert body["stats"]["net_pnl"] == -400.0
        assert body["window"] == "all_time"
        # Still reported: the app explains the gap between the two views with it.
        assert body["excluded_pre_eval"] == 1

    def test_excluded_count_respects_the_ticker_filter(
        self, client: TestClient, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EVAL_START_DATE", "2026-08-02")
        repo.upsert_closed_trades([
            _trade("a-old", "AAPL", -50.0, day=0),
            _trade("m-old", "MSFT", -60.0, day=0),
            _trade("m-new", "MSFT", 10.0, day=2),
        ])

        body = client.get("/v1/trades", params={"ticker": "MSFT"}).json()

        assert body["stats"]["trades"] == 1
        assert body["excluded_pre_eval"] == 1  # not 2 — AAPL is out of scope

    def test_limit_applies_after_the_cutoff(
        self, client: TestClient, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Filtering in Python after a LIMIT would return an empty page here
        # while newer in-window trades existed.
        monkeypatch.setenv("EVAL_START_DATE", "2026-08-02")
        repo.upsert_closed_trades([
            _trade("old1", "AAPL", -10.0, day=0),
            _trade("old2", "AAPL", -10.0, day=0),
            _trade("new", "MSFT", 25.0, day=2),
        ])

        body = client.get("/v1/trades", params={"limit": 2}).json()

        assert [t["ticker"] for t in body["trades"]] == ["MSFT"]
        assert body["stats"]["net_pnl"] == 25.0

    def test_no_cutoff_configured_reports_all_time(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        repo.upsert_closed_trades([_trade("t1", "AAPL", 10.0)])

        body = client.get("/v1/trades").json()

        assert body["window"] == "all_time"
        assert body["eval_start_utc"] is None
        assert body["excluded_pre_eval"] == 0
        assert body["stats"]["trades"] == 1

    def test_unknown_window_value_is_rejected(self, client: TestClient) -> None:
        assert client.get("/v1/trades", params={"window": "eval-ish"}).status_code == 422


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


class TestExitAttribution:
    """The split by what actually closed each position.

    `stats` blends every exit path into one expectancy, and on this account
    that number is dominated by the 2026-06-24 flatten that cleaned up the
    accumulation bug. These guard the two ways the split can lie: a partially
    attributed ledger rendering as a complete one, and an operator flatten
    being scored as the agent's own exit discipline.
    """

    def test_buckets_and_strategy_roll_up_come_from_the_stored_class(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        repo.upsert_closed_trades(
            [
                _trade("t1", "AAPL", 60.0, day=0),
                _trade("t2", "MSFT", -40.0, day=1),
                _trade("t3", "NVDA", -600.0, day=2),
            ],
            exit_classes={"t1": "take_profit", "t2": "stop", "t3": "flatten"},
        )

        body = client.get("/v1/trades").json()

        by_class = {b["exit_class"]: b for b in body["by_exit"]}
        assert by_class["take_profit"]["net_pnl"] == 60.0
        assert by_class["stop"]["net_pnl"] == -40.0
        assert by_class["flatten"]["net_pnl"] == -600.0
        # The whole point: the agent's own record is +20 on 2 trades, not the
        # -580 the blended ledger reports.
        assert body["stats"]["net_pnl"] == -580.0
        assert body["strategy"]["trades"] == 2
        assert body["strategy"]["net_pnl"] == 20.0
        assert body["unattributed"] == 0

    def test_each_trade_carries_its_own_exit_class(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        repo.upsert_closed_trades(
            [_trade("t1", "AAPL", 60.0)], exit_classes={"t1": "decision_sell"}
        )
        assert client.get("/v1/trades").json()["trades"][0]["exit_class"] == "decision_sell"

    def test_a_never_attributed_ledger_reports_no_split_rather_than_a_zeroed_one(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        # Rows written before attribution existed. An empty bucket list plus a
        # count is honest; a "strategy: 0 trades, $0.00" card would read as
        # "the agent has never closed a position".
        repo.upsert_closed_trades(
            [_trade("t1", "AAPL", 60.0), _trade("t2", "MSFT", -40.0, day=1)]
        )

        body = client.get("/v1/trades").json()

        assert body["by_exit"] == []
        assert body["strategy"] is None
        assert body["unattributed"] == 2
        assert body["trades"][0]["exit_class"] is None
        # The blended stats still stand — those need no order history.
        assert body["stats"]["trades"] == 2

    def test_unattributed_rows_are_counted_so_the_split_cannot_pass_as_complete(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        repo.upsert_closed_trades(
            [
                _trade("t1", "AAPL", 60.0, day=0),
                _trade("t2", "MSFT", -40.0, day=1),
                _trade("t3", "NVDA", -25.0, day=2),
            ],
            exit_classes={"t1": "stop"},
        )

        body = client.get("/v1/trades").json()

        assert body["unattributed"] == 2
        assert sum(b["trades"] for b in body["by_exit"]) == 1
        assert body["stats"]["trades"] == 3

    def test_the_split_follows_the_window_and_filters_the_rows_do(
        self, client: TestClient, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A split computed over a different set than the rows returned is the
        # same bug the stats tests above guard, one level down.
        monkeypatch.setenv("EVAL_START_DATE", (T0 + timedelta(days=1)).date().isoformat())
        repo.upsert_closed_trades(
            [_trade("old", "AAPL", -900.0, day=-5), _trade("new", "MSFT", 50.0, day=2)],
            exit_classes={"old": "flatten", "new": "stop"},
        )

        body = client.get("/v1/trades").json()

        assert body["window"] == "eval"
        assert [b["exit_class"] for b in body["by_exit"]] == ["stop"]
        assert body["strategy"]["net_pnl"] == 50.0

    def test_a_ticker_filter_scopes_the_split_too(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        repo.upsert_closed_trades(
            [_trade("t1", "AAPL", 60.0), _trade("t2", "MSFT", -600.0, day=1)],
            exit_classes={"t1": "stop", "t2": "flatten"},
        )

        body = client.get("/v1/trades", params={"ticker": "AAPL"}).json()

        assert [b["exit_class"] for b in body["by_exit"]] == ["stop"]
        assert body["strategy"]["trades"] == 1

    def test_buckets_carry_a_human_label(
        self, client: TestClient, repo: TradeLogRepository
    ) -> None:
        repo.upsert_closed_trades(
            [_trade("t1", "AAPL", -600.0)], exit_classes={"t1": "flatten"}
        )
        body = client.get("/v1/trades").json()
        assert body["by_exit"][0]["label"] == "flatten (outside the agent)"
        assert body["strategy"]["label"] == "strategy exits only"
