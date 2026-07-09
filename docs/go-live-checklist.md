# Go-Live Checklist — paper → real money

**Status: eval verdict = GO (2026-07-09, day 10/10).** Sharpe 9.94, MaxDD -1.04%,
+4.55% vs SPY +1.66%. This document is the manual path from the paper eval to a
funded live Alpaca account. **Nothing here is automated — every money-moving step
is the operator's to perform deliberately.**

> The autonomous trader currently trades a **paper** Alpaca account
> (`paper-api.alpaca.markets`, acct `PA348DFG9628`). Going live is a config +
> funding change, not a code change.

## 0. Decision gate (done)

| Gate | Threshold | Result |
|------|-----------|--------|
| Trading days | ≥ 10 | 10 ✅ |
| Sharpe (excess) | > 1.0 | 9.94 ✅ |
| Max drawdown | < 15% | 1.04% ✅ |
| Beats SPY | edge > 0 | +2.9pt ✅ |

The eval metrics are extraordinary because the window is short (10 trading days)
and the market was favorable — **do not extrapolate Sharpe 9.94 forward.** Treat
the GO as "the plumbing works and the strategy wasn't destructive," not as a
return forecast. Consider letting the paper eval run longer (30–60d, walk-forward
holdout note kicks in at 60d) before funding real capital.

## 1. Broker — open + fund a LIVE Alpaca account (operator only)

- [ ] Open a **live** Alpaca brokerage account (separate from paper), complete KYC.
- [ ] Fund it. **Start small** — size the initial deposit as risk capital you can
      lose. The strategy is unproven on real fills/slippage.
- [ ] Generate **live** API key + secret (live keys are distinct from paper keys).
- [ ] Note PDT rules: < $25k equity caps you at 3 day-trades / 5 days. The strategy
      is swing/position (1–18mo horizons) so this rarely bites, but `refuse_on_pdt`
      is on by default as a guard.

## 2. Box config — point the trader at live (operator only)

On `agentmesh:/opt/ai-trader/secrets.env`:

- [ ] `ALPACA_BASE_URL=https://api.alpaca.markets/v2`  ← the only endpoint switch
      (default is `paper-api.alpaca.markets`; see `dataflows/alpaca_broker.py`).
- [ ] `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` → **live** credentials.
- [ ] Keep secrets in `secrets.env` only (0600, never committed). Verify with
      `git check-ignore secrets.env`.

## 3. Execution safety — flip live-appropriate guards

- [ ] `daily_run.sh`: confirm `SUBMIT=1` (real orders). It already runs with
      `--submit`; a dry run is `SUBMIT=0`.
- [ ] Live runs should set `refuse_outside_hours=True` in `ExecutionConfig`
      (paper queues fine; live should not fire market orders into a closed book).
      Wire the `--refuse-outside-hours` flag into the live `daily_run.sh` invocation.
- [ ] Confirm bracket orders on (`use_bracket=True`) so every entry ships a
      broker-side stop — survives a box outage.
- [ ] Sanity-cap position size / notional per name for the first live weeks
      (concentration flag warns > 10% per name).

## 4. Pre-flight before first live run

- [ ] `ai-trader-preflight.timer` green (validates Alpaca + Polygon + FRED reachable).
- [ ] Kill-switch reachable from mobile Settings (stops the timer without SSH).
- [ ] `HEALTHCHECK_URL` dead-man ping wired to a healthchecks.io URL (env-gated,
      still pending) so a silent box death pages you.
- [ ] Off-box backup timer (`ai-trader-backup.timer`, 02:15 UTC) confirmed green.
- [ ] Alerting sane after the 2026-07-09 fixes: a Hold/no-trade day no longer
      false-fails the unit; only real broker/API errors page.

## 5. First live day — supervised

- [ ] Run the first live daily cycle **while watching** journalctl + Alpaca dashboard.
- [ ] Reconcile: every `ACCEPTED` order in the trade log has a matching Alpaca
      order; fills + stops attached as expected.
- [ ] Watch slippage vs the Polygon delayed price the sizer used.

## 6. Deferred / follow-ups (safe to do now that eval closed)

- [ ] Merge `feat/cost-opt-adr006` + flip the routing flag (ADR-006). Held until
      eval closed so decision quality stayed frozen — now unblocked. Re-run the
      test suite + a paper smoke before it touches live.
- [ ] HTTPS hardening for the API edge (cloudflared/caddy) — low risk on paper,
      do before exposing anything beyond the tunnel.
- [ ] Consider a longer paper re-run (30–60d) for a walk-forward-validated Sharpe
      before scaling capital up.

---

**Reminder:** Claude will not, and cannot, move real money. Steps 1–2 (open + fund
+ live keys) are yours alone. Everything the autonomous system does stays paper
until `ALPACA_BASE_URL` is pointed at the live endpoint with live keys present.
