"""The vectorbt engine, exercised without credentials or network.

`test_backtest_engine.py` needs an S3 parquet, so the daily loop deselects it
and has done for months. That deselect turned out to be load-bearing in a way
nobody intended: on 2026-09-09 the test was failing at `import vectorbt`, not
at S3 at all. vectorbt declares `plotly>=4.12.0` with no upper bound and its
`_settings.py` references the `scattermapbox` trace that plotly removed in 6.0,
so a clean resolve produced a `backtest/run.py`, `backtest/optimize.py` and
`tradingagents_us/backtest/engine.py` that all raised on import — and the only
test that would have said so was routinely skipped for an unrelated reason.

So the point of this file is not more engine coverage. It is that the signal
"the engine imports and produces finite numbers" must not be reachable only
through a credential. Everything here is arithmetic on a hand-built series:
no S3, no market data, no keys, nothing to deselect.

It lives in its own module deliberately. The standing deselect names one test
id today, but a `--deselect tests/test_backtest_engine.py` would take the whole
file with it, and this check has to survive that.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

#: Long enough for a 30-bar SMA to warm up and still leave room for a crossover
#: in each direction. The three legs below are sized against this.
_DAYS = 240


def _ramp_series() -> pd.DataFrame:
    """A deterministic down → up → down close series for one synthetic ticker.

    Not random and not seeded-random: the SMA crossover under test is a
    statement about the shape of the path, and a fixed path is the only version
    of that statement a future reader can check by eye. Legs are linear so the
    two crossings are forced by construction rather than hoped for.
    """
    legs = (
        (60, 100.0, 80.0),   # decline — 10 SMA below 30 SMA, no position
        (100, 80.0, 140.0),  # rally — golden cross lands here
        (80, 140.0, 95.0),   # give-back — death cross closes the round-trip
    )
    closes: list[float] = []
    for length, first, last in legs:
        step = (last - first) / (length - 1)
        closes.extend(first + step * i for i in range(length))
    assert len(closes) == _DAYS, f"legs sum to {len(closes)}, expected {_DAYS}"

    index = pd.bdate_range("2024-01-01", periods=_DAYS, name="timestamp")
    return pd.DataFrame({"SYNTH": closes}, index=index)


def _sma_crossover(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The same 10/30 crossover the S3 test uses, so the two agree on method."""
    fast = prices.rolling(10).mean()
    slow = prices.rolling(30).mean()
    entries = ((fast > slow) & (fast.shift(1) <= slow.shift(1))).fillna(False)
    exits = ((fast < slow) & (fast.shift(1) >= slow.shift(1))).fillna(False)
    return entries, exits


def test_engine_imports_in_a_clean_interpreter() -> None:
    """`import` alone must succeed — the failure this file exists for was here.

    Run in a subprocess: by the time the rest of this module executes, pytest
    has already imported the engine, so an in-process assertion would pass on
    a resolve that cannot actually import it from cold.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import tradingagents_us.backtest.engine"],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "the backtest engine no longer imports — check the plotly bound in "
        f"pyproject.toml before anything else:\n{result.stderr}"
    )


def test_crossover_round_trip_produces_finite_metrics() -> None:
    """A completed round-trip, and every headline number finite.

    NaN is the failure mode that matters here: vectorbt returns it rather than
    raising when a portfolio never opens a position, so asserting "it ran" is
    not the same as asserting it did anything.
    """
    from tradingagents_us.backtest import BacktestConfig, run_signal_backtest

    prices = _ramp_series()
    entries, exits = _sma_crossover(prices)
    assert entries.to_numpy().sum() >= 1, "the ramp should force a golden cross"
    assert exits.to_numpy().sum() >= 1, "the give-back leg should force a death cross"

    summary = run_signal_backtest(
        prices, entries, exits, BacktestConfig(init_cash=100_000.0)
    ).summary()

    assert summary["total_trades"] >= 1
    for key in ("total_return_pct", "max_drawdown_pct", "sharpe_ratio"):
        value = summary[key]
        assert isinstance(value, float), f"{key} is {type(value).__name__}, not float"
        assert not math.isnan(value), f"{key} came back NaN"


def test_rally_leg_is_profitable_before_costs() -> None:
    """Direction, not just finiteness — a strategy long a 75% rally must not lose.

    Weak on purpose. The engine's job in this test is arithmetic, and pinning a
    precise return would pin vectorbt's fee and slippage internals rather than
    anything this repo owns.
    """
    from tradingagents_us.backtest import BacktestConfig, run_signal_backtest

    prices = _ramp_series()
    entries, exits = _sma_crossover(prices)
    summary = run_signal_backtest(
        prices, entries, exits, BacktestConfig(init_cash=100_000.0, fees=0.0, slippage=0.0)
    ).summary()

    assert summary["total_return_pct"] > 0.0, (
        "a long-only crossover that entered on the rally leg and exited on the "
        f"death cross returned {summary['total_return_pct']:.2f}%"
    )


def test_shape_mismatch_is_refused() -> None:
    """The guard in `run_signal_backtest` — misaligned signals are a caller bug.

    Silently broadcasting them would put entries on the wrong bars, which reads
    as a mediocre strategy rather than as the wiring error it is.
    """
    from tradingagents_us.backtest import run_signal_backtest

    prices = _ramp_series()
    entries, exits = _sma_crossover(prices)
    with pytest.raises(ValueError, match="shape mismatch"):
        run_signal_backtest(prices, entries.iloc[:-1], exits)
