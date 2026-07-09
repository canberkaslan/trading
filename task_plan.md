# Autopilot backlog — Phase 7+ (eval-safe)

Survives context resets. Rule: never deploy decision-quality changes to the
eval timer until the eval window closes. Commit as Canberk, no AI attribution.
Each item: code → tests green → commit → deploy (OTA if mobile).

## Backlog
- [x] **1. Charts tab** — backend `/v1/prices/{ticker}` (Polygon daily bars) + mobile area/line chart (react-native-svg). OTA. ✅
- [x] **2. "Analiz Et" quick action** — portfolio positions + Charts button → deep-link Ask, auto-run. OTA. ✅
- [x] **3. Settings** — kill-switch 3-state control + backend health + last-run. OTA. ✅
- [x] **4. Eval snapshot logger** — daily equity+positions JSONL on the box (EVAL_SNAPSHOT_FILE), wired into daily_run; eval_report shows avg-positions + max-concentration. ✅
- [x] **5. Cost-opt (ADR-006)** — per-agent routing wired on branch `feat/cost-opt-adr006` (GraphSetup._pick + agent_model_map), opt-in `TRADINGAGENTS_PER_AGENT_ROUTING=1`, 93 tests green, NOT merged/deployed. ✅ branch-only

## Round 2 (eval-safe) — done
- [x] Candlestick mode toggle on Charts ✅
- [x] eval --notify push + weekly eval-report.timer (Mon 14:00 UTC) ✅
- [x] Agents tab reasoning expand + model badges ✅
- [x] /v1/eval endpoint + in-app scorecard card (Settings) ✅

## 🎯 EVAL GATE — GO (2026-07-09)
- /v1/eval verdict=**GO** at day 10/10 (days_remaining=0). Sharpe **9.94**, MaxDD **-1.04%**, total return **+4.55%** vs SPY +1.66% (α +2.9pt). All 4 hard gates passed. Paper account PA348DFG9628 equity **$103,643**.
- **DO NOT move real money automatically** — go-live requires the user to open + fund a live Alpaca account, set ALPACA_BASE_URL=live + live keys on the box, and flip submit/routing. See `docs/go-live-checklist.md` (pending) + Deferred (go-live) below.

## Round 3 (next, eval-safe)
- [x] daily-run false-failure fix — a legitimate policy refusal (non-actionable Hold, risk guard, PDT, market closed) made `trade.py` exit rc=1 under `--submit`, so all-Hold days marked `ai-trader.service` failed + fired OnFailure push (alert fatigue, masks real broker errors). Added `ExecutionResult.error` (True only for broker/unexpected exceptions); trade.py exits non-zero only on `error`. 24 execution tests green, off-eval-path (no decision/order change), deployed 2026-07-09 ✅
- [x] eval-report false-failure fix — same anti-pattern: weekly `eval_report.py` exited rc=1 on any non-GO verdict, so during the whole open eval window (WAIT/TOO EARLY) the systemd unit showed `failed`. Verdict is data (printed + pushed via `--notify`), not job health; a real data/compute failure already raises. Now exits 0 whenever the scorecard builds; `--strict` restores the GO-only gate. 23 eval_report tests green, off-eval-path, deployed 2026-07-09 ✅
- [ ] prompt-cache markers injected into the LLM client (cache.py helpers exist) — BRANCH; needs a live API run to confirm cache hits (costs ~$1)
- [x] /v1/eval: provisional verdict ("eğilim") while TOO EARLY — projects GO/NO-GO from current Sharpe/MaxDD hard-gates ignoring the min-days req (SPY lag is a flag, not a gate); read-only reporting, off-eval-path; mobile scorecard shows trend badge next to verdict; 26 eval tests green, deployed 2026-07-07 ✅
- [x] /v1/eval: benchmark on by default so mobile scorecard + weekly push show the "Beats SPY" gate without a query param (read-only, off-eval-path); currently +3.3% vs SPY +1.6% at day 7 ✅ 2026-07-03
- [x] Portfolio: surface snapshot concentration / position-count trend — /v1/portfolio/concentration deployed 2026-06-29 (HHI, effective_n, top/top3 weight, flags, snapshot trend; read-only, off-eval-path)
- [x] Pull-to-refresh + empty states polish on Charts/Ask — Charts View→ScrollView+RefreshControl(refetch), Ask empty-state card + ticker chips; OTA preview 2026-06-30 ✅
- [x] eval_report: walk-forward holdout note when days>=60 — IS(2/3)->OOS(1/3) Sharpe split, regime-fit WARN flag; read-only, off-eval-path; 13 eval_report tests green 2026-07-01
- [x] snapshot: populate `sector` from static GICS map (sector_map.py) — display-only, off trading path; deployed + verified live 2026-07-04 ✅
- [x] E2E review critical fixes (docs/review-e2e-2026-07-04.md top-now #1/#3/#5/#7) — daily-run alerting (exit-1 + push + OnFailure unit + per-ticker timeout 1800s -k 30 + TimeoutStartSec 21600), off-box backup (private repo canberkaslan/trading-backups, deploy key, 02:15 UTC timer, sqlite online backup + snapshots + memory), preflight canary (21:45 UTC, alert-only), honest eval metrics (Sharpe/Sortino excess over FRED DGS3MO, correct Sortino downside dev, SPY total-return benchmark); adversarially reviewed (20 findings fixed pre-deploy), deployed + verified live 2026-07-04 ✅ — HEALTHCHECK_URL dead-man ping env-gated, needs healthchecks.io URL

- [x] scorecard: α vs SPY stat (portfolio return − SPY total return) as 4th grid card — makes the "Beats SPY" gate legible as a number ahead of GO/NO-GO; read-only, off-eval-path; OTA preview 2026-07-08 ✅

## Deferred (go-live)
- HTTPS (cloudflared/caddy) — needs domain decision; low risk on paper.
- Merge `feat/cost-opt-adr006` + flip routing flag — AFTER eval window closes.

## Done (2026-06-24)
- accumulation fix, universe 3→11, clean book, timeout 150min
- 7a eval scorecard, 7b /analyze, 7c Ask tab + OTA
- 7e Charts, 7f Analiz-Et deep-link, 7g Settings kill-switch+health, snapshot logger
- cost-opt routing on branch (opt-in, not deployed)
