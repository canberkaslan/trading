"""Position sizing algorithms.

Default: ATR-based with a portfolio-vol-target overlay. Fractional Kelly is
available for high-conviction signals where p_win and b are well-estimated.

Never full Kelly — it assumes perfect probability estimates we never have.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def apply_cash_cap(
    suggested_shares: int,
    price: float,
    available_cash: float,
    cash_utilization: float = 1.0,
) -> int:
    """Cap size so a new opening order cannot spend cash the account does not have.

    Every other cap in this module is a fraction of *equity*, which keeps growing as
    an already fully-invested book appreciates — so equity-only sizing quietly walks
    a long book into margin. This is the only cap denominated in settled cash.

    `cash_utilization` < 1.0 leaves dry powder (e.g. 0.9 = never spend the last 10%).
    Negative cash (already levered) yields 0 — no new exposure until it is unwound.
    """
    if price <= 0 or cash_utilization <= 0:
        return 0
    budget = max(0.0, available_cash) * cash_utilization
    max_new_shares = int(budget / price)
    return min(suggested_shares, max_new_shares)


@dataclass(frozen=True)
class PositionCapHeadroom:
    """What the single-name cap left room for, and why.

    `apply_portfolio_caps` answers "how many shares" and throws the reasoning
    away, which is how a refusal reaches the order log as the bare string
    `trimmed_to_zero_by_portfolio_caps`. That string cannot distinguish the two
    cases that matter: a name already sitting at the cap (the book is saturated
    — no BUY of it will ever pass again until it is trimmed or equity grows)
    from a name with real headroom too small to buy one share (a granularity
    limit that resolves itself on the next up-move in equity). One is a
    structural freeze, the other is noise, and the log currently spells them
    identically.
    """

    max_new_shares: int
    headroom_usd: float
    # Existing position as a share of equity. 0.0 when equity is non-positive —
    # a weight is meaningless without a denominator, and 0.0 reads as "unknown"
    # in the one branch (`priceable=False`/at-cap) that never prints it.
    current_weight_pct: float
    cap_pct: float
    # Headroom exhausted: the name is at or over its cap. Distinct from
    # `max_new_shares == 0`, which is also true when headroom exists but buys
    # less than one share.
    at_cap: bool
    # False when the price is unusable (<= 0), in which case share counts say
    # nothing about the cap at all.
    priceable: bool


def position_cap_headroom(
    price: float,
    equity: float,
    existing_position_value: float,
    max_position_pct: float = 0.10,
) -> PositionCapHeadroom:
    """Explain the single-name cap for one ticker. Pure: no clock, no I/O."""
    if equity <= 0:
        return PositionCapHeadroom(
            max_new_shares=0,
            headroom_usd=0.0,
            current_weight_pct=0.0,
            cap_pct=max_position_pct,
            at_cap=True,
            priceable=price > 0,
        )
    max_position_value = equity * max_position_pct
    headroom = max(0.0, max_position_value - existing_position_value)
    priceable = price > 0
    return PositionCapHeadroom(
        max_new_shares=int(headroom / price) if priceable else 0,
        headroom_usd=headroom,
        current_weight_pct=existing_position_value / equity,
        cap_pct=max_position_pct,
        at_cap=headroom <= 0.0,
        priceable=priceable,
    )


def describe_position_cap_trim(ticker: str, price: float, headroom: PositionCapHeadroom) -> str:
    """One-line detail for a cap refusal, for the order log's rejection reasons.

    Kept free of nested parentheses on purpose: the actionability report groups
    reasons by stripping a trailing `(...)`, so a nested pair would leave a
    dangling fragment and split one cause across several buckets.
    """
    if not headroom.priceable:
        return f"{ticker} unusable price=${price:,.2f}"
    cap = f"cap {headroom.cap_pct:.1%}"
    if headroom.at_cap:
        return f"{ticker} at {headroom.current_weight_pct:.1%} of equity, {cap}, headroom=$0.00"
    return (
        f"{ticker} headroom=${headroom.headroom_usd:,.2f}, {cap}, "
        f"below 1 share @ ${price:,.2f}"
    )


def apply_portfolio_caps(
    suggested_shares: int,
    price: float,
    equity: float,
    existing_position_value: float,
    max_position_pct: float = 0.10,
) -> int:
    """Cap final size so total position (existing + new) stays within max_position_pct."""
    headroom = position_cap_headroom(
        price=price,
        equity=equity,
        existing_position_value=existing_position_value,
        max_position_pct=max_position_pct,
    )
    return min(suggested_shares, headroom.max_new_shares)
