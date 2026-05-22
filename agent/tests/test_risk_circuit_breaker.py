"""Circuit breaker unit tests."""

from __future__ import annotations

from tradingagents_tr.risk.circuit_breaker import CircuitBreaker
from tradingagents_tr.risk.kill_switch import StaticKillSwitchReader


def make_breaker(ks_state: str = "RUN") -> CircuitBreaker:
    return CircuitBreaker(kill_switch=StaticKillSwitchReader(ks_state))  # type: ignore[arg-type]


class TestKillSwitch:
    def test_run_allows(self) -> None:
        cb = make_breaker("RUN")
        ok, _ = cb.check(equity_now=100, equity_open=100, price=10, rolling_mean=10, rolling_std=1)
        assert ok

    def test_pause_new_blocks(self) -> None:
        cb = make_breaker("PAUSE_NEW")
        ok, reasons = cb.check(equity_now=100, equity_open=100, price=10, rolling_mean=10, rolling_std=1)
        assert not ok
        assert any("kill_switch" in r for r in reasons)

    def test_flatten_all_blocks(self) -> None:
        cb = make_breaker("FLATTEN_ALL")
        ok, _ = cb.check(equity_now=100, equity_open=100, price=10, rolling_mean=10, rolling_std=1)
        assert not ok


class TestDrawdown:
    def test_within_dd_allows(self) -> None:
        cb = make_breaker()
        # -2% DD vs 3% limit → allowed
        ok, _ = cb.check(equity_now=98, equity_open=100, price=10, rolling_mean=10, rolling_std=1)
        assert ok

    def test_exceeds_dd_blocks(self) -> None:
        cb = make_breaker()
        ok, reasons = cb.check(equity_now=96, equity_open=100, price=10, rolling_mean=10, rolling_std=1)
        assert not ok
        assert any("drawdown" in r for r in reasons)


class TestPriceAnomaly:
    def test_normal_price_allows(self) -> None:
        cb = make_breaker()
        ok, _ = cb.check(equity_now=100, equity_open=100, price=11, rolling_mean=10, rolling_std=1)
        assert ok

    def test_spike_blocks(self) -> None:
        cb = make_breaker()
        # price 15, mean 10, std 1 → z=5 > 3
        ok, reasons = cb.check(equity_now=100, equity_open=100, price=15, rolling_mean=10, rolling_std=1)
        assert not ok
        assert any("z_score" in r for r in reasons)


class TestErrorRate:
    def test_high_error_rate_blocks(self) -> None:
        cb = make_breaker()
        for _ in range(30):
            cb.record_api_call(error=True)
        ok, reasons = cb.check(equity_now=100, equity_open=100, price=10, rolling_mean=10, rolling_std=1)
        assert not ok
        assert any("error_rate" in r for r in reasons)


class TestConsecutiveLosses:
    def test_five_losses_blocks(self) -> None:
        cb = make_breaker()
        for _ in range(5):
            cb.record_trade_result(profitable=False)
        ok, reasons = cb.check(equity_now=100, equity_open=100, price=10, rolling_mean=10, rolling_std=1)
        assert not ok
        assert any("consecutive_losses" in r for r in reasons)
