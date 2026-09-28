"""Runtime circuit breakers.

Checks invoked before every trade submission. Returns False -> trade blocked.

The gates here divide into two kinds, and `check` takes a `side` because the
distinction only matters once you ask which way an order moves risk:

- **Risk-taking gates** — kill switch PAUSE_NEW, the daily drawdown halt, the
  losing streak — stop the system committing MORE capital. They are entry-side.
  Applied to an exit they invert: the account is refused the one order that
  reduces what it has at stake, at the moment it is losing money.
- **The data-sanity gate** — the price anomaly — asks whether the price in
  front of us can be trusted at all. A bad print misprices a sell as readily as
  a buy, so it runs on both sides.
- **The API error rate** is side-blind too, but inert, and nothing counts on it:
  no caller records API calls, and each ticker's trade.py is its own process
  making a handful of them, never the 21 the gate needs before it measures.
- **FLATTEN_ALL** is neither. It hands the book to execution/flatten.py, and
  while that path owns the positions a second seller is how a lot gets sold
  twice. It blocks both sides.
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
        side: str = "BUY",
    ) -> tuple[bool, list[str]]:
        """Return (allowed, reasons_if_blocked).

        `side` decides whether the risk-taking gates apply. It defaults to BUY —
        the strict reading, every gate on — so a caller that does not know its
        side gets the old behavior rather than a silently unguarded entry.
        """
        reasons: list[str] = []
        # A sell closes a position: it puts nothing at stake and takes something
        # off. Only the gates that doubt the price itself have anything to say
        # about it.
        entering = side.upper() != "SELL"

        # 1. Remote kill switch (mobile-controlled, polled every 5s).
        #
        #    PAUSE_NEW is entry-side: kill_switch.py documents it as "no new
        #    entries; manage existing (honor stops)", and an exit IS managing
        #    existing. This matters most where the state is not an operator's
        #    choice at all: FileKillSwitchReader returns PAUSE_NEW for an
        #    unreadable, empty or corrupt flag file. Failing closed on entries is
        #    the right instinct — but a failed read of a local file must not be
        #    able to seal the exits.
        #
        #    FLATTEN_ALL still blocks both sides. It is not a halt on risk-taking:
        #    it hands the book to execution/flatten.py, which cancels the stops and
        #    closes every position. A sell from here would be a second writer on
        #    the same lots, sized off a holding read before the flatten ran — the
        #    double sell scripts/manage_positions.py skips the whole pass to avoid.
        ks_state = self.kill_switch.read()
        if ks_state == "FLATTEN_ALL" or (entering and ks_state == "PAUSE_NEW"):
            reasons.append(f"kill_switch={ks_state}")

        # 2. Drawdown halt — entry-side. Stopping new risk after a bad day is the
        #    point; stopping the exits is how the bad day gets worse. This is
        #    verbatim the hazard tests/test_risk_cash_budget.py names for the cash
        #    cap: it "would strand a position in a drawdown exactly when it most
        #    needs to be exited."
        if entering and equity_open > 0:
            dd = equity_now / equity_open - 1.0
            if dd < -self.max_daily_dd:
                reasons.append(f"daily_drawdown={dd:.3f} below threshold {-self.max_daily_dd}")

        # 3. Price anomaly (>N sigma from rolling mean) — both sides. Not an
        #    exposure limit: it says the print we would trade against looks wrong,
        #    and selling into a bad print is no safer than buying into one.
        if rolling_std > 0:
            z = abs(price - rolling_mean) / rolling_std
            if z > self.vol_z:
                reasons.append(f"price_z_score={z:.2f} above threshold {self.vol_z}")

        # 4. API error rate — both sides, but INERT in this deployment: nothing
        #    calls `record_api_call`, and trade.py builds a fresh breaker in a
        #    fresh process per ticker, which never reaches 21 requests. Kept
        #    because wiring it needs state shared across those processes, and
        #    until then no control, and no sell-side argument, may lean on it.
        if self.state.requests > 20:
            err_rate = self.state.errors / self.state.requests
            if err_rate > self.max_err:
                reasons.append(f"api_error_rate={err_rate:.2%} above threshold {self.max_err:.0%}")

        # 5. Consecutive losses — entry-side. A losing streak is a reason to stop
        #    opening positions, never a reason to hold on to the ones losing.
        if entering and self.state.consecutive_losses >= self.max_consec_losses:
            reasons.append(f"consecutive_losses={self.state.consecutive_losses}")

        return (len(reasons) == 0, reasons)
