#!/usr/bin/env python3
"""Measure each exit path on its own sample, by replaying brackets over history.

The live realized ledger has eight strategy exits in it — four take-profits,
three stops, one decision sell. `scripts/exit_quality.py` reports that split
honestly, and the honesty is the problem: three stops averaging −$138 is not a
finding about the stop rule, it is three trades. Every reading of the NO-GO so
far has had to rest on it anyway, because it was the only exit data there was.

This produces the other kind of evidence. It attaches the live bracket's
*shape* — a protective stop and a take-profit leg at fixed distances from the
fill — to a deterministic entry signal, replays it over years of daily bars,
and scores the result through the same `exit_quality` roll-ups the live ledger
uses. The entry signal is not the subject and is not being defended; it is a
way to generate a few hundred bracketed positions so the exit rule has a sample.

Read-only in the strongest sense: no broker call, no database, no box. It needs
historical prices (yfinance, via `backtest.data.load_ohlcv`) and nothing else.

    python scripts/exit_path_backtest.py
    python scripts/exit_path_backtest.py --stop-pct 3 --take-profit-pct 6
    python scripts/exit_path_backtest.py --tickers AAPL,MSFT --start 2018-01-01

What to look at first is the ambiguous share. Daily bars cannot order two
intrabar events, so a bar that reaches both levels is not scored at all. If
that bucket is large next to the resolved one, the answer to "is the stop rule
bad?" is "daily data cannot tell you", and the next step is intraday bars
rather than a bigger date range.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from tradingagents_us.backtest.exit_paths import (  # noqa: E402
    Bar,
    SimulatedExit,
    attributed,
    census,
    level_mix,
    replay_signals,
    sample_shortfall,
)
from tradingagents_us.execution.exit_quality import (  # noqa: E402
    bucket_by_exit,
    strategy_bucket,
)

#: The live universe, so the replay runs on the names the account actually holds.
DEFAULT_TICKERS = (
    "AAPL,AMZN,GOOGL,JPM,META,MSFT,NVDA,UNH,V,XOM"
)

#: Below this a per-path expectancy is noise, and the report says so rather
#: than printing a number that reads as a finding. Same threshold the mobile
#: "Gerçekleşen" card uses, for the same reason.
MIN_SAMPLE = 30


def _bars_for(frames: dict[str, object], ticker: str) -> list[Bar]:
    """The OHLC frames for one ticker as plain `Bar`s, oldest first.

    Rows with any missing field are dropped rather than filled: a bar with no
    low cannot answer whether a stop was touched, and an interpolated one would
    answer it confidently and wrongly.
    """
    import pandas as pd

    closes = frames["close"]
    assert isinstance(closes, pd.DataFrame)
    if ticker not in closes.columns:
        return []

    frame = pd.DataFrame(
        {
            field: frames[field][ticker]  # type: ignore[index]
            for field in ("open", "high", "low", "close")
        }
    ).dropna()

    bars: list[Bar] = []
    for stamp, row in frame.iterrows():
        day = stamp.date() if hasattr(stamp, "date") else date.fromisoformat(str(stamp)[:10])
        bars.append(
            Bar(
                day=day,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
        )
    return bars


def _signal_days(bars: list[Bar], fast: int, slow: int) -> list[date]:
    """Golden-cross days from the bars' own closes — deterministic, no look-ahead.

    Computed here from `Bar`s rather than through `backtest.strategies` so the
    signal is built from exactly the series the replay walks. Two views of the
    same prices that disagree by a row would shift every fill by a day, and the
    fill price is the thing every level is measured from.
    """
    closes = [b.close for b in bars]
    if len(closes) <= slow:
        return []

    def _sma(period: int, end: int) -> float:
        window = closes[end - period + 1 : end + 1]
        return sum(window) / period

    days: list[date] = []
    for i in range(slow, len(bars)):
        fast_now, slow_now = _sma(fast, i), _sma(slow, i)
        fast_prev, slow_prev = _sma(fast, i - 1), _sma(slow, i - 1)
        if fast_prev <= slow_prev and fast_now > slow_now:
            days.append(bars[i].day)
    return days


def _render(exits: list[SimulatedExit], stop_pct: float, tp_pct: float) -> None:
    counts = census(exits)
    rows = attributed(exits)

    print()
    print(f"Bracket: stop −{stop_pct:.1%} / target +{tp_pct:.1%} from the fill")
    print(counts.summary())
    if counts.ambiguous_share >= 0.25:
        print(
            "  ⚠️  a quarter or more of the exits are unreadable on daily bars — "
            "treat the split below as indicative and get intraday data before "
            "concluding anything about the stop rule"
        )
    print()

    header = f"{'exit path':<22}{'n':>6}{'share':>8}{'net P&L':>14}{'per trade':>12}{'hold d':>9}"
    print(header)
    print("-" * len(header))
    total = sum(b.trades for b in bucket_by_exit(rows)) or 1
    for bucket in bucket_by_exit(rows):
        print(
            f"{bucket.label:<22}{bucket.trades:>6}{bucket.trades / total:>7.0%}"
            f"{bucket.net_pnl:>14,.2f}{bucket.avg_pnl:>12,.2f}"
            f"{bucket.avg_holding_days:>9.1f}"
        )

    strategy = strategy_bucket(rows)
    print("-" * len(header))
    print(
        f"{strategy.label:<22}{strategy.trades:>6}{1.0:>7.0%}"
        f"{strategy.net_pnl:>14,.2f}{strategy.avg_pnl:>12,.2f}"
        f"{strategy.avg_holding_days:>9.1f}"
    )
    # `share`, not `win%`: for a level exit the win rate is a definition (a
    # take-profit leg fills above the entry, a stop below it), so printing it
    # would put a 100% next to the live ledger's real one.

    mix = level_mix(exits, nominal_payoff=tp_pct / stop_pct)
    print()
    if mix.hit_rate is None or mix.breakeven_hit_rate is None:
        print("No position reached a level — the bracket is unscored.")
        return
    print(
        f"Level mix: {mix.target_exits} targets / {mix.stop_exits} stops → "
        f"hit rate {mix.hit_rate:.1%}"
    )
    print(
        f"  payoff {mix.payoff_ratio:.2f}:1 realised vs {mix.nominal_payoff:.2f}:1 asked for"
        f"  (avg win ${mix.avg_win:,.0f} / avg loss ${mix.avg_loss:,.0f})"
    )
    edge = (mix.edge or 0.0) * 100.0
    print(
        f"  needs {mix.breakeven_hit_rate:.1%} to break even → "
        f"edge {edge:+.1f}pp ({'positive' if edge > 0 else 'negative'})"
    )

    short = sample_shortfall(rows, MIN_SAMPLE)
    thin = {c: n for c, n in short.items() if n > 0}
    print()
    if thin:
        missing = ", ".join(f"{c} needs {n} more" for c, n in sorted(thin.items()))
        print(f"Below the {MIN_SAMPLE}-trade floor — not yet a finding: {missing}")
    else:
        print(f"Every strategy exit path is at or above the {MIN_SAMPLE}-trade floor.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", default=DEFAULT_TICKERS, help="comma-separated")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument(
        "--stop-pct", type=float, default=5.0, help="stop distance below the fill, in percent"
    )
    parser.add_argument(
        "--take-profit-pct", type=float, default=10.0, help="target above the fill, in percent"
    )
    parser.add_argument("--fast", type=int, default=20, help="fast SMA for the entry signal")
    parser.add_argument("--slow", type=int, default=50, help="slow SMA for the entry signal")
    parser.add_argument(
        "--provider",
        choices=("yfinance", "survivor-safe"),
        default="yfinance",
        help=(
            "yfinance: today's names only, and a recycled ticker returns a "
            "stranger's prices under the old symbol. survivor-safe: Polygon "
            "bars restricted to the issuer that held the ticker at --start, "
            "with a coverage line per name."
        ),
    )
    args = parser.parse_args()

    stop_pct = args.stop_pct / 100.0
    tp_pct = args.take_profit_pct / 100.0
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    print(f"Loading {len(tickers)} tickers, {args.start} → {args.end}…", file=sys.stderr)
    if args.provider == "survivor-safe":
        from backtest.survivor_prices import load_universe

        bars_by_ticker, coverage = load_universe(
            tickers, date.fromisoformat(args.start), date.fromisoformat(args.end)
        )
        for row in coverage:
            print(f"  {row.summary()}", file=sys.stderr)
        unusable = [c.ticker for c in coverage if not c.usable]
        if unusable:
            # Printed as a share, because that share *is* the survivorship
            # measurement: it is how much of the point-in-time universe a
            # survivor-only run silently drops.
            print(
                f"  {len(unusable)}/{len(tickers)} tickers unpriceable "
                f"({len(unusable) / len(tickers):.0%}): {', '.join(unusable)}",
                file=sys.stderr,
            )
    else:
        from backtest.data import load_ohlcv

        frames = load_ohlcv(tickers, args.start, args.end)
        bars_by_ticker = {t: _bars_for(frames, t) for t in tickers}

    exits: list[SimulatedExit] = []
    for ticker in tickers:
        bars = bars_by_ticker.get(ticker) or []
        if not bars:
            print(f"  {ticker}: no bars, skipped", file=sys.stderr)
            continue
        signals = _signal_days(bars, args.fast, args.slow)
        replayed = replay_signals(ticker, signals, bars, stop_pct, tp_pct, quantity=100.0)
        print(
            f"  {ticker}: {len(bars)} bars, {len(signals)} signals, "
            f"{len(replayed)} positions",
            file=sys.stderr,
        )
        exits.extend(replayed)

    if not exits:
        print("No positions were opened — nothing to report.", file=sys.stderr)
        return 1

    _render(exits, stop_pct, tp_pct)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
