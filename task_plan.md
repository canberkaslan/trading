# Autopilot backlog — Phase 7+ (eval-safe)

Survives context resets. Rule: never deploy decision-quality changes to the
eval timer until the eval window closes. Commit as Canberk, no AI attribution.
Each item: code → tests green → commit → deploy (OTA if mobile).

## EVAL CLOSED — verdict GO (2026-07-13)
Window closed at day 13/10. All hard gates passed:
Sharpe 9.91 > 1.0 · MaxDD 1.04% < 15% · +5.9% vs SPY +3.0% · Sortino 23.65.
Next phase = go-live prep (docs/go-live-checklist.md). NO real money is moved
autonomously: user must open + fund their own live Alpaca account.

### Pre-go-live safety work → branch `feat/pre-golive-safety` (NOT on main)
Round-4 items 2/3/9/6 implemented + green (192 tests) but HIGH-blast on the
live execution path. Held off main/deploy until drilled:
GATE = drill kill-switch RUN/PAUSE_NEW/FLATTEN_ALL on paper, supervised, then
merge → deploy. Undrilled execution code must not reach the running trader.

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

## 🎯 EVAL GATE — GO confirmed (2026-07-10, day 11/10)
- day 11/10, verdict **GO**, days_remaining=0. Sharpe **7.67**, MaxDD **-1.04%**, total return **+4.03%** vs SPY +2.52% (α +1.5pt), Sortino 17.81, Calmar 163.98. All 4 hard gates green. Paper equity **$104,226** (daily +4.23%).
- Eval min-days requirement **met** → `eval_complete=true` now on /v1/eval; mobile scorecard footer reads "Eval tamamlandı · karar kesin" (deployed backend + OTA 2026-07-10).
- **STILL do not move real money automatically.** Go-live = user opens+funds live Alpaca, sets ALPACA_BASE_URL=live + live keys on the box, flips submit/routing. See docs/go-live-checklist.md.

## 🎯 EVAL GATE — GO (2026-07-09)
- /v1/eval verdict=**GO** at day 10/10 (days_remaining=0). Sharpe **9.94**, MaxDD **-1.04%**, total return **+4.55%** vs SPY +1.66% (α +2.9pt). All 4 hard gates passed. Paper account PA348DFG9628 equity **$103,643**.
- **DO NOT move real money automatically** — go-live requires the user to open + fund a live Alpaca account, set ALPACA_BASE_URL=live + live keys on the box, and flip submit/routing. See [`docs/go-live-checklist.md`](docs/go-live-checklist.md) + Deferred (go-live) below.

## Round 3 (next, eval-safe)
- [x] daily-run false-failure fix — a legitimate policy refusal (non-actionable Hold, risk guard, PDT, market closed) made `trade.py` exit rc=1 under `--submit`, so all-Hold days marked `ai-trader.service` failed + fired OnFailure push (alert fatigue, masks real broker errors). Added `ExecutionResult.error` (True only for broker/unexpected exceptions); trade.py exits non-zero only on `error`. 24 execution tests green, off-eval-path (no decision/order change), deployed 2026-07-09 ✅
- [x] eval-report false-failure fix — same anti-pattern: weekly `eval_report.py` exited rc=1 on any non-GO verdict, so during the whole open eval window (WAIT/TOO EARLY) the systemd unit showed `failed`. Verdict is data (printed + pushed via `--notify`), not job health; a real data/compute failure already raises. Now exits 0 whenever the scorecard builds; `--strict` restores the GO-only gate. 23 eval_report tests green, off-eval-path, deployed 2026-07-09 ✅
- [ ] prompt-cache markers injected into the LLM client (cache.py helpers exist) — BRANCH; needs a live API run to confirm cache hits (costs ~$1)
- [x] /v1/eval: `eval_complete` flag (days>=MIN_TRADING_DAYS) so the scorecard shows a definitive window-closed state instead of "karar penceresi açık" at days_remaining=0; read-only, off-eval-path; 7 eval-api tests green, backend deployed + OTA 2026-07-10 ✅
- [x] /v1/eval: provisional verdict ("eğilim") while TOO EARLY — projects GO/NO-GO from current Sharpe/MaxDD hard-gates ignoring the min-days req (SPY lag is a flag, not a gate); read-only reporting, off-eval-path; mobile scorecard shows trend badge next to verdict; 26 eval tests green, deployed 2026-07-07 ✅
- [x] /v1/eval: benchmark on by default so mobile scorecard + weekly push show the "Beats SPY" gate without a query param (read-only, off-eval-path); currently +3.3% vs SPY +1.6% at day 7 ✅ 2026-07-03
- [x] Portfolio: surface snapshot concentration / position-count trend — /v1/portfolio/concentration deployed 2026-06-29 (HHI, effective_n, top/top3 weight, flags, snapshot trend; read-only, off-eval-path)
- [x] Pull-to-refresh + empty states polish on Charts/Ask — Charts View→ScrollView+RefreshControl(refetch), Ask empty-state card + ticker chips; OTA preview 2026-06-30 ✅
- [x] eval_report: walk-forward holdout note when days>=60 — IS(2/3)->OOS(1/3) Sharpe split, regime-fit WARN flag; read-only, off-eval-path; 13 eval_report tests green 2026-07-01
- [x] snapshot: populate `sector` from static GICS map (sector_map.py) — display-only, off trading path; deployed + verified live 2026-07-04 ✅
- [x] E2E review critical fixes (docs/review-e2e-2026-07-04.md top-now #1/#3/#5/#7) — daily-run alerting (exit-1 + push + OnFailure unit + per-ticker timeout 1800s -k 30 + TimeoutStartSec 21600), off-box backup (private repo canberkaslan/trading-backups, deploy key, 02:15 UTC timer, sqlite online backup + snapshots + memory), preflight canary (21:45 UTC, alert-only), honest eval metrics (Sharpe/Sortino excess over FRED DGS3MO, correct Sortino downside dev, SPY total-return benchmark); adversarially reviewed (20 findings fixed pre-deploy), deployed + verified live 2026-07-04 ✅ — HEALTHCHECK_URL dead-man ping env-gated, needs healthchecks.io URL

- [x] scorecard: α vs SPY stat (portfolio return − SPY total return) as 4th grid card — makes the "Beats SPY" gate legible as a number ahead of GO/NO-GO; read-only, off-eval-path; OTA preview 2026-07-08 ✅

## Eval CLOSED 2026-07-11 — verdict GO (12/10 days; Sharpe 8.68, MaxDD 1.0%, +4.8% vs SPY +3.0%)

## Round 4 — pre-go-live hardening + mobile UI (full-system scan 2026-07-11, 41 ideas → ranked)
Eval is CLOSED: decision-path changes now allowed on main, but each HIGH-blast item gets tests + a supervised paper run first. Order matters: 6 before new screens; 2/3 before any real money.

### Critical correctness (found by scan — real bugs)
- [x] **1. PAPER/LIVE mode badge + degraded banner** (S) ✅ 2026-07-13 — trading_mode on /healthz+/readyz, StatusBanner (tri-state MOD?/PAPER/LIVE, stable height, offline after 2+ fails), hero timestamp; deployed+OTA — `trading_mode` field on /healthz (from ALPACA_BASE_URL) in agent/api/main.py; mobile health poll → /readyz (alpaca/db degraded flags); shared `<StatusBanner>` in app/(tabs)/_layout.tsx: amber PAPER / persistent red "LIVE — gerçek para" / degraded strip; last-updated ts on Portfolio hero from snapshot.timestamp_utc
- [x] **2. Kill-switch REAL wiring** (M, HIGH blast) ✅ 2026-07-13 — FileKillSwitchReader (absolute path, empty→PAUSE) in trade.py breaker; kill_check backstop in daily_run (before weekend guard, snapshot on skips); FLATTEN_ALL executes AT FLIP TIME in API w/ per-position 207 inspection (partial→fail loud); approve path gated; kill_switch_events audit; atomic writes; drilled live RUN/PAUSE/empty/garbage — FLATTEN live-fire deliberately mock-only (paper book preserved) — FileKillSwitchReader reading KILL_SWITCH_PATH (API already writes it) replaces hardcoded StaticKillSwitchReader('RUN') at agent/scripts/trade.py:216; pre-run check in daily_run.sh; FLATTEN_ALL → AlpacaClient.close_all_positions() (alpaca_broker.py:165, implemented-unreachable) + DB audit row; fail-to-PAUSE_NEW on read error; drill all 3 states on paper. Mobile switch is currently theater.
- [x] **3. Execution integrity trio** (M, HIGH blast) ✅ 2026-07-13 — stop-leg OTO without TP, gtc protective legs (day legs expired at close!), run-date idempotency key (--date threaded; post-midnight decisions can't shift it) + suffixed -rN retries + weld-free duplicates + partial-fill→NEEDS_RECONCILE; explicit status map (partially_filled→PARTIAL, stopped/suspended→NEEDS_RECONCILE); 210 tests — executor.py: (a) line 193 `if tp and sl:` drops the STOP leg when price_target missing → attach stop alone (OTO) so no naked positions (this is why live positions show stop_loss 0.0); (b) line 213 status mapping false-REJECTs `partially_filled` → explicit allowlist + NEEDS_RECONCILE; (c) derive_client_order_id from (ticker, trade_date, side) not per-run UUID + query-before-submit idempotency. test_execution.py + supervised paper run.
- [x] **8. Delete fake "Auto-execute trades" switch** (S) ✅ 2026-07-13 — honest 'Emir gönderimi' card (paper auto-submits via daily run; live→hold+approve) — settings.tsx local useState controls nothing; replace with honest read-only card "Her emir manuel onay gerektirir". If it returns, it returns as a real backend flag w/ ADR-005 gates.
- [x] **9. Honest daily P&L + intraday DD** (S) ✅ 2026-07-13 — last_equity baseline (live verify: -0.38% gerçek günlük vs eski +4.9% kümülatif yalan), 5Min portfolio_history max DD — portfolio.py:113 daily_pnl = equity−100000 (inception!) and :122 max_drawdown_today=0.0 hardcoded; parse Alpaca last_equity into Account model, daily_pnl = equity − last_equity, DD from intraday portfolio_history. Mobile renders these fields already. Bonus: last_equity is the circuit-breaker daily-DD input.
- [x] **10. Dead-man switch + preflight fix** (S) ✅ 2026-07-13 kod tarafı — EXPECTED_TRADING_MODE (preflight+secrets), OnFailure on eval-report.service, install.sh ships api+eval-report units, ping_healthcheck on kill-skip days. KALAN: healthchecks.io hesabında check açıp HEALTHCHECK_URL'i secrets.env'e koymak (user) — healthchecks.io check + HEALTHCHECK_URL into /opt/ai-trader/secrets.env (ping code shipped 07-04, never configured); OnFailure= on eval-report.service; preflight.py:37 hardcoded paper-assert → EXPECTED_TRADING_MODE env (else go-live day = false-alarm storm)

### Trust/safety UX (mobile, before real money)
- [~] **4. Approve-flow hardening** (M) — ✅ 2026-07-21 slice-1 (OTA): (a) biometric.ts OS-passcode fallback — pure `resolveAuthMode(hasHardware, getEnrolledLevelAsync)` → biometric/passcode/none; SECRET-level devices (PIN, no Face/Touch ID) no longer permanently locked out of approving; `authenticate()` returns {success, mode}, 'none' → distinct "no device lock" alert; 4 authPolicy jest tests. (b) approve/[orderId].tsx gates the false "not in pending list" on usePendingOrders.isLoading → spinner during push deep-link fetch race. (c) deleted dead "Approve (TBD)"/Reject stubs on trade/[ticker].tsx → honest read-only decision-detail note (approve happens from Orders tab). TR copy on all touched strings. 36 mobile jest green. KALAN: expo-haptics (needs native rebuild, NOT OTA-safe — deferred to next dev build) + hold-to-approve friction.
- [ ] **5. Order history + fills + cancel** (M) — Orders tab segmented Pending|Geçmiş; consume orphaned useOrders hook (GET /v1/orders) rendering broker_status/filled_qty/avg_fill_price/submitted_at/rejection_reasons (none rendered today); wire api.cancelOrder (zero callers) confirm-gated; backend: cancel_order persist OrderUpdate row; delete dead `notional` var orders.tsx:50
- [~] **11. API auth hardening** (M) — ✅ 2026-07-13 core (off-eval-path, deployed): fail-closed Cognito JWT when python-jose missing (was: parse unsigned claims → forgeable `sub`; now 500 unless ALLOW_UNVERIFIED_JWT=1 for local dev); `secrets.compare_digest` on dev-bearer (timing-safe); CORS `*` → env-driven allowlist (CORS_ALLOW_ORIGINS, default localhost Expo; native fetch/curl unaffected), methods/headers narrowed. 213 tests green. KALAN (deferred, higher blast/user step): rate limit /v1/* (needs slowapi + care not to throttle daily_run); rotate DEV_API_TOKEN + stop baking into public OTA bundle (per-device token / CF Access — user); notifications/test → only auth'd user's tokens
- [ ] **12. Notifications inbox + contextual permission** (M) — persist pushes (listeners exist in _layout.tsx) → zustand+AsyncStorage inbox w/ unread badge + same deep-links; move permission request from startup to first pending order / Settings row; clear badge on open

### Mobile UI polish (the "arayüz" work)
- [~] **6. Design-system extraction FIRST** (M) — ✅ 2026-07-14 first slice (OTA preview): `src/components/ErrorState` (retry-wired via refetch) + `EmptyState` extracted; Portfolio/Orders/Agents inline "Backend unreachable"+empty blocks replaced → dev-CLI leaks GONE (uvicorn / `python -m scripts.trade` no longer shown to users), dead `notional` proxy + now-unused styles dropped, honest TR copy. KALAN: Card, Stat, RatingBadge, Skeleton (kills bare "Loading…"); raw hexes → theme/colors.ts. ✅ 2026-07-17 slice: Money done — `src/utils/format.ts` (formatUsd/formatPct single source), 3 formatUsd + 2 formatPct copies killed (orders/portfolio/approve), negative sign outside $, `signed` opt replaces inline `>=0?'+'` P&L patterns, null/NaN→em-dash; wired the dead `test:jest` script (jest.config.js jest-expo preset + babel.config.js), 7 format tests green; OTA preview.
- [~] **7. Home dashboard: equity curve + drawdown** (M) — 2026-07-18 slice-1 SHIPPED (OTA): wrapped GET /v1/portfolio/history (usePortfolioHistory), portfolio.tsx now renders OTA-safe RN-View equity area chart w/ worst-DD annotation + GO/NO-GO verdict hero badge (pure helpers utils/equity.ts, jest 18 tests). 2026-07-19 slice-2 SHIPPED (OTA): period selector 1M/3M/6M (PERIODS const drives usePortfolioHistory+useEval+refresh invalidation; backend forwards to Alpaca, EVAL_START_DATE trims — windows currently coincide, diverge past 1mo) + drawdown ribbon under the curve (ddIntensity pure helper, cells tinted by relative DD depth; 5 new jest tests, 23 green). 2026-07-20 slice-3 SHIPPED (OTA): SPY overlay + α chip — usePrices('SPY', PERIOD_DAYS[period]) forwarded to EquityChart; rebaseSpy anchors SPY closes to portfolio start-equity + trims to equity window, combinedScale prevents overlay clipping, spyReturnPct/alphaPct pure helpers; dotted SPY markers per date-aligned column + Portföy/SPY legend + α chip (green/red, hidden when benchmark null); 7 new jest tests, 32 green. Item 7 home dashboard COMPLETE.
- [ ] Quick UI wins: tab bar icons + userInterfaceStyle 'dark' (light-mode device → white tab bar under dark screens bug); WCAG contrast (textMuted #666 on #0a0a0a fails AA) + 44pt touch targets + accessibilityLabels on money actions; render already-fetched debate_transcript/take_profit/tokens_in/out+latency on trade/[ticker] + agents; sortino/calmar on scorecard; sector card from Position.sector + /v1/portfolio/concentration wrap

### Round 4 later (post-go-live)
- Fill reconciliation + realized P&L ledger (scripts/reconcile.py hourly, closed_trades table, GET /v1/trades, win-rate/expectancy) → THEN reflection memory on realized fills
- Circuit breaker real inputs (5/7 gates placeholder-fed) — first supervised live week, uses last_equity from #9
- Position detail screen + per-ticker history from snapshot JSONL; kill-switch audit trail + header pill (after #2); LLM cost panel (after pipeline.py tokens_in/out=0 stub fix); TR-primary language sweep; paper lane as permanent staging (templated units + EXPECTED_ALPACA_ACCOUNT interlock); ws.ts is implemented-unused (+ close() reconnect bug) — decide: wire realtime or delete; backup restore drill; secrets → /etc/ai-trader/
- Dropped: /v1/limits risk panel (protection theater until real caps merge)

## Deferred (go-live)
- HTTPS (cloudflared/caddy) — needs domain decision; low risk on paper.
- Merge `feat/cost-opt-adr006` + flip routing flag — eval window now CLOSED. Branch rebased
  clean on today's main, 167 tests green, default-OFF proven. Merge + flag-flip left for human
  (go-live decision) — see [docs/cost-opt-merge-readiness.md](docs/cost-opt-merge-readiness.md).
  Blocker before flip: refresh stale model IDs (opus-4-7/sonnet-4-6 → 4.8/5).

## Done (2026-06-24)
- accumulation fix, universe 3→11, clean book, timeout 150min
- 7a eval scorecard, 7b /analyze, 7c Ask tab + OTA
- 7e Charts, 7f Analiz-Et deep-link, 7g Settings kill-switch+health, snapshot logger
- cost-opt routing on branch (opt-in, not deployed)

## Daily loop 2026-07-16 (eval CLOSED — GO 15/10d)
- [x] scorecard: Sortino + Calmar render (were typed+returned, never shown) → OTA preview (f885455)
- Live: verdict GO, Sharpe 9.47, MaxDD 1.04%, +6.3% vs SPY +2.9%; equity $107.7k, net P&L +$8.3k
- Next: item 7 home equity-curve dashboard (backend /v1/portfolio/history done, absent from endpoints.ts)
