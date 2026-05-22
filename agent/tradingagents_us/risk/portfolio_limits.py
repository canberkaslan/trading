"""Portfolio-level limits enforced at order-submission time.

These are hard caps; the LLM agent's suggested size is then trimmed to fit.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PortfolioLimits:
    max_position_pct: float = 0.10
    max_sector_pct: float = 0.30
    max_correlated: int = 3
    max_gross_exposure: float = 1.5
    min_liquidity_adv: float = 100_000.0  # min average daily $ volume


@dataclass(frozen=True)
class PortfolioContext:
    equity: float
    existing_position_values_by_ticker: dict[str, float]
    existing_position_values_by_sector: dict[str, float]
    high_correlation_count: int  # count of existing positions with |rho| > 0.7 vs candidate


def check_limits(
    ticker: str,
    sector: str | None,
    new_position_value: float,
    avg_daily_volume_usd: float,
    ctx: PortfolioContext,
    limits: PortfolioLimits = PortfolioLimits(),
) -> tuple[bool, list[str]]:
    """Return (allowed, rejection_reasons)."""
    reasons: list[str] = []

    if ctx.equity <= 0:
        return False, ["zero_equity"]

    # 1. Per-name cap
    existing_in_ticker = ctx.existing_position_values_by_ticker.get(ticker, 0.0)
    total_in_ticker = existing_in_ticker + new_position_value
    if total_in_ticker / ctx.equity > limits.max_position_pct:
        reasons.append(
            f"position_pct={(total_in_ticker / ctx.equity):.2%} exceeds {limits.max_position_pct:.0%}"
        )

    # 2. Per-sector cap
    if sector:
        existing_in_sector = ctx.existing_position_values_by_sector.get(sector, 0.0)
        total_in_sector = existing_in_sector + new_position_value
        if total_in_sector / ctx.equity > limits.max_sector_pct:
            reasons.append(
                f"sector_pct={(total_in_sector / ctx.equity):.2%} exceeds {limits.max_sector_pct:.0%}"
            )

    # 3. Correlation cap
    if ctx.high_correlation_count >= limits.max_correlated:
        reasons.append(
            f"correlated_positions={ctx.high_correlation_count} exceeds {limits.max_correlated}"
        )

    # 4. Gross exposure
    gross = (
        sum(ctx.existing_position_values_by_ticker.values()) + new_position_value
    ) / ctx.equity
    if gross > limits.max_gross_exposure:
        reasons.append(f"gross_exposure={gross:.2f} exceeds {limits.max_gross_exposure}")

    # 5. Liquidity
    if avg_daily_volume_usd < limits.min_liquidity_adv:
        reasons.append(f"adv_usd={avg_daily_volume_usd:.0f} below {limits.min_liquidity_adv:.0f}")

    return (len(reasons) == 0, reasons)
