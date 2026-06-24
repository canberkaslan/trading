# Autopilot backlog — Phase 7+ (eval-safe)

Survives context resets. Rule: never deploy decision-quality changes to the
eval timer until the eval window closes. Commit as Canberk, no AI attribution.
Each item: code → tests green → commit → deploy (OTA if mobile).

## Backlog
- [x] **1. Charts tab** — backend `/v1/prices/{ticker}` (Polygon daily bars) + mobile area/line chart (react-native-svg). OTA. ✅
- [x] **2. "Analiz Et" quick action** — portfolio positions + Charts button → deep-link Ask, auto-run. OTA. ✅
- [x] **3. Settings** — kill-switch 3-state control + backend health + last-run. OTA. ✅
- [ ] **4. Eval snapshot logger** — daily equity+positions JSONL on the box; eval_report reads it for richer history.
- [ ] **5. Cost-opt (ADR-006)** — per-agent Haiku routing + prompt caching, on a SEPARATE branch, build+test, DO NOT deploy to eval timer.

## Deferred (go-live)
- HTTPS (cloudflared/caddy) — needs domain decision; low risk on paper.

## Done (2026-06-24)
- accumulation fix, universe 3→11, clean book, timeout 150min
- 7a eval scorecard, 7b /analyze, 7c Ask tab + OTA
