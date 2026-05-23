"""Vectorbt-based backtest engine.

Wraps `vbt.Portfolio.from_signals` with our risk/cost defaults:
- 0.05% commission per trade (Alpaca free, but model slippage + fees)
- 10 bps slippage on market orders
- Fractional shares allowed (Alpaca supports)
- Cash starting at $100k by default
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
import vectorbt as vbt

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacktestConfig:
    init_cash: float = 100_000.0
    fees: float = 0.0005          # 5 bps round-trip; covers Alpaca regulatory fees
    slippage: float = 0.0010      # 10 bps slippage on market orders
    freq: str = "1D"
    allow_fractional: bool = True
    direction: str = "longonly"   # longonly | shortonly | both


@dataclass(frozen=True)
class BacktestResult:
    portfolio: vbt.Portfolio
    stats: pd.Series
    equity_curve: pd.Series
    returns: pd.Series

    def summary(self) -> dict[str, float | int | str]:
        return summary_stats(self.portfolio)


def run_signal_backtest(
    prices: pd.DataFrame,
    entries: pd.DataFrame,
    exits: pd.DataFrame,
    config: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    """Run a vectorbt portfolio backtest from explicit entry/exit signals.

    Args:
        prices:  DataFrame indexed by datetime, columns = tickers, values = close.
        entries: Same shape, bool — True on bars where we enter.
        exits:   Same shape, bool — True on bars where we exit.
        config:  BacktestConfig.

    Returns:
        BacktestResult with portfolio, stats, equity curve, returns.
    """
    if not (prices.shape == entries.shape == exits.shape):
        raise ValueError(
            f"prices/entries/exits shape mismatch: "
            f"{prices.shape} / {entries.shape} / {exits.shape}"
        )

    portfolio = vbt.Portfolio.from_signals(
        close=prices,
        entries=entries,
        exits=exits,
        init_cash=config.init_cash,
        fees=config.fees,
        slippage=config.slippage,
        freq=config.freq,
        direction=config.direction,
        size=np.inf if not config.allow_fractional else np.inf,  # full available cash
    )

    equity = portfolio.value()
    if isinstance(equity, pd.DataFrame):
        # Multi-column → aggregate across tickers
        equity = equity.sum(axis=1)
    returns = equity.pct_change().fillna(0.0)
    return BacktestResult(portfolio=portfolio, stats=portfolio.stats(), equity_curve=equity, returns=returns)


def summary_stats(portfolio: vbt.Portfolio) -> dict[str, float | int | str]:
    """Extract the core numbers from a vectorbt Portfolio."""
    stats = portfolio.stats()
    # vbt stats may be a Series or a DataFrame depending on portfolio shape;
    # flatten to a dict of scalars where possible.
    if isinstance(stats, pd.DataFrame):
        stats = stats.iloc[:, 0]

    def _g(key: str, default: float = float("nan")) -> float:
        v = stats.get(key, default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return float("nan")

    return {
        "start": str(stats.get("Start", "")),
        "end": str(stats.get("End", "")),
        "total_return_pct": _g("Total Return [%]"),
        "benchmark_return_pct": _g("Benchmark Return [%]"),
        "max_drawdown_pct": _g("Max Drawdown [%]"),
        "max_drawdown_duration_days": _g("Max Drawdown Duration"),
        "sharpe_ratio": _g("Sharpe Ratio"),
        "sortino_ratio": _g("Sortino Ratio"),
        "calmar_ratio": _g("Calmar Ratio"),
        "win_rate_pct": _g("Win Rate [%]"),
        "total_trades": int(_g("Total Trades", 0)),
        "profit_factor": _g("Profit Factor"),
    }
