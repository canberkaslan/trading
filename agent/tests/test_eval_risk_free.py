"""Risk-free-rate sourcing + SPY total-return benchmark for the eval scorecard."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

import pytest

from scripts.eval_report import _risk_free_rate, _spy_return


class _Obs:
    def __init__(self, value):
        self.value = value


def test_rf_from_fred(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = mock.MagicMock()
    fake.__enter__ = mock.Mock(return_value=fake)
    fake.__exit__ = mock.Mock(return_value=False)
    fake.series.return_value = [_Obs(4.1), _Obs(None), _Obs(4.3)]
    with mock.patch("tradingagents_us.dataflows.fred.FREDClient", return_value=fake):
        rf, source = _risk_free_rate()
    assert rf == pytest.approx(0.043)
    assert source == "fred:DGS3MO"


def test_rf_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVAL_RISK_FREE_RATE", "0.04")
    with mock.patch(
        "tradingagents_us.dataflows.fred.FREDClient", side_effect=RuntimeError("no net")
    ):
        rf, source = _risk_free_rate()
    assert rf == pytest.approx(0.04)
    assert source == "env"


def test_rf_zero_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EVAL_RISK_FREE_RATE", raising=False)
    with mock.patch(
        "tradingagents_us.dataflows.fred.FREDClient", side_effect=RuntimeError("no net")
    ):
        rf, source = _risk_free_rate()
    assert rf == 0.0
    assert source == "none"


class _Bar:
    def __init__(self, close):
        self.close = close


def test_spy_return_includes_dividends() -> None:
    fake = mock.MagicMock()
    fake.__enter__ = mock.Mock(return_value=fake)
    fake.__exit__ = mock.Mock(return_value=False)
    fake.aggregates.return_value = [_Bar(100.0), _Bar(101.0)]
    fake.dividends.return_value = [{"cash_amount": 1.5}, {"cash_amount": 0.5}]
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 30, tzinfo=timezone.utc)
    with mock.patch("tradingagents_us.dataflows.polygon.PolygonClient", return_value=fake):
        ret = _spy_return(start, end)
    # (101 + 2.0) / 100 - 1 = 3.0% total return vs 1.0% price-only
    assert ret == pytest.approx(0.03)


def test_spy_return_survives_dividend_failure() -> None:
    fake = mock.MagicMock()
    fake.__enter__ = mock.Mock(return_value=fake)
    fake.__exit__ = mock.Mock(return_value=False)
    fake.aggregates.return_value = [_Bar(100.0), _Bar(102.0)]
    fake.dividends.side_effect = RuntimeError("api down")
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 30, tzinfo=timezone.utc)
    with mock.patch("tradingagents_us.dataflows.polygon.PolygonClient", return_value=fake):
        ret = _spy_return(start, end)
    assert ret == pytest.approx(0.02)  # falls back to price return
