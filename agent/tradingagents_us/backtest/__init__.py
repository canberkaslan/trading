"""Backtest harness — vectorbt + survivorship-safe universe.

Two entry points:
- run_signal_backtest(signals_df, prices_df, ...) — generic; feed in any
  long/short signal frame
- run_decisions_backtest(decisions, prices_df, ...) — replays a list of
  AgentDecisions produced by the LLM pipeline through the risk sizer +
  vectorbt portfolio

Walk-forward CV helper in `walk_forward.py` (Phase 3e+1).

`exit_paths` lives here too and is pure: no vectorbt, no pandas, no network.
That is why the re-exports below are **lazy**. Importing them eagerly made
`from tradingagents_us.backtest.exit_paths import ...` pull in vectorbt, which
imports plotly, which since plotly 6 raises on vectorbt's `scattermapbox`
reference — so a pure module with no plotting in it took the whole test
collection down on CI while passing on a laptop with an older pin. A module's
dependencies should be the ones it uses; PEP 562 makes that free here, and
`from tradingagents_us.backtest import BacktestConfig` still works for the
callers that do want the engine.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .engine import (
        BacktestConfig,
        BacktestResult,
        run_signal_backtest,
        summary_stats,
    )

_ENGINE_EXPORTS = frozenset(
    {"BacktestConfig", "BacktestResult", "run_signal_backtest", "summary_stats"}
)

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "run_signal_backtest",
    "summary_stats",
]


def __getattr__(name: str) -> Any:
    if name in _ENGINE_EXPORTS:
        from . import engine

        return getattr(engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
