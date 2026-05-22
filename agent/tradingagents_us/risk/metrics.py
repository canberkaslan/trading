"""Risk metrics. Used in backtests and live monitoring."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sharpe(returns: pd.Series, risk_free_rate: float = 0.0, periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio."""
    excess = returns - risk_free_rate / periods_per_year
    sd = excess.std()
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float(excess.mean() / sd * np.sqrt(periods_per_year))


def sortino(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized Sortino — penalizes downside vol only."""
    downside = returns[returns < 0]
    if len(downside) == 0:
        return float("inf")
    dd_std = downside.std()
    if dd_std == 0 or np.isnan(dd_std):
        return 0.0
    return float(returns.mean() / dd_std * np.sqrt(periods_per_year))


def max_drawdown(equity_curve: pd.Series) -> tuple[float, int]:
    """Returns (max_drawdown_pct, duration_in_bars)."""
    cumulative = equity_curve.cummax()
    drawdown = (equity_curve - cumulative) / cumulative
    max_dd = float(drawdown.min())
    # duration: longest gap between high-water marks
    in_dd = drawdown < 0
    durations = []
    count = 0
    for v in in_dd:
        if v:
            count += 1
        else:
            if count > 0:
                durations.append(count)
            count = 0
    if count > 0:
        durations.append(count)
    duration = max(durations) if durations else 0
    return max_dd, duration


def calmar(returns: pd.Series, equity_curve: pd.Series, periods_per_year: int = 252) -> float:
    """Calmar = CAGR / |MaxDD|."""
    cagr = (1 + returns).prod() ** (periods_per_year / len(returns)) - 1 if len(returns) > 0 else 0
    max_dd, _ = max_drawdown(equity_curve)
    if max_dd == 0:
        return float("inf")
    return float(cagr / abs(max_dd))


def var_cvar(returns: pd.Series, confidence: float = 0.95) -> tuple[float, float]:
    """Historical VaR and Conditional VaR (Expected Shortfall) at given confidence."""
    if len(returns) == 0:
        return 0.0, 0.0
    alpha = 1.0 - confidence
    var = float(returns.quantile(alpha))
    tail = returns[returns <= var]
    cvar = float(tail.mean()) if len(tail) > 0 else var
    return var, cvar


def information_ratio(
    returns: pd.Series, benchmark_returns: pd.Series, periods_per_year: int = 252
) -> float:
    """IR vs benchmark."""
    if len(returns) != len(benchmark_returns):
        raise ValueError("returns and benchmark must align")
    diff = returns - benchmark_returns
    te = diff.std()
    if te == 0 or np.isnan(te):
        return 0.0
    return float(diff.mean() / te * np.sqrt(periods_per_year))
