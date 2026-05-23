"""Backtest harness — vectorbt + survivorship-safe universe.

Two entry points:
- run_signal_backtest(signals_df, prices_df, ...) — generic; feed in any
  long/short signal frame
- run_decisions_backtest(decisions, prices_df, ...) — replays a list of
  AgentDecisions produced by the LLM pipeline through the risk sizer +
  vectorbt portfolio

Walk-forward CV helper in `walk_forward.py` (Phase 3e+1).
"""

from .engine import (
    BacktestConfig,
    BacktestResult,
    run_signal_backtest,
    summary_stats,
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "run_signal_backtest",
    "summary_stats",
]
