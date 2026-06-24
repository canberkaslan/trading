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

## Backlog drained — next round (eval-safe)
- [ ] Candlestick mode toggle on Charts (have OHLC already)
- [ ] eval_report: weekly auto-post to Slack/push at checkpoint
- [ ] prompt-cache markers actually injected (cache.py helpers exist; wire into client) — branch
- [ ] Agents tab: show reasoning expand/collapse + model badge per agent

## Deferred (go-live)
- HTTPS (cloudflared/caddy) — needs domain decision; low risk on paper.
- Merge `feat/cost-opt-adr006` + flip routing flag — AFTER eval window closes.

## Done (2026-06-24)
- accumulation fix, universe 3→11, clean book, timeout 150min
- 7a eval scorecard, 7b /analyze, 7c Ask tab + OTA
- 7e Charts, 7f Analiz-Et deep-link, 7g Settings kill-switch+health, snapshot logger
- cost-opt routing on branch (opt-in, not deployed)
