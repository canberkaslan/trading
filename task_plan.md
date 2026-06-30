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

## Round 3 (next, eval-safe)
- [ ] prompt-cache markers injected into the LLM client (cache.py helpers exist) — BRANCH; needs a live API run to confirm cache hits (costs ~$1)
- [x] Portfolio: surface snapshot concentration / position-count trend — /v1/portfolio/concentration deployed 2026-06-29 (HHI, effective_n, top/top3 weight, flags, snapshot trend; read-only, off-eval-path)
- [x] Pull-to-refresh + empty states polish on Charts/Ask — Charts View→ScrollView+RefreshControl(refetch), Ask empty-state card + ticker chips; OTA preview 2026-06-30 ✅
- [ ] eval_report: walk-forward holdout note when days>=60

## Deferred (go-live)
- HTTPS (cloudflared/caddy) — needs domain decision; low risk on paper.
- Merge `feat/cost-opt-adr006` + flip routing flag — AFTER eval window closes.

## Done (2026-06-24)
- accumulation fix, universe 3→11, clean book, timeout 150min
- 7a eval scorecard, 7b /analyze, 7c Ask tab + OTA
- 7e Charts, 7f Analiz-Et deep-link, 7g Settings kill-switch+health, snapshot logger
- cost-opt routing on branch (opt-in, not deployed)
