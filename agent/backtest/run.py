#!/usr/bin/env python3
"""Deterministic backtest runner (Methods 1 + 2).

Runs each non-LLM baseline through vectorbt WITH the risk layer applied
(stop-loss + take-profit + costs), across market regimes, vs SPY buy & hold.
Validates the harness + risk mechanics and shows what "no LLM edge" looks like.

    python -m backtest.run
    python -m backtest.run --start 2023-01-01 --end 2025-12-31

Survivorship caveat: fixed current universe -> optimistic. Mechanics > edge.
"""

from __future__ import annotations

import argparse

import pandas as pd
import vectorbt as vbt

from backtest.data import REGIMES, load_ohlcv
from backtest.strategies import STRATEGIES

UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "JPM", "V", "XOM", "UNH"]
BENCHMARK = "SPY"

# Risk layer in backtest form.
SL_STOP = 0.08   # 8% stop-loss
TP_STOP = 0.20   # 20% take-profit
FEES = 0.0005    # 5 bps commission-equivalent
SLIPPAGE = 0.0005
INIT_CASH = 100_000.0


def _metrics(pf: "vbt.Portfolio") -> dict:
    try:
        sharpe = float(pf.sharpe_ratio())
    except Exception:
        sharpe = float("nan")
    return {
        "sharpe": sharpe,
        "max_dd": float(pf.max_drawdown()) * 100.0,
        "total_return": float(pf.total_return()) * 100.0,
    }


def _run_strategy(close, entries, exits) -> dict:
    pf = vbt.Portfolio.from_signals(
        close,
        entries,
        exits,
        sl_stop=SL_STOP,
        tp_stop=TP_STOP,
        fees=FEES,
        slippage=SLIPPAGE,
        init_cash=INIT_CASH,
        group_by=True,
        cash_sharing=True,
        freq="1D",
    )
    return _metrics(pf)


def _spy_buyhold(spy_close) -> dict:
    pf = vbt.Portfolio.from_holding(spy_close, init_cash=INIT_CASH, fees=FEES, freq="1D")
    return _metrics(pf)


def run(start: str, end: str) -> None:
    print(f"Loading {len(UNIVERSE)} names + {BENCHMARK}  {start}..{end} (yfinance)…")
    fields = load_ohlcv(UNIVERSE + [BENCHMARK], start, end)
    close_all = fields["close"]

    regimes = {k: v for k, v in REGIMES.items() if v[0] >= start or k.startswith("full")}
    regimes["custom"] = (start, end)

    for rname, (rs, re) in regimes.items():
        sub = close_all.loc[rs:re]
        if len(sub) < 60:
            continue
        uni = sub[[t for t in UNIVERSE if t in sub.columns]].dropna(how="all")
        spy = sub[BENCHMARK].dropna()

        print(f"\n{'='*64}\n  REGIME: {rname}  ({sub.index.min().date()} → {sub.index.max().date()}, {len(sub)} gün)\n{'='*64}")
        print(f"  {'strateji':<22}{'Sharpe':>9}{'MaxDD%':>10}{'Getiri%':>11}")
        print("  " + "-" * 50)

        spy_m = _spy_buyhold(spy)
        for sname, fn in STRATEGIES.items():
            entries, exits = fn(uni)
            m = _run_strategy(uni, entries, exits)
            print(f"  {sname:<22}{m['sharpe']:>9.2f}{m['max_dd']:>10.1f}{m['total_return']:>11.1f}")
        print("  " + "-" * 50)
        print(f"  {'SPY buy&hold':<22}{spy_m['sharpe']:>9.2f}{spy_m['max_dd']:>10.1f}{spy_m['total_return']:>11.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic backtest (Methods 1+2)")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()
    run(args.start, args.end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
