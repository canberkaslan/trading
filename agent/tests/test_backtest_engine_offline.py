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


def _three_name_book() -> pd.DataFrame:
    """Three deterministic paths that disagree: one rallies, two bleed.

    Disagreement is the point. When every name has the same shape, the mean of
    per-name metrics and the metric of the summed book coincide, and a test
    built on that would pass against the bug it is meant to catch.
    """
    days = 300
    index = pd.bdate_range("2022-01-03", periods=days, name="timestamp")

    def zigzag(start: float, drift: float, swing: float, period: int) -> list[float]:
        return [
            start * (1 + drift * i) + swing * (1 if (i // period) % 2 else -1)
            for i in range(days)
        ]

    return pd.DataFrame(
        {
            "UP": zigzag(100.0, 0.0020, 3.0, 23),
            "DOWN": zigzag(100.0, -0.0015, 6.0, 17),
            "CHOP": zigzag(100.0, 0.0, 8.0, 11),
        },
        index=index,
    )


def test_multi_name_summary_scores_the_book_not_the_mean_of_names() -> None:
    """Sharpe, MaxDD and trade count are the summed book's, on a 252-day year.

    Before 2026-09-28 `summary_stats` called plain `stats()`, which on an
    ungrouped multi-column portfolio averages each metric across tickers (the
    "Aggregating using mean" warning). Checked here against the equity curve
    the result already exposes, computed by hand.
    """
    import numpy as np

    from tradingagents_us.backtest import run_signal_backtest

    prices = _three_name_book()
    entries, exits = _sma_crossover(prices)
    result = run_signal_backtest(prices, entries, exits)
    summary = result.summary()

    equity = result.equity_curve
    # vectorbt counts the first bar as a 0% return; drop it and the hand
    # figure drifts by ~0.2%, which reads as a failure of the thing under test.
    returns = result.returns
    sharpe = returns.mean() / returns.std() * np.sqrt(252)
    max_dd = -((equity / equity.cummax()) - 1).min() * 100
    trades = sum(
        int(result.portfolio.trades[c].count()) for c in prices.columns
    )

    assert trades >= 3, "each path should force at least one crossover"
    assert summary["total_trades"] == trades
    assert summary["max_drawdown_pct"] == pytest.approx(max_dd, rel=1e-6)
    assert summary["sharpe_ratio"] == pytest.approx(sharpe, rel=1e-6)
    assert result.stats["Sharpe Ratio"] == pytest.approx(sharpe, rel=1e-6)


def test_multi_name_summary_does_not_mean_aggregate() -> None:
    """The vectorbt warning that marks the averaging path must not fire."""
    import warnings

    from tradingagents_us.backtest import run_signal_backtest

    prices = _three_name_book()
    entries, exits = _sma_crossover(prices)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_signal_backtest(prices, entries, exits).summary()

    averaged = [w for w in caught if "Aggregating using" in str(w.message)]
    assert not averaged, f"stats were mean-aggregated across tickers: {averaged[0].message}"


def test_baseline_runners_annualise_on_252_days() -> None:
    """`backtest.run` and `backtest.optimize` score on the same year as the engine.

    Both called bare `pf.sharpe_ratio()`, which vectorbt annualises on 365 days;
    on business-day bars that reads ~1.20x high against a live gate computed on
    252. Checked against a hand-computed daily-returns Sharpe, not against vbt.
    """
    import numpy as np

    from backtest import optimize, run

    idx = pd.bdate_range("2024-01-01", periods=_DAYS)
    rng = np.random.default_rng(7)
    close = pd.DataFrame(
        {"SYN": 100.0 * np.cumprod(1.0 + rng.normal(0.0005, 0.01, _DAYS))}, index=idx
    )
    entries = pd.DataFrame(False, index=idx, columns=close.columns)
    entries.iloc[0] = True
    exits = pd.DataFrame(False, index=idx, columns=close.columns)

    def hand_sharpe(**kw: float) -> float:
        pf = run.vbt.Portfolio.from_signals(
            close, entries, exits, group_by=True, cash_sharing=True, freq="1D", **kw
        )
        value = pf.value()
        # The first bar's return is measured against starting cash, so an
        # entry fee lands on day one rather than being dropped with it.
        rets = value / value.shift(1).fillna(kw["init_cash"]) - 1.0
        return float(rets.mean() / rets.std(ddof=1) * math.sqrt(252))

    pf = run.vbt.Portfolio.from_signals(
        close, entries, exits, init_cash=run.INIT_CASH, group_by=True,
        cash_sharing=True, freq="1D",
    )
    assert run._metrics(pf)["sharpe"] == pytest.approx(
        hand_sharpe(init_cash=run.INIT_CASH), rel=1e-6
    )
    assert optimize._sharpe(close, entries, exits, sl=0.99, tp=None) == pytest.approx(
        hand_sharpe(
            sl_stop=0.99, fees=optimize.FEES, slippage=optimize.SLIPPAGE,
            init_cash=optimize.INIT,
        ),
        rel=1e-6,
    )
