#!/usr/bin/env python3
"""Paper-trading evaluation scorecard.

Pulls the live Alpaca paper account equity curve and prints a go/no-go
scorecard against the ADR-005 paper->live gates. Run this at the eval
checkpoint to decide whether to risk real capital.

    python scripts/eval_report.py                  # default 1M window
    python scripts/eval_report.py --period 3M
    python scripts/eval_report.py --period 1M --no-benchmark

Gates (ADR-005, user-shortened eval): Sharpe > 1.0, MaxDD < 15%. Sharpe is
EXCESS over the 3-month T-bill (FRED DGS3MO; EVAL_RISK_FREE_RATE fallback) —
grading against a 0% risk-free while cash yields 4-5% would inflate the
metric and bias the GO/NO-GO toward GO. The rate and source used are shown
on the report (rf_annual/rf_source).
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
    rf_annual: float = 0.0  # annualized risk-free rate used for Sharpe/Sortino
    rf_source: str = "none"  # "fred:DGS3MO" | "env" | "none"


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


def _walk_forward_sharpe(equity: pd.Series, rf: float = 0.0) -> tuple[float, float] | None:
    """Split the equity curve into a 2/3 in-sample head and a 1/3 out-of-sample
    holdout tail, returning (is_sharpe, oos_sharpe). None if there aren't enough
    days for the split to be meaningful. Read-only diagnostic — a strategy that
    only worked in-sample shows up as a Sharpe that collapses on the holdout.
    Uses the same risk-free basis as the headline Sharpe so all three figures
    are comparable."""
    if len(equity) < HOLDOUT_MIN_DAYS:
        return None
    split = (len(equity) * 2) // 3
    in_sample = equity.iloc[:split]
    holdout = equity.iloc[split - 1:]  # overlap one bar so returns are continuous
    is_ret = in_sample.pct_change().dropna()
    oos_ret = holdout.pct_change().dropna()
    if len(is_ret) < 2 or len(oos_ret) < 2:
        return None
    return (
        metrics.sharpe(is_ret, risk_free_rate=rf),
        metrics.sharpe(oos_ret, risk_free_rate=rf),
    )


def _spy_return(start: datetime, end: datetime) -> float | None:
    """Best-effort SPY TOTAL return (price + cash dividends) over the window.

    The account equity curve is dividend-inclusive (cash dividends land in
    the account), so benchmarking it against SPY price return alone flatters
    the strategy by SPY's yield (~1.2%/yr). Dividends are best-effort — if
    the reference call fails we still return the price return rather than
    dropping the benchmark."""
    try:
        from tradingagents_us.dataflows.polygon import PolygonClient

        with PolygonClient() as pc:
            bars = pc.aggregates("SPY", start.date(), end.date(), timespan="day")
            closes = [b.close for b in bars if b.close]
            if len(closes) < 2:
                return None
            divs = 0.0
            try:
                divs = sum(
                    float(d.get("cash_amount") or 0.0)
                    for d in pc.dividends("SPY", start.date(), end.date())
                )
            except Exception as exc:
                print(f"  (SPY dividends unavailable: {exc})", file=sys.stderr)
        return (closes[-1] + divs) / closes[0] - 1.0
    except Exception as exc:  # benchmark is optional — never fail the report
        print(f"  (SPY benchmark unavailable: {exc})", file=sys.stderr)
        return None


def _risk_free_rate() -> tuple[float, str]:
    """Annualized risk-free rate for excess-return metrics, with source tag.

    Order: FRED 3-month T-bill (DGS3MO, latest print) -> EVAL_RISK_FREE_RATE
    env (decimal, e.g. 0.043) -> 0.0. Grading Sharpe > 1.0 against a 0%%
    risk-free while cash yields 4-5%% inflates the metric — this keeps the
    GO/NO-GO gate honest. Best-effort: any failure falls through."""
    try:
        from datetime import timedelta

        from tradingagents_us.dataflows.fred import FREDClient

        today = datetime.now(timezone.utc).date()
        with FREDClient() as fc:
            obs = fc.series("DGS3MO", start=today - timedelta(days=14), end=today)
        vals = [o.value for o in obs if o.value is not None]
        if vals:
            return vals[-1] / 100.0, "fred:DGS3MO"
    except Exception as exc:
        print(f"  (FRED risk-free unavailable: {exc})", file=sys.stderr)
    env = os.environ.get("EVAL_RISK_FREE_RATE")
    if env:
        try:
            return float(env), "env"
        except ValueError:
            pass
    return 0.0, "none"


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
    rf, rf_source = _risk_free_rate()
    wf = _walk_forward_sharpe(equity, rf=rf)

    return Scorecard(
        days=len(equity),
        start_equity=float(equity.iloc[0]),
        end_equity=float(equity.iloc[-1]),
        total_return=float(equity.iloc[-1] / equity.iloc[0] - 1.0),
        sharpe=metrics.sharpe(returns, risk_free_rate=rf),
        sortino=metrics.sortino(returns, risk_free_rate=rf),
        max_dd=max_dd,
        dd_duration=dd_dur,
        calmar=metrics.calmar(returns, equity),
        var95=var95,
        cvar95=cvar95,
        positive_days_pct=float((returns > 0).mean() * 100.0),
        spy_return=spy,
        is_sharpe=wf[0] if wf else None,
        oos_sharpe=wf[1] if wf else None,
        rf_annual=rf,
        rf_source=rf_source,
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


def _provisional_verdict(sc: Scorecard) -> str | None:
    """Trend verdict ("eğilim") while the eval window is still open.

    Read-only reporting only. When there aren't yet enough trading days the
    real verdict is locked to "TOO EARLY"; this projects what the GO/NO-GO
    *would* be if the current hard-gate metrics (Sharpe, MaxDD) held to day
    ``MIN_TRADING_DAYS``. Returns None once enough days exist (the real
    verdict is then authoritative) or before the metrics are meaningful.
    Beating SPY is a flag, not a hard gate, so it does not affect this.
    """
    if sc.days >= MIN_TRADING_DAYS:
        return None
    if sc.days < 2:
        return None  # Sharpe/MaxDD need at least a couple of points to mean anything
    passed = sc.sharpe > GATE_SHARPE and abs(sc.max_dd) < GATE_MAX_DD
    return "GO" if passed else "NO-GO"


def build_gates(sc: Scorecard) -> list[dict]:
    """Structured GO/NO-GO gate breakdown for the mobile scorecard.

    Read-only reporting: mirrors the pass/fail logic in _verdict but as a
    per-gate checklist the UI can render. `passed=None` means "not yet
    evaluable" (e.g. SPY edge without a benchmark, or metrics while still
    below the min-days threshold)."""
    have_days = sc.days >= MIN_TRADING_DAYS
    gates: list[dict] = [
        {
            "name": "Trading days",
            "passed": have_days,
            "detail": f"{sc.days}/{MIN_TRADING_DAYS}",
        },
        {
            "name": "Sharpe",
            "passed": (sc.sharpe > GATE_SHARPE) if have_days else None,
            "detail": f"{sc.sharpe:.2f} > {GATE_SHARPE}",
        },
        {
            "name": "Max drawdown",
            "passed": (abs(sc.max_dd) < GATE_MAX_DD) if have_days else None,
            "detail": f"{abs(sc.max_dd) * 100:.1f}% < {GATE_MAX_DD * 100:.0f}%",
        },
    ]
    if sc.spy_return is not None:
        gates.append(
            {
                "name": "Beats SPY",
                "passed": (sc.total_return >= sc.spy_return) if have_days else None,
                "detail": f"{sc.total_return * 100:+.1f}% vs {sc.spy_return * 100:+.1f}%",
            }
        )
    else:
        gates.append({"name": "Beats SPY", "passed": None, "detail": "benchmark off"})
    return gates


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
    row("Risk-free (ann.)", f"{sc.rf_annual * 100:.2f}%", f"src {sc.rf_source}")
    row("Sharpe (excess)", f"{sc.sharpe:.2f}", f"GATE > {GATE_SHARPE}")
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
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero unless verdict is GO (default: exit 0 whenever "
                         "the scorecard is produced — verdict is data, not job health)")
    args = ap.parse_args()

    sc = build_scorecard(period=args.period, benchmark=not args.no_benchmark)
    _print(sc)
    verdict, _ = _verdict(sc)
    if args.notify:
        _notify(sc, verdict)
    # The scorecard built successfully — that is job success. The verdict
    # (GO / NO-GO / TOO EARLY) is delivered via print + push, not the exit
    # code; a WAIT/NO-GO is legitimate information, not a unit failure. Only
    # a real data/compute failure exits non-zero (build_scorecard raises).
    # --strict restores the old GO-only gate for callers that want it.
    if args.strict:
        return 0 if verdict == "GO" else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
