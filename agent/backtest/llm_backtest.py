"""Strict point-in-time LLM backtest (Method 3).

For each (ticker, as-of-date): run the agent pipeline as of that date, then
score the rating against the REALIZED forward return (yfinance). Yields a
directional hit-rate — the honest "X of 10 right" number, measured on history.

⚠️  LEAKAGE CAVEAT (read docs/RESEARCH.md):
The LLM was trained on data covering the test window, so it has partial
foreknowledge ("Profit Mirage", arXiv:2510.07920). We gate PRICE data to the
as-of date, but cannot un-train the model. Treat results as INDICATIVE, never
proof. Forward paper trading is the only fully clean test. A run also costs
~$1-2 of LLM spend per decision, so the full sweep is on-demand, not in CI.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

# Bullish / bearish / neutral rating buckets.
_BULLISH = {"Buy", "Overweight"}
_BEARISH = {"Sell", "Underweight"}


def forward_return(ticker: str, as_of: str, horizon_days: int = 21) -> float | None:
    """Realized return from the first close on/after `as_of` to ~horizon later."""
    start = pd.Timestamp(as_of)
    df = yf.download(
        ticker, start=start, end=start + pd.Timedelta(days=horizon_days * 2 + 10),
        interval="1d", progress=False, auto_adjust=True,
    )
    if df.empty:
        return None
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    if len(close) <= horizon_days:
        return None
    return float(close.iloc[min(horizon_days, len(close) - 1)] / close.iloc[0] - 1.0)


def score_rating(rating: str, fwd_return: float, deadband: float = 0.01) -> str:
    """'hit' / 'miss' / 'neutral' for a rating vs realized forward return."""
    if rating in _BULLISH:
        return "hit" if fwd_return > 0 else "miss"
    if rating in _BEARISH:
        return "hit" if fwd_return < 0 else "miss"
    # Hold: a hit only if the move stayed within the deadband (correctly flat).
    return "hit" if abs(fwd_return) <= deadband else "neutral"


@dataclass
class PointResult:
    ticker: str
    date: str
    rating: str
    fwd_return: float | None
    score: str | None


def run_point(ticker: str, date: str, horizon: int) -> PointResult:
    """Run the real pipeline as-of `date` and score it. Costs LLM $."""
    from tradingagents_us.graph.pipeline import propagate

    decision = propagate(ticker, date)
    fwd = forward_return(ticker, date, horizon)
    score = score_rating(decision.rating, fwd) if fwd is not None else None
    return PointResult(ticker, date, decision.rating, fwd, score)


def summarize(results: list[PointResult]) -> dict:
    scored = [r for r in results if r.score in ("hit", "miss")]
    hits = sum(1 for r in scored if r.score == "hit")
    return {
        "directional_calls": len(scored),
        "hits": hits,
        "hit_rate_pct": round(hits / len(scored) * 100, 1) if scored else None,
        "neutral_holds": sum(1 for r in results if r.score == "neutral"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Strict PIT LLM backtest (costs LLM $)")
    ap.add_argument("--points", nargs="+", required=True,
                    help="ticker:YYYY-MM-DD pairs, e.g. AAPL:2025-01-15 NVDA:2025-02-03")
    ap.add_argument("--horizon", type=int, default=21)
    args = ap.parse_args()

    print("⚠️  LLM backtest is leakage-contaminated — indicative only.\n")
    results: list[PointResult] = []
    for p in args.points:
        ticker, date = p.split(":")
        print(f"running {ticker} as-of {date} …")
        r = run_point(ticker, date, args.horizon)
        results.append(r)
        fr = f"{r.fwd_return*100:+.1f}%" if r.fwd_return is not None else "n/a"
        print(f"  -> {r.rating}  | fwd {args.horizon}d {fr}  | {r.score}")

    print("\n", summarize(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
