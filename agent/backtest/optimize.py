#!/usr/bin/env python3
"""Walk-forward risk-param optimization — deterministic, no LLM, no leakage.

Splits history into TRAIN (in-sample) and TEST (out-of-sample, untouched until
the end). Grid-searches risk-layer params (stop-loss, take-profit) per baseline
strategy on TRAIN, picks the best by Sharpe, then reports that config's TEST
Sharpe. Train→test degradation is the overfitting tell. A deflated-Sharpe
haircut accounts for the number of trials (Bailey & López de Prado).

    python -m backtest.optimize

The risk layer is signal-agnostic, so robust stop/TP settings found here apply
to any strategy — including the LLM agent. Goal: robustness, not magic alpha.
"""

from __future__ import annotations

import math

import numpy as np
import vectorbt as vbt

from backtest.data import load_ohlcv
from backtest.strategies import STRATEGIES

UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "JPM", "V", "XOM", "UNH"]

SL_GRID = [0.05, 0.08, 0.12]          # stop-loss
TP_GRID = [0.15, 0.25, None]          # take-profit (None = none)
FEES, SLIPPAGE, INIT = 0.0005, 0.0005, 100_000.0

TRAIN = ("2018-01-01", "2021-12-31")
TEST = ("2022-01-01", "2025-12-31")   # out-of-sample, includes a bear + a bull


def _sharpe(close, entries, exits, sl, tp) -> float:
    kw = dict(sl_stop=sl, fees=FEES, slippage=SLIPPAGE, init_cash=INIT,
              group_by=True, cash_sharing=True, freq="1D")
    if tp is not None:
        kw["tp_stop"] = tp
    pf = vbt.Portfolio.from_signals(close, entries, exits, **kw)
    try:
        s = float(pf.sharpe_ratio())
        return s if math.isfinite(s) else float("nan")
    except Exception:
        return float("nan")


def run() -> None:
    print("Loading universe 2018-2025 (yfinance)…")
    close = load_ohlcv(UNIVERSE, "2018-01-01", "2025-12-31")["close"]
    tr = close.loc[TRAIN[0]:TRAIN[1]]
    te = close.loc[TEST[0]:TEST[1]]
    n_trials = len(SL_GRID) * len(TP_GRID)
    print(f"TRAIN {TRAIN[0]}..{TRAIN[1]} ({len(tr)}g)  TEST {TEST[0]}..{TEST[1]} ({len(te)}g)  grid={n_trials}/strateji\n")

    for sname, fn in STRATEGIES.items():
        tr_e, tr_x = fn(tr)
        te_e, te_x = fn(te)
        results = []
        for sl in SL_GRID:
            for tp in TP_GRID:
                results.append((sl, tp, _sharpe(tr, tr_e, tr_x, sl, tp)))
        valid = [r for r in results if math.isfinite(r[2])]
        if not valid:
            print(f"{sname}: tüm kombolar nan — atlandı"); continue
        best = max(valid, key=lambda r: r[2])
        sl, tp, tr_sh = best
        te_sh = _sharpe(te, te_e, te_x, sl, tp)
        # deflated-Sharpe haircut estimate (expected max of N noise trials)
        haircut = math.sqrt(2 * math.log(n_trials))  # in "z" units
        deflated_tr = tr_sh - haircut * (1.0 / math.sqrt(len(tr)))
        tag = "OVERFIT?" if (math.isfinite(te_sh) and te_sh < tr_sh - 0.5) else "robust"
        print(f"=== {sname} ===")
        print(f"  en iyi TRAIN param : SL {sl*100:.0f}%  TP {('%d%%'%(tp*100)) if tp else 'yok'}")
        print(f"  TRAIN Sharpe       : {tr_sh:.2f}  (deflated ~{deflated_tr:.2f})")
        print(f"  TEST  Sharpe (OOS) : {te_sh:.2f}   -> {tag}")
        print()


if __name__ == "__main__":
    run()
