"""Non-LLM baseline signals — pure price, zero look-ahead.

These establish what "no LLM edge" looks like and exercise the harness. All
signals use only data available at bar t (rolling windows, shifted crossovers).
"""

from __future__ import annotations

import pandas as pd


def rsi(close: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - 100 / (1 + rs)


def momentum(close: pd.DataFrame, lookback: int = 126) -> tuple[pd.DataFrame, pd.DataFrame]:
    """6-month momentum: long while trailing return is positive."""
    mom = close.pct_change(lookback)
    entries = (mom > 0) & (mom.shift(1) <= 0)
    exits = (mom < 0) & (mom.shift(1) >= 0)
    return entries.fillna(False), exits.fillna(False)


def mean_reversion(close: pd.DataFrame, lower: int = 30, upper: int = 60) -> tuple[pd.DataFrame, pd.DataFrame]:
    """RSI mean reversion: buy oversold, exit when normalized."""
    r = rsi(close)
    entries = (r < lower) & (r.shift(1) >= lower)
    exits = (r > upper) & (r.shift(1) <= upper)
    return entries.fillna(False), exits.fillna(False)


def ma_crossover(close: pd.DataFrame, fast: int = 20, slow: int = 50) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Golden/death cross trend follower."""
    f = close.rolling(fast).mean()
    s = close.rolling(slow).mean()
    entries = (f > s) & (f.shift(1) <= s.shift(1))
    exits = (f < s) & (f.shift(1) >= s.shift(1))
    return entries.fillna(False), exits.fillna(False)


STRATEGIES = {
    "momentum_6m": momentum,
    "mean_reversion_rsi": mean_reversion,
    "ma_crossover_20_50": ma_crossover,
}
