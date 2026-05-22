"""Runtime circuit breakers.

Checks invoked before every trade submission. Returns False -> trade blocked.
"""

from __future__ import annotations

from dataclasses import dataclass

from .kill_switch import KillSwitchReader


@dataclass
class BreakerState:
    requests: int = 0
    errors: int = 0
    consecutive_losses: int = 0


class CircuitBreaker:
    """Composite breaker covering drawdown, anomaly, error-rate, and kill switch."""

    def __init__(
        self,
        kill_switch: KillSwitchReader,
        max_daily_dd: float = 0.03,
        vol_z_threshold: float = 3.0,
        max_api_err_rate: float = 0.20,
        max_consec_losses: int = 5,
    ) -> None:
        self.kill_switch = kill_switch
        self.max_daily_dd = max_daily_dd
        self.vol_z = vol_z_threshold
        self.max_err = max_api_err_rate
        self.max_consec_losses = max_consec_losses
        self.state = BreakerState()

    def record_api_call(self, error: bool) -> None:
        self.state.requests += 1
        if error:
            self.state.errors += 1

    def record_trade_result(self, profitable: bool) -> None:
        self.state.consecutive_losses = 0 if profitable else self.state.consecutive_losses + 1

    def check(
        self,
        equity_now: float,
        equity_open: float,
        price: float,
        rolling_mean: float,
        rolling_std: float,
    ) -> tuple[bool, list[str]]:
        """Return (allowed, reasons_if_blocked)."""
        reasons: list[str] = []

        # 1. Remote kill switch (mobile-controlled, polled every 5s)
        ks_state = self.kill_switch.read()
        if ks_state in ("PAUSE_NEW", "FLATTEN_ALL"):
            reasons.append(f"kill_switch={ks_state}")

        # 2. Drawdown halt
        if equity_open > 0:
            dd = equity_now / equity_open - 1.0
            if dd < -self.max_daily_dd:
                reasons.append(f"daily_drawdown={dd:.3f} below threshold {-self.max_daily_dd}")

        # 3. Price anomaly (>N sigma from rolling mean)
        if rolling_std > 0:
            z = abs(price - rolling_mean) / rolling_std
            if z > self.vol_z:
                reasons.append(f"price_z_score={z:.2f} above threshold {self.vol_z}")

        # 4. API error rate
        if self.state.requests > 20:
            err_rate = self.state.errors / self.state.requests
            if err_rate > self.max_err:
                reasons.append(f"api_error_rate={err_rate:.2%} above threshold {self.max_err:.0%}")

        # 5. Consecutive losses
        if self.state.consecutive_losses >= self.max_consec_losses:
            reasons.append(f"consecutive_losses={self.state.consecutive_losses}")

        return (len(reasons) == 0, reasons)
