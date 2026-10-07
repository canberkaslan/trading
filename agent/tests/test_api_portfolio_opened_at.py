"""/v1/portfolio/snapshot `opened_at_utc` — recovered from the fill ledger.

It used to be `datetime.now(UTC)` for every position, so the whole book read
as opened this instant on every poll.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.routes import portfolio as portfolio_route
from tradingagents_us.dataflows.alpaca_broker import FillActivity, Position
from tradingagents_us.execution.reconcile import OpenLot, holding_since

T1 = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
T2 = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)
T3 = datetime(2026, 9, 20, 14, 0, tzinfo=UTC)


def _lot(symbol: str, qty: float, at: datetime, direction: str = "LONG") -> OpenLot:
    return OpenLot(symbol, direction, qty, 100.0, at, f"{symbol}-{at:%d}")


def _fill(i: str, symbol: str, side: str, qty: float, at: datetime) -> FillActivity:
    return FillActivity(i, symbol, side, qty, 100.0, at)  # type: ignore[arg-type]


def _pos(symbol: str, qty: float) -> Position:
    return Position(symbol, qty, "long", 100.0, 100.0 * qty, 0.0, 0.0)


class TestHoldingSince:
    def test_oldest_surviving_lot_when_quantities_reconcile(self) -> None:
        lots = [_lot("AAPL", 10, T2), _lot("AAPL", 5, T1)]
        assert holding_since(lots, {"AAPL": 15}) == {"AAPL": T1}

    def test_truncated_ledger_is_left_out_not_guessed(self) -> None:
        # 15 held but the ledger only accounts for 10 — the oldest lot it saw
        # is not necessarily when this holding began.
        assert holding_since([_lot("AAPL", 10, T2)], {"AAPL": 15}) == {}

    def test_short_lots_and_unheld_symbols_ignored(self) -> None:
        lots = [_lot("XOM", 5, T1, "SHORT"), _lot("NVDA", 3, T1)]
        assert holding_since(lots, {"XOM": 5}) == {}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.setitem(portfolio_route._opened_cache, "key", None)
    from api.main import app

    return TestClient(app)


def _cli(positions: list[Position], fills: list[FillActivity]) -> MagicMock:
    cli = MagicMock()
    acct = MagicMock(cash=1_000.0, portfolio_value=100_000.0, last_equity=100_000.0)
    cli.account.return_value = acct
    cli.list_positions.return_value = positions
    cli.portfolio_history.return_value = {}
    cli.list_orders.return_value = []
    cli.list_fill_activities.return_value = fills
    return cli


def _snapshot(client: TestClient, cli: MagicMock) -> dict[str, object]:
    from api.deps import get_alpaca
    from api.main import app

    app.dependency_overrides[get_alpaca] = lambda: cli
    try:
        r = client.get("/v1/portfolio/snapshot")
        assert r.status_code == 200
        return {p["ticker"]: p["opened_at_utc"] for p in r.json()["positions"]}
    finally:
        app.dependency_overrides.pop(get_alpaca, None)


class TestSnapshotOpenedAt:
    def test_dates_come_from_fills_not_now(self, client: TestClient) -> None:
        fills = [
            # A closed round trip, then the current holding from T2.
            _fill("1", "MSFT", "buy", 10, T1),
            _fill("2", "MSFT", "sell", 10, T1.replace(hour=15)),
            _fill("3", "MSFT", "buy", 7, T2),
            _fill("4", "MSFT", "buy", 3, T3),
        ]
        got = _snapshot(client, _cli([_pos("MSFT", 10)], fills))
        assert datetime.fromisoformat(str(got["MSFT"]).replace("Z", "+00:00")) == T2

    def test_unaccounted_position_is_null(self, client: TestClient) -> None:
        got = _snapshot(client, _cli([_pos("UNH", 15)], []))
        assert got == {"UNH": None}

    def test_ledger_failure_degrades_to_null(self, client: TestClient) -> None:
        cli = _cli([_pos("UNH", 15)], [])
        cli.list_fill_activities.side_effect = RuntimeError("alpaca hiccup")
        assert _snapshot(client, cli) == {"UNH": None}

    def test_ledger_read_once_while_book_unchanged(self, client: TestClient) -> None:
        cli = _cli([_pos("MSFT", 7)], [_fill("3", "MSFT", "buy", 7, T2)])
        _snapshot(client, cli)
        _snapshot(client, cli)
        assert cli.list_fill_activities.call_count == 1
        cli.list_positions.return_value = [_pos("MSFT", 7), _pos("XOM", 2)]
        cli.list_fill_activities.return_value = [
            _fill("3", "MSFT", "buy", 7, T2),
            _fill("5", "XOM", "buy", 2, T3),
        ]
        got = _snapshot(client, cli)
        assert cli.list_fill_activities.call_count == 2
        assert got["XOM"] is not None
