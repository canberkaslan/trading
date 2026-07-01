#!/usr/bin/env python3
"""Paper-trading evaluation scorecard.

Pulls the live Alpaca paper account equity curve and prints a go/no-go
scorecard against the ADR-005 paper->live gates. Run this at the eval
checkpoint to decide whether to risk real capital.

    python scripts/eval_report.py                  # default 1M window
    python scripts/eval_report.py --period 3M
    python scripts/eval_report.py --period 1M --no-benchmark

Gates (ADR-005, user-shortened eval): Sharpe(net) > 1.0, MaxDD < 15%.
The script is read-only — it never places or cancels an order.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.risk import metrics

# ADR-005 gates (eval phase). Sharpe is the primary signal; MaxDD bounds risk.
GATE_SHARPE = 1.0
GATE_MAX_DD = 0.15  # 15%, as a positive magnitude
MIN_TRADING_DAYS = 10  # below this the metrics are too noisy to trust
HOLDOUT_MIN_DAYS = 60  # below this a walk-forward holdout split is meaningless
# Out-of-sample Sharpe below IS Sharpe by more than this fraction = regime-fit warning.
HOLDOUT_DEGRADE_FRAC = 0.5


@dataclass
class Scorecard:
    days: int
    start_equity: float
    end_equity: float
    total_return: float
    sharpe: float
    sortino: float
    max_dd: float  # negative
    dd_duration: int
    calmar: float
    var95: float
    cvar95: float
    positive_days_pct: float
    spy_return: float | None
    is_sharpe: float | None = None  # in-sample (first 2/3) Sharpe
    oos_sharpe: float | None = None  # out-of-sample holdout (last 1/3) Sharpe


def _equity_series(history: dict) -> pd.Series:
    """Build a clean daily equity Series from Alpaca portfolio history.

    Drops None/zero points and the leading flat segment before the first
    real position (clean-book days where equity never moved)."""
    ts = history.get("timestamp", [])
    eq = history.get("equity", [])
    pairs = [
        (datetime.fromtimestamp(t, tz=timezone.utc), float(e))
        for t, e in zip(ts, eq)
        if e is not None and float(e) > 0
    ]
    if not pairs:
        return pd.Series(dtype=float)
    s = pd.Series({d: v for d, v in pairs}).sort_index()
    # Trim leading days where equity is perfectly flat (no trading yet).
    moved = s.ne(s.shift())
    moved.iloc[0] = True
    first_move = moved[s.diff().fillna(0).ne(0)].index.min()
    if first_move is not None and not pd.isna(first_move):
        # keep the bar just before the first move as the baseline
        idx = list(s.index)
        start = max(0, idx.index(first_move) - 1)
        s = s.iloc[start:]
    return s


def _walk_forward_sharpe(equity: pd.Series) -> tuple[float, float] | None:
    """Split the equity curve into a 2/3 in-sample head and a 1/3 out-of-sample
    holdout tail, returning (is_sharpe, oos_sharpe). None if there aren't enough
    days for the split to be meaningful. Read-only diagnostic — a strategy that
    only worked in-sample shows up as a Sharpe that collapses on the holdout."""
    if len(equity) < HOLDOUT_MIN_DAYS:
        return None
    split = (len(equity) * 2) // 3
    in_sample = equity.iloc[:split]
    holdout = equity.iloc[split - 1:]  # overlap one bar so returns are continuous
    is_ret = in_sample.pct_change().dropna()
    oos_ret = holdout.pct_change().dropna()
    if len(is_ret) < 2 or len(oos_ret) < 2:
        return None
    return metrics.sharpe(is_ret), metrics.sharpe(oos_ret)


def _spy_return(start: datetime, end: datetime) -> float | None:
    """Best-effort SPY total return over the window for benchmark."""
    try:
        from tradingagents_us.dataflows.polygon import PolygonClient

        with PolygonClient() as pc:
            bars = pc.aggregates("SPY", start.date(), end.date(), timespan="day")
        closes = [b.close for b in bars if b.close]
        if len(closes) < 2:
            return None
        return closes[-1] / closes[0] - 1.0
    except Exception as exc:  # benchmark is optional — never fail the report
        print(f"  (SPY benchmark unavailable: {exc})", file=sys.stderr)
        return None


def build_scorecard(period: str, benchmark: bool) -> Scorecard:
    with AlpacaClient() as ac:
        history = ac.portfolio_history(period=period, timeframe="1D")

    equity = _equity_series(history)

    # Eval start cutoff: ignore everything before the clean diversified book
    # began (set EVAL_START_DATE=YYYY-MM-DD), so the earlier bug-era history
    # can't pollute Sharpe / drawdown / return.
    start = os.environ.get("EVAL_START_DATE")
    if start:
        equity = equity[equity.index >= pd.Timestamp(start, tz="UTC")]

    if len(equity) < 2:
        raise SystemExit(
            "Not enough equity history yet — the clean eval book likely just "
            "started. Re-run after a few trading days."
        )

    returns = equity.pct_change().dropna()
    max_dd, dd_dur = metrics.max_drawdown(equity)
    var95, cvar95 = metrics.var_cvar(returns)
    spy = (
        _spy_return(equity.index[0], equity.index[-1]) if benchmark else None
    )
    wf = _walk_forward_sharpe(equity)

    return Scorecard(
        days=len(equity),
        start_equity=float(equity.iloc[0]),
        end_equity=float(equity.iloc[-1]),
        total_return=float(equity.iloc[-1] / equity.iloc[0] - 1.0),
        sharpe=metrics.sharpe(returns),
        sortino=metrics.sortino(returns),
        max_dd=max_dd,
        dd_duration=dd_dur,
        calmar=metrics.calmar(returns, equity),
        var95=var95,
        cvar95=cvar95,
        positive_days_pct=float((returns > 0).mean() * 100.0),
        spy_return=spy,
        is_sharpe=wf[0] if wf else None,
        oos_sharpe=wf[1] if wf else None,
    )


def _snapshot_summary() -> tuple[float, float, int] | None:
    """Avg position count, max concentration, sample count from the snapshot
    JSONL (written by scripts/snapshot.py). None if no log exists yet."""
    path = Path(os.environ.get("EVAL_SNAPSHOT_FILE", "./data/eval_snapshots.jsonl"))
    if not path.exists():
        return None
    counts: list[int] = []
    tops: list[float] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        counts.append(int(rec.get("n_positions", 0)))
        tops.append(float(rec.get("top_weight_pct", 0.0)))
    if not counts:
        return None
    return sum(counts) / len(counts), max(tops), len(counts)


def _holdout_note(sc: Scorecard) -> str | None:
    """Human-readable walk-forward holdout line, or None if not enough days.
    Warns when out-of-sample Sharpe collapses vs in-sample (regime-fit risk)."""
    if sc.is_sharpe is None or sc.oos_sharpe is None:
        return None
    degraded = (
        sc.is_sharpe > 0
        and sc.oos_sharpe < sc.is_sharpe * (1.0 - HOLDOUT_DEGRADE_FRAC)
    )
    flag = "  [WARN regime-fit]" if degraded else "  [stable]"
    return (
        f"Walk-forward: IS Sharpe {sc.is_sharpe:.2f} -> "
        f"OOS Sharpe {sc.oos_sharpe:.2f}{flag}"
    )


def _verdict(sc: Scorecard) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if sc.days < MIN_TRADING_DAYS:
        return "TOO EARLY", [
            f"only {sc.days} trading days (need >= {MIN_TRADING_DAYS} for stable metrics)"
        ]
    passed = True
    if sc.sharpe <= GATE_SHARPE:
        passed = False
        reasons.append(f"Sharpe {sc.sharpe:.2f} <= {GATE_SHARPE}")
    if abs(sc.max_dd) >= GATE_MAX_DD:
        passed = False
        reasons.append(f"MaxDD {abs(sc.max_dd) * 100:.1f}% >= {GATE_MAX_DD * 100:.0f}%")
    if sc.spy_return is not None and sc.total_return < sc.spy_return:
        # not a hard gate, but a flag — beating SPY is the whole point
        reasons.append(
            f"underperformed SPY ({sc.total_return * 100:+.1f}% vs {sc.spy_return * 100:+.1f}%)"
        )
    return ("GO" if passed else "NO-GO"), reasons


def _print(sc: Scorecard) -> None:
    def row(label: str, value: str, gate: str = "") -> None:
        print(f"  {label:<22} {value:>14}   {gate}")

    print("\n" + "=" * 56)
    print("  PAPER EVAL SCORECARD")
    print("=" * 56)
    row("Trading days", str(sc.days), f"need >= {MIN_TRADING_DAYS}")
    row("Equity", f"${sc.start_equity:,.0f} -> ${sc.end_equity:,.0f}")
    row("Total return", f"{sc.total_return * 100:+.2f}%")
    if sc.spy_return is not None:
        edge = (sc.total_return - sc.spy_return) * 100
        row("SPY (same window)", f"{sc.spy_return * 100:+.2f}%", f"edge {edge:+.2f}pp")
    print("  " + "-" * 52)
    row("Sharpe (net)", f"{sc.sharpe:.2f}", f"GATE > {GATE_SHARPE}")
    row("Sortino", f"{sc.sortino:.2f}", "> 1.2 nice")
    row("Max drawdown", f"{sc.max_dd * 100:.2f}%", f"GATE < {GATE_MAX_DD * 100:.0f}%")
    row("DD duration", f"{sc.dd_duration} bars")
    row("Calmar", f"{sc.calmar:.2f}", "> 0.5 nice")
    row("VaR 95% (daily)", f"{sc.var95 * 100:.2f}%")
    row("CVaR 95% (daily)", f"{sc.cvar95 * 100:.2f}%")
    row("Positive days", f"{sc.positive_days_pct:.0f}%")
    snap = _snapshot_summary()
    if snap is not None:
        avg_pos, max_conc, n = snap
        row("Avg positions", f"{avg_pos:.1f}", f"{n} snapshots")
        row("Max concentration", f"{max_conc:.0f}%", "watch >10% per name")
    note = _holdout_note(sc)
    if note is not None:
        print("  " + "-" * 52)
        print(f"  {note}")
    print("  " + "-" * 52)

    verdict, reasons = _verdict(sc)
    mark = {"GO": "[GO]", "NO-GO": "[NO-GO]", "TOO EARLY": "[WAIT]"}[verdict]
    print(f"  VERDICT: {mark} {verdict}")
    for r in reasons:
        print(f"           - {r}")
    if verdict == "GO":
        print("           swap paper key -> funded LIVE key, start $500-1000, low caps")
    print("=" * 56 + "\n")


def _notify(sc: Scorecard, verdict: str) -> None:
    """Push the scorecard verdict to every registered device (best-effort)."""
    try:
        from sqlalchemy import create_engine

        from tradingagents_us.notifications import send_expo_push
        from tradingagents_us.notifications.sender import PushMessage
        from tradingagents_us.storage import TradeLogRepository
        from tradingagents_us.storage.device_tokens import list_all_tokens

        url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
        repo = TradeLogRepository(engine=create_engine(url, future=True))
        with repo.session() as s:
            tokens = list_all_tokens(s)
        if not tokens:
            print("notify: no registered devices", file=sys.stderr)
            return
        mark = {"GO": "✅", "NO-GO": "⛔", "TOO EARLY": "⏳"}.get(verdict, "")
        body = (
            f"Sharpe {sc.sharpe:.2f} · DD {sc.max_dd * 100:.1f}% · "
            f"{sc.total_return * 100:+.1f}% ({sc.days}g)"
        )
        send_expo_push([
            PushMessage(
                to=t,
                title=f"{mark} Eval: {verdict}",
                body=body,
                data={"type": "eval_scorecard", "verdict": verdict},
            )
            for t in tokens
        ])
        print(f"notify: sent to {len(tokens)} device(s)")
    except Exception as exc:  # never fail the report on a push error
        print(f"notify failed: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Paper-trading eval scorecard")
    ap.add_argument("--period", default="1M", help="Alpaca history window (1M/3M/6M/1A)")
    ap.add_argument("--no-benchmark", action="store_true", help="skip SPY benchmark")
    ap.add_argument("--notify", action="store_true", help="push the verdict to registered devices")
    args = ap.parse_args()

    sc = build_scorecard(period=args.period, benchmark=not args.no_benchmark)
    _print(sc)
    verdict, _ = _verdict(sc)
    if args.notify:
        _notify(sc, verdict)
    return 0 if verdict == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
