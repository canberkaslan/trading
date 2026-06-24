"""Eval scorecard gate logic — the go/no-go decision must be deterministic."""

from __future__ import annotations

import pandas as pd

from scripts.eval_report import (
    GATE_MAX_DD,
    GATE_SHARPE,
    MIN_TRADING_DAYS,
    Scorecard,
    _equity_series,
    _verdict,
)


def _sc(**kw) -> Scorecard:
    base = dict(
        days=20,
        start_equity=100_000.0,
        end_equity=110_000.0,
        total_return=0.10,
        sharpe=1.5,
        sortino=1.8,
        max_dd=-0.05,
        dd_duration=4,
        calmar=1.2,
        var95=-0.01,
        cvar95=-0.02,
        positive_days_pct=60.0,
        spy_return=0.04,
    )
    base.update(kw)
    return Scorecard(**base)


class TestVerdict:
    def test_go_when_all_gates_pass(self) -> None:
        verdict, reasons = _verdict(_sc())
        assert verdict == "GO"
        assert reasons == []

    def test_too_early_below_min_days(self) -> None:
        verdict, _ = _verdict(_sc(days=MIN_TRADING_DAYS - 1))
        assert verdict == "TOO EARLY"

    def test_no_go_on_low_sharpe(self) -> None:
        verdict, reasons = _verdict(_sc(sharpe=GATE_SHARPE - 0.01))
        assert verdict == "NO-GO"
        assert any("Sharpe" in r for r in reasons)

    def test_no_go_on_deep_drawdown(self) -> None:
        verdict, reasons = _verdict(_sc(max_dd=-(GATE_MAX_DD + 0.01)))
        assert verdict == "NO-GO"
        assert any("MaxDD" in r for r in reasons)

    def test_spy_underperformance_flags_but_does_not_block(self) -> None:
        # beats gates but lags SPY -> still GO, but a reason is surfaced
        verdict, reasons = _verdict(_sc(total_return=0.02, spy_return=0.09))
        assert verdict == "GO"
        assert any("SPY" in r for r in reasons)


class TestEquitySeries:
    def test_trims_leading_flat_baseline(self) -> None:
        # three flat clean-book days, then it starts trading
        ts = [1_700_000_000 + i * 86_400 for i in range(6)]
        eq = [100_000, 100_000, 100_000, 101_000, 100_500, 102_000]
        s = _equity_series({"timestamp": ts, "equity": eq})
        # keeps the baseline bar just before first move; drops earlier flats
        assert s.iloc[0] == 100_000
        assert len(s) <= 4
        assert s.iloc[-1] == 102_000

    def test_drops_none_and_zero_points(self) -> None:
        ts = [1_700_000_000, 1_700_086_400, 1_700_172_800]
        eq = [None, 0, 100_000]
        s = _equity_series({"timestamp": ts, "equity": eq})
        assert list(s) == [100_000.0]

    def test_empty_history_returns_empty(self) -> None:
        assert _equity_series({"timestamp": [], "equity": []}).empty
        assert isinstance(_equity_series({}), pd.Series)
