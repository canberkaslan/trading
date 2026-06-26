"""Backtest baseline signals — deterministic, no look-ahead, no network."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.strategies import ma_crossover, mean_reversion, momentum, rsi


def _series(vals: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(vals), freq="D")
    return pd.DataFrame({"X": vals}, index=idx)


def test_rsi_bounds() -> None:
    up = _series(list(range(1, 60)))  # steadily rising -> high RSI
    r = rsi(up).dropna()
    assert (r <= 100).all().all() and (r >= 0).all().all()
    assert float(r["X"].iloc[-1]) > 70  # strong uptrend -> overbought


def test_ma_crossover_fires_on_cross() -> None:
    # down then up -> a golden cross must appear
    vals = list(np.linspace(100, 60, 60)) + list(np.linspace(60, 140, 60))
    entries, exits = ma_crossover(_series(vals), fast=5, slow=20)
    assert entries["X"].any()  # at least one golden cross
    # signals are booleans aligned to the index, no look-ahead (uses shift)
    assert entries.dtypes.iloc[0] == bool


def test_momentum_and_mean_reversion_fire_on_oscillation() -> None:
    # Oscillating + trending series: momentum crosses zero, RSI crosses its
    # bands — both signal types must fire (monotonic data never crosses).
    t = np.arange(320)
    vals = list(100 + 20 * np.sin(t / 14.0) + np.linspace(0, 30, 320))
    df = _series(vals)
    m_entries, _ = momentum(df, lookback=20)
    r_entries, _ = mean_reversion(df)
    assert m_entries["X"].any()
    assert r_entries["X"].any()


def test_signals_have_no_nan_leak() -> None:
    vals = list(np.linspace(100, 120, 80))
    for fn in (momentum, mean_reversion, ma_crossover):
        entries, exits = fn(_series(vals))
        assert not entries.isna().any().any()
        assert not exits.isna().any().any()
