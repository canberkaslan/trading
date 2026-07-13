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
    def test_healthz_paper_by_default(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALPACA_BASE_URL", raising=False)
        b = client.get("/healthz").json()
        assert b == {"status": "ok", "trading_mode": "paper"}

    def test_healthz_paper_when_paper_url(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
        assert client.get("/healthz").json()["trading_mode"] == "paper"

    def test_healthz_live_when_live_url(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets/v2")
        assert client.get("/healthz").json()["trading_mode"] == "live"
