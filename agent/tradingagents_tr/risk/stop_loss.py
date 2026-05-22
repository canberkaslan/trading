"""Stop-loss strategies.

Choose by trade type — see ADR-005.
"""

from __future__ import annotations

from typing import Literal

StopType = Literal["fixed_pct", "atr_trailing", "time_based", "vol_adjusted"]


def fixed_pct_stop(entry_price: float, pct: float = 0.02, side: Literal["LONG", "SHORT"] = "LONG") -> float:
    """Simple fixed-percent stop. Default 2%."""
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    return entry_price * (1.0 - pct) if side == "LONG" else entry_price * (1.0 + pct)


def atr_trailing_stop(
    current_close: float,
    atr: float,
    previous_stop: float,
    atr_mult: float = 3.0,
    side: Literal["LONG", "SHORT"] = "LONG",
) -> float:
    """Trailing ATR stop. Ratchets in favor of position; never against it."""
    if side == "LONG":
        candidate = current_close - atr * atr_mult
        return max(previous_stop, candidate)
    candidate = current_close + atr * atr_mult
    return min(previous_stop, candidate)


def time_exit(bars_held: int, max_bars: int, pnl_pct: float, min_pnl_threshold: float) -> bool:
    """True -> exit on time. Use for catalysts that didn't materialize."""
    return bars_held > max_bars and abs(pnl_pct) < min_pnl_threshold
