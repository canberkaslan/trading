"""Portfolio concentration analytics — pure, broker-agnostic.

Read-only diagnostics over the current book: how concentrated is the
portfolio, and how has the position count / top-name weight trended over
the logged snapshots. None of this feeds trading decisions; it is surfaced
to the mobile app and the eval report so a human can eyeball diversification.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Mirror PortfolioLimits.max_position_pct (0.10) — flag any single name above it.
DEFAULT_CONCENTRATION_FLAG_PCT = 10.0


@dataclass(frozen=True)
class PositionWeight:
    """A single holding's gross market value (abs, USD)."""

    ticker: str
    market_value: float


@dataclass(frozen=True)
class ConcentrationMetrics:
    n_positions: int
    gross_exposure_pct: float  # invested / equity * 100
    cash_pct: float
    top_weight_pct: float  # largest single name as % of equity
    top3_weight_pct: float  # sum of three largest names as % of equity
    hhi: float  # Herfindahl index over invested weights, 0..1 (1 = single name)
    effective_n: float  # 1 / hhi — effective number of equal-weight positions
    flags: list[str] = field(default_factory=list)


def compute_concentration(
    positions: list[PositionWeight],
    equity: float,
    *,
    flag_pct: float = DEFAULT_CONCENTRATION_FLAG_PCT,
) -> ConcentrationMetrics:
    """Concentration metrics for the current book.

    HHI / effective_n are computed over *invested* weights (normalized to the
    gross exposure, cash excluded) so they describe diversification among the
    holdings independent of how much dry powder is sitting in cash.
    """
    safe_equity = equity if equity > 0 else 0.0
    gross = sum(abs(p.market_value) for p in positions)

    # Weights as % of equity (cash-inclusive) — used for the human-facing top-N.
    by_equity = sorted(
        (
            (p.ticker, (abs(p.market_value) / safe_equity * 100.0) if safe_equity else 0.0)
            for p in positions
        ),
        key=lambda t: t[1],
        reverse=True,
    )
    top_weight = by_equity[0][1] if by_equity else 0.0
    top3_weight = sum(w for _, w in by_equity[:3])

    # HHI over invested weights (normalized to gross, sum to 1).
    if gross > 0:
        inv_weights = [abs(p.market_value) / gross for p in positions]
        hhi = sum(w * w for w in inv_weights)
    else:
        hhi = 0.0
    effective_n = (1.0 / hhi) if hhi > 0 else 0.0

    gross_exposure_pct = (gross / safe_equity * 100.0) if safe_equity else 0.0
    cash_pct = max(0.0, 100.0 - gross_exposure_pct)

    flags: list[str] = []
    for ticker, w in by_equity:
        if w > flag_pct:
            flags.append(f"{ticker} is {w:.1f}% of equity (> {flag_pct:.0f}% single-name cap)")
    if positions and effective_n and effective_n < 3.0:
        flags.append(
            f"effective_n {effective_n:.1f} < 3 — book behaves like fewer than 3 equal bets"
        )

    return ConcentrationMetrics(
        n_positions=len(positions),
        gross_exposure_pct=round(gross_exposure_pct, 2),
        cash_pct=round(cash_pct, 2),
        top_weight_pct=round(top_weight, 2),
        top3_weight_pct=round(top3_weight, 2),
        hhi=round(hhi, 4),
        effective_n=round(effective_n, 2),
        flags=flags,
    )


@dataclass(frozen=True)
class TrendPoint:
    ts: str
    n_positions: int
    top_weight_pct: float
    equity: float


def parse_trend(records: list[dict], *, limit: int = 30) -> list[TrendPoint]:
    """Extract the position-count / top-weight trend from snapshot records
    (as written by scripts/snapshot.py). Returns the most recent `limit`
    points in chronological order, skipping malformed rows.
    """
    points: list[TrendPoint] = []
    for rec in records:
        try:
            points.append(
                TrendPoint(
                    ts=str(rec["ts"]),
                    n_positions=int(rec.get("n_positions", 0)),
                    top_weight_pct=float(rec.get("top_weight_pct", 0.0)),
                    equity=float(rec.get("equity", 0.0)),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return points[-limit:]
