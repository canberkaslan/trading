"""/v1/portfolio/snapshot — honest daily P&L + intraday max drawdown,
plus the trading_mode field on /healthz and /readyz."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.routes.portfolio import _intraday_max_dd


class TestIntradayMaxDd:
    def test_empty_payload(self) -> None:
        assert _intraday_max_dd({}) == 0.0
        assert _intraday_max_dd({"equity": []}) == 0.0
        assert _intraday_max_dd({"equity": [100_000.0]}) == 0.0

    def test_monotonic_up_has_zero_dd(self) -> None:
        assert _intraday_max_dd({"equity": [100.0, 101.0, 102.0]}) == 0.0

    def test_peak_to_trough(self) -> None:
        # peak 104 -> trough 100.88 = -3%
        dd = _intraday_max_dd({"equity": [100.0, 104.0, 100.88, 103.0]})
        assert dd == pytest.approx(-0.03, abs=1e-6)

    def test_none_and_zero_points_skipped(self) -> None:
        # Alpaca pads pre-open bars with 0/None
        dd = _intraday_max_dd({"equity": [None, 0, 100.0, 98.0]})
        assert dd == pytest.approx(-0.02, abs=1e-6)


def _mock_alpaca(portfolio_value=104_000.0, last_equity=100_000.0, intraday=None):
    cli = MagicMock()
    acct = MagicMock()
    acct.cash = 20_000.0
    acct.portfolio_value = portfolio_value
    acct.last_equity = last_equity
    cli.account.return_value = acct
    cli.list_positions.return_value = []
    cli.portfolio_history.return_value = intraday if intraday is not None else {}
    cli.close = MagicMock()
    return cli


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    from api.main import app

    return TestClient(app)


class TestSnapshotDailyPnl:
    def _with_alpaca(self, client: TestClient, cli) -> dict:
        from api.deps import get_alpaca
        from api.main import app

        app.dependency_overrides[get_alpaca] = lambda: cli
        try:
            r = client.get("/v1/portfolio/snapshot")
            assert r.status_code == 200
            return r.json()
        finally:
            app.dependency_overrides.pop(get_alpaca, None)

    def test_daily_pnl_uses_last_equity_not_inception(self, client: TestClient) -> None:
        # equity 104k, prev close 103k -> today is +1k (+0.97%), NOT +4k
        b = self._with_alpaca(client, _mock_alpaca(104_000.0, 103_000.0))
        assert b["daily_pnl_usd"] == pytest.approx(1_000.0)
        assert b["daily_pnl_pct"] == pytest.approx(1_000.0 / 103_000.0)

    def test_missing_last_equity_reports_zero_not_lie(self, client: TestClient) -> None:
        b = self._with_alpaca(client, _mock_alpaca(104_000.0, 0.0))
        assert b["daily_pnl_usd"] == 0.0
        assert b["daily_pnl_pct"] == 0.0

    def test_intraday_dd_surfaced(self, client: TestClient) -> None:
        b = self._with_alpaca(
            client,
            _mock_alpaca(intraday={"equity": [100_000.0, 104_000.0, 100_880.0]}),
        )
        assert b["max_drawdown_today"] == pytest.approx(-0.03, abs=1e-6)

    def test_intraday_history_failure_does_not_break_snapshot(self, client: TestClient) -> None:
        cli = _mock_alpaca()
        cli.portfolio_history.side_effect = RuntimeError("alpaca hiccup")
        b = self._with_alpaca(client, cli)
        assert b["max_drawdown_today"] == 0.0
        assert b["total_equity_usd"] == 104_000.0


class TestTradingModeField:
    def test_healthz_paper_by_default(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ALPACA_BASE_URL", raising=False)
        b = client.get("/healthz").json()
        assert b == {"status": "ok", "trading_mode": "paper"}

    def test_healthz_paper_when_paper_url(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
        assert client.get("/healthz").json()["trading_mode"] == "paper"

    def test_healthz_live_when_live_url(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets/v2")
        assert client.get("/healthz").json()["trading_mode"] == "live"


def _order(symbol, side="sell", order_type="stop", status="held", qty=10.0,
           filled=0.0, stop_price=90.0, legs=()):
    o = MagicMock()
    o.symbol, o.side, o.order_type, o.status = symbol, side, order_type, status
    o.qty, o.filled_qty, o.stop_price = qty, filled, stop_price
    o.legs = legs
    return o


def _position(symbol, qty):
    p = MagicMock()
    p.symbol, p.qty = symbol, qty
    p.avg_entry_price, p.market_value = 100.0, 100.0 * qty
    p.unrealized_pl, p.unrealized_plpc = 0.0, 0.0
    return p


class TestSnapshotStopLoss:
    """`stop_loss` used to be hardcoded 0.0 on every position. It now carries the
    live stop, but ONLY when the whole position is behind one — a price next to
    a half-covered name reads as "protected" when it is not."""

    def _stops(self, client: TestClient, positions, orders=None, orders_exc=None) -> dict:
        from api.deps import get_alpaca
        from api.main import app

        cli = _mock_alpaca()
        cli.list_positions.return_value = positions
        if orders_exc is not None:
            cli.list_orders.side_effect = orders_exc
        else:
            cli.list_orders.return_value = orders or []
        app.dependency_overrides[get_alpaca] = lambda: cli
        try:
            r = client.get("/v1/portfolio/snapshot")
            assert r.status_code == 200, r.text
            return {p["ticker"]: p["stop_loss"] for p in r.json()["positions"]}
        finally:
            app.dependency_overrides.pop(get_alpaca, None)

    def test_held_bracket_leg_is_reported(self, client: TestClient) -> None:
        # The resting stop of a bracket is nested under its parent in status
        # `held` — the shape that a naive `status=open` read misses entirely.
        parent = _order("AAPL", side="buy", order_type="limit", status="filled",
                        qty=10.0, filled=10.0, stop_price=None,
                        legs=(_order("AAPL", order_type="limit", status="new", stop_price=None),
                              _order("AAPL", stop_price=280.0)))
        assert self._stops(client, [_position("AAPL", 10)], [parent]) == {"AAPL": 280.0}

    def test_partly_covered_position_reports_no_stop(self, client: TestClient) -> None:
        stops = self._stops(client, [_position("MSFT", 27)], [_order("MSFT", qty=10.0)])
        assert stops == {"MSFT": 0.0}

    def test_indeterminate_order_reports_no_stop(self, client: TestClient) -> None:
        stops = self._stops(client, [_position("NVDA", 10)],
                            [_order("NVDA", status="pending_cancel")])
        assert stops == {"NVDA": 0.0}

    def test_several_stops_report_the_highest(self, client: TestClient) -> None:
        stops = self._stops(client, [_position("XOM", 20)],
                            [_order("XOM", qty=10.0, stop_price=150.0),
                             _order("XOM", qty=10.0, stop_price=155.0)])
        assert stops == {"XOM": 155.0}

    def test_terminal_stop_is_not_protection(self, client: TestClient) -> None:
        stops = self._stops(client, [_position("UNH", 15)],
                            [_order("UNH", qty=15.0, status="canceled")])
        assert stops == {"UNH": 0.0}

    def test_order_book_failure_degrades_to_unknown(self, client: TestClient) -> None:
        stops = self._stops(client, [_position("META", 18)],
                            orders_exc=RuntimeError("orders endpoint down"))
        assert stops == {"META": 0.0}
