"""Position sizing algorithms.

Default: ATR-based with a portfolio-vol-target overlay. Fractional Kelly is
available for high-conviction signals where p_win and b are well-estimated.

Never full Kelly — it assumes perfect probability estimates we never have.
"""

from __future__ import annotations


def kelly_fraction(p_win: float, win_loss_ratio: float, kelly_mult: float = 0.25) -> float:
    """Fractional Kelly. kelly_mult typically 0.25-0.5 — never 1.0 in live.

    Returns fraction of equity (0..1) to risk on this trade.
    """
    if not 0.0 < p_win < 1.0:
        raise ValueError(f"p_win must be in (0, 1), got {p_win}")
    if win_loss_ratio <= 0:
        raise ValueError(f"win_loss_ratio must be > 0, got {win_loss_ratio}")
    if not 0.0 < kelly_mult <= 0.5:
        raise ValueError(f"kelly_mult must be in (0, 0.5], got {kelly_mult}")

    full = p_win - (1.0 - p_win) / win_loss_ratio
    return max(0.0, full * kelly_mult)


def atr_position_size(
    equity: float,
    atr: float,
    price: float,
    risk_per_trade: float = 0.005,
    atr_mult: float = 2.0,
) -> int:
    """ATR-based sizing. Risk `risk_per_trade` of equity; stop = atr_mult*ATR away.

    Default 0.5% risk per trade. Returns share count (int).
    """
    if equity <= 0 or atr <= 0 or price <= 0:
        return 0
    dollar_risk = equity * risk_per_trade
    stop_distance = atr * atr_mult
    if stop_distance <= 0:
        return 0
    shares = dollar_risk / stop_distance
    return int(shares)


def vol_target_size(
    equity: float,
    target_annual_vol: float,
    asset_annual_vol: float,
    price: float,
    max_weight: float = 1.0,
) -> int:
    """Volatility targeting. Target portfolio vol; weight = target_vol/asset_vol.

    Caps at max_weight (default 1.0 = no leverage).
    """
    if equity <= 0 or asset_annual_vol <= 0 or price <= 0:
        return 0
    weight = min(target_annual_vol / asset_annual_vol, max_weight)
    notional = equity * weight
    return int(notional / price)


def apply_portfolio_caps(
    suggested_shares: int,
    price: float,
    equity: float,
    existing_position_value: float,
    max_position_pct: float = 0.10,
) -> int:
    """Cap final size so total position (existing + new) stays within max_position_pct."""
    if equity <= 0:
        return 0
    max_position_value = equity * max_position_pct
    headroom = max(0.0, max_position_value - existing_position_value)
    max_new_shares = int(headroom / price) if price > 0 else 0
    return min(suggested_shares, max_new_shares)
