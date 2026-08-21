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
- [~] **5. Order history + fills + cancel** (M) — ✅ 2026-07-23 slice-1 (OTA, read-only): Orders tab segmented **Onay Bekleyen | Geçmiş**; consumes the orphaned `useOrders` hook (GET /v1/orders) — Geçmiş cards render broker_status (TR label + tone), fill progress (`filled_qty/quantity lot`), avg_fill_price, submitted_at. Pure helpers `utils/orders.ts` (orderStatusMeta/fillSummary/formatOrderDate — Alpaca status map, NaN clamps, UTC TR date), 14 jest green (50 total), tsc clean, TR copy sweep on tab. KALAN slice-2 (needs backend): wire api.cancelOrder (zero callers) confirm-gated + backend cancel_order persist OrderUpdate row; rejection_reasons render on rejected rows — ✅ 2026-08-03 slice-2 DONE (backend cancel rewrite + confirm-gated mobile cancel + TR rejection reasons); item 5 COMPLETE
- [~] **11. API auth hardening** (M) — ✅ 2026-07-13 core (off-eval-path, deployed): fail-closed Cognito JWT when python-jose missing (was: parse unsigned claims → forgeable `sub`; now 500 unless ALLOW_UNVERIFIED_JWT=1 for local dev); `secrets.compare_digest` on dev-bearer (timing-safe); CORS `*` → env-driven allowlist (CORS_ALLOW_ORIGINS, default localhost Expo; native fetch/curl unaffected), methods/headers narrowed. 213 tests green. KALAN (deferred, higher blast/user step): rate limit /v1/* (needs slowapi + care not to throttle daily_run); rotate DEV_API_TOKEN + stop baking into public OTA bundle (per-device token / CF Access — user); notifications/test → only auth'd user's tokens
- [x] **12. Notifications inbox + contextual permission** (M) — ✅ 2026-08-01 (OTA): zustand store `stores/notifications.ts` persisted via **expo-secure-store** (AsyncStorage would need a native rebuild → not OTA-safe), fed by BOTH `addNotificationReceivedListener` (foreground) and the tap handler (tap copy filed as read); bell + unread badge in the shared `StatusBanner` (inside the 24pt strip, hitSlop→44pt target) → new `app/notifications.tsx` history screen, rows deep-link via the SAME `routeForType` the tap handler uses (single source of truth, can't drift), open = markAllRead + `setBadgeCountAsync(0)`, confirm-gated Temizle. Permission moved off startup: `syncPushTokenIfGranted()` (silent, no prompt) at boot; the OS prompt now fires from the Settings row (live permission status + inbox link, TR copy) or `maybeAskForPush()` on the first pending order (once/session, explained first) — a cold-start denial is permanent and would mean never seeing an approval alert again. Pure helpers `utils/inbox.ts` (routeForType, toInboxItem w/ truncation, mergeInbox dedup that never resurrects a read row, UTF-8-aware byte-budget serializeInbox, tolerant parseInbox, TR relative dates, badgeLabel 9+), 27 new jest (117 total), tsc no new errors, 213 backend tests green. Read-only, off-eval-path.

### Mobile UI polish (the "arayüz" work)
- [~] **6. Design-system extraction FIRST** (M) — ✅ 2026-07-14 first slice (OTA preview): `src/components/ErrorState` (retry-wired via refetch) + `EmptyState` extracted; Portfolio/Orders/Agents inline "Backend unreachable"+empty blocks replaced → dev-CLI leaks GONE (uvicorn / `python -m scripts.trade` no longer shown to users), dead `notional` proxy + now-unused styles dropped, honest TR copy. KALAN: Card, Stat, RatingBadge, Skeleton (kills bare "Loading…"); raw hexes → theme/colors.ts. ✅ 2026-07-17 slice: Money done — `src/utils/format.ts` (formatUsd/formatPct single source), 3 formatUsd + 2 formatPct copies killed (orders/portfolio/approve), negative sign outside $, `signed` opt replaces inline `>=0?'+'` P&L patterns, null/NaN→em-dash; wired the dead `test:jest` script (jest.config.js jest-expo preset + babel.config.js), 7 format tests green; OTA preview.
- [~] **7. Home dashboard: equity curve + drawdown** (M) — 2026-07-18 slice-1 SHIPPED (OTA): wrapped GET /v1/portfolio/history (usePortfolioHistory), portfolio.tsx now renders OTA-safe RN-View equity area chart w/ worst-DD annotation + GO/NO-GO verdict hero badge (pure helpers utils/equity.ts, jest 18 tests). 2026-07-19 slice-2 SHIPPED (OTA): period selector 1M/3M/6M (PERIODS const drives usePortfolioHistory+useEval+refresh invalidation; backend forwards to Alpaca, EVAL_START_DATE trims — windows currently coincide, diverge past 1mo) + drawdown ribbon under the curve (ddIntensity pure helper, cells tinted by relative DD depth; 5 new jest tests, 23 green). 2026-07-20 slice-3 SHIPPED (OTA): SPY overlay + α chip — usePrices('SPY', PERIOD_DAYS[period]) forwarded to EquityChart; rebaseSpy anchors SPY closes to portfolio start-equity + trims to equity window, combinedScale prevents overlay clipping, spyReturnPct/alphaPct pure helpers; dotted SPY markers per date-aligned column + Portföy/SPY legend + α chip (green/red, hidden when benchmark null); 7 new jest tests, 32 green. Item 7 home dashboard COMPLETE.
- [~] Quick UI wins: ✅ 2026-07-24 WCAG contrast (OTA) — textMuted #666 (3.4:1, AA fail) → #949494 (5.0:1 lightest surface, 6.5:1 background); added utils/contrast.ts (WCAG luminance/ratio helpers) + guard test asserting every text token clears AA on all 3 backgrounds + regression check that #666 fails; 64 jest green, tsc clean on touched files. KALAN: tab bar icons + userInterfaceStyle 'dark' (NATIVE, not OTA — light-mode device → white tab bar under dark screens bug); 44pt touch targets + accessibilityLabels on money actions; render already-fetched debate_transcript/take_profit/tokens_in/out+latency on trade/[ticker] + agents; sortino/calmar on scorecard; sector card from Position.sector + /v1/portfolio/concentration wrap

- [~] Quick UI wins cont.: ✅ 2026-07-25 trade/[ticker] detail (OTA) — the "Tam detay →" link from Agents now actually shows full detail: per-agent reasoning cards (model badge + tokens_in↓/out↑ + latency via `utils/decision.ts` formatTokens/formatLatency), debate_transcript rendered in debate-flow order (debateEntries sorts bull→bear→…→PM, drops blank roles, debateRoleLabel title-cases), added the previously-dropped `take_profit` alongside price_target, money via formatUsd, bare English "Loading…"/"Back" → TR ("Yükleniyor…"/"Geri") + honest "Karar bulunamadı." empty state; 13 new jest (77 total), tsc clean on touched files. All already-fetched fields — read-only, off-eval-path.

- [~] Quick UI wins cont.: ✅ 2026-07-26 Portfolio risk & sector-allocation card (OTA) — wraps GET /v1/portfolio/concentration (useConcentration hook + Concentration type) into a Risk & Dağılım card on portfolio.tsx: diversification badge from effective_n, top-name + top3 weights (toned vs the 10% single-name cap), >10% flag count; sector split derived locally from snapshot Position.sector (market-value weighted, GICS→short TR labels, cash as remainder) rendered as horizontal weight bars. Pure helpers utils/concentration.ts (sectorLabelTr/sectorAllocation/topWeightTone/diversificationLabel), 13 new jest (90 total), tsc clean on touched files. Read-only, off-eval-path. KALAN: tab bar icons + userInterfaceStyle 'dark' (NATIVE); 44pt touch targets + accessibilityLabels on money actions.

### Round 4 later (post-go-live)
- [x] **Fill reconciliation + realized P&L ledger** ✅ 2026-08-09 — see the daily-loop entry below.
      Reflection memory on realized fills is now UNBLOCKED (closed_trades is the input).
- [ ] **Sizer has no cash / buying-power cap** (found 2026-08-09). `size_from_decision` sizes off
      `account_equity` only (sizer.py:98), so a fully-invested long book drifts into margin as it
      appreciates — the paper account sits at cash **-$510.54** on $109,574 equity (0.47% levered).
      Harmless on paper; on a funded live account that is real leverage plus margin interest, and
      nothing in the risk stack currently refuses it. Fix = cap notional at min(equity·pct, cash) or
      an explicit `MAX_GROSS_EXPOSURE_PCT`. Decision-path change → tests + supervised run. This is
      why `assert acct.cash >= 0` no longer lives in test_dataflows_smoke.py.
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

## Daily loop 2026-08-21 (eval CLOSED — GO 23/10d, kitap 9 run günüdür DONMUŞ)
- [x] **Inert order-flow push alert** (backend, off-decision-path — deployed 28a9e28).
  Dünkü iş teşhisi app'e taşıdı; ama teşhisin bütün mesele olduğu durumda (donmuş kitap her
  metrikte sağlıklı görünür) kimsenin app'i açmak için bir sebebi yok. Kanıt: kitap **08-11'den
  beri** inert, GO rozeti 9 run günüdür render ediliyor ve hiçbir şey kimseyi uyandırmadı.
  `scripts/inert_alert.py` artık `daily_run.sh`'ın kuyruğunda: aynı order row'ları, aynı eşik
  (`INERT_THRESHOLD_RUN_DAYS` route'tan actionability modülüne taşındı — rozet ile push'un
  "donmuş" tanımı hakkında ayrışması mümkün olmasın), run günü başına **en fazla bir** push.
  Politika saf ve test edilebilir (`notifications/inert_alert.py`), asıl kararlar SUSMA
  kararları:
  - **`idle` asla uyarmaz.** Boş pencere = run hiç olmadı; bu cron'un arızası ve zaten
    daily_run'ın exit code'u + healthchecks dead-man's switch'i sahipleniyor. Burada
    raporlamak stratejiyi scheduler'ın suçuyla itham etmek olurdu — ve yanlış subsystem için
    bağıran bir alert, insanların swipe etmeyi öğrendiği alerttir.
  - Bilinen bir freeze yalnızca **5 run günü daha derinleşince** ya da **dominant blocker
    değişince** tekrar uyarır (aynı derinlikte yeni bir sebep yeni bilgidir). Pencere kayarken
    `inert_run_days` düşebiliyor — bu bir çözülme değil, o yüzden re-alert tetiklemiyor.
  - **Recovery bir kez** push'lanır, sadece freeze'i gerçekten raporladıysak: donduğu söylenen
    birinin çözüldüğünü öğrenmesinin başka yolu yok.
  - State **sadece teslim edilen push'tan sonra** yazılır (`_send` → `(delivered, detail)`;
    cihaz yok / `PUSH_DISABLED` / Expo hatası hepsi False döner). Teslim edilmemiş bir alert'i
    "raporlandı" diye kaydetmek bu script'i sessizliğe çeviren tek bug. Bozuk state dosyası ise
    "hiç uyarılmadı" okunur — fazladan bir push, donmuş kitap hakkında sessizlikten iyidir.
  Her koşulda exit 0 (daily run'ın kendi exit code'unu maskelemesin) ve kill-switch skip
  yollarının ALTINDA duruyor — PAUSE_NEW/FLATTEN_ALL kitabı kasıtlı olarak inert'tir, page etmez.
  21 yeni test (**348 backend** yeşil). Box'a deploy + API restart, /healthz 200. Canlı dry-run:
  `verdict=inert inert_run_days=9 submitted=6/234 → ⚠️ ... top blocker: rating=Hold (131)`.
- **YENİ BULGU — push kanalının abonesi YOK:** `device_tokens` tablosunda **0 satır**. Yani bu
  alert de, **kill_check'in FLATTEN_ALL PARTIAL alarmı da, daily run failure alarmı da** bugüne
  kadar hiçbir yere gitmedi (`notify_ops` "no registered devices" deyip 0 dönüyor — best-effort
  tasarımı gereği sessiz). Kanal ancak Canberk app'i açıp bildirim izni verince (mobile →
  `POST /v1/notifications/register`) canlanır. Yeni alert bunu doğru ele alıyor: teslim
  edilmedi → state yazılmadı → cihaz kaydolduğu ilk gün 9 günlük freeze'i anlatarak ateşler.
- Live: verdict GO — Sharpe 1.23, Sortino 1.98, MaxDD −2.69%, Calmar 8.58, 23/10 gün.
  Getiri **+1.83% vs SPY +1.91% → α −0.08pp** (SPY gate ❌). Equity $106,990, günlük −$703 (−0.65%),
  cash $3,378 (negatif değil — 08-14 cash cap tutuyor), 10 pozisyon.
  Order flow: 30 günde **234 emir, 6 submitted (%2.56)**, `inert_run_days` **9**, son ack 08-11.
- Sıradaki: (a) **Underweight→SELL exit path** — hâlâ kök neden, hâlâ Canberk'in çağrısı
  (decision-path, HIGH blast, supervised paper run şart). (b) SPY hard-gate kararı.
  (c) push kanalını canlandır (app'te bildirim izni) — aksi halde tüm ops alarmları teorik.

## Daily loop 2026-08-20 (eval CLOSED — GO 22/10d, kitap 8 run günüdür DONMUŞ)
- [x] **Actionability mobile'a bağlandı** (OTA + commit 596bd2c, read-only, off-decision-path).
  Dün eklenen `/v1/diagnostics/actionability` sadece curl'de yaşıyordu; verdict'in fiilen OKUNDUĞU
  iki yere taşındı. Mesele şu: karnedeki HER gate equity curve'den hesaplanıyor ve kendi sizing
  cap'lerini artık geçemeyen, tam yatırımlı bir sepetin de equity curve'ü var — "çalışıyor" ile
  "donmuş" app'te birebir aynı görünüyordu (8 run günüdür GO rozeti, son broker ack'i 08-11).
  - Portfolio → **"Emir akışı" kartı**: durum, üretilen emirlerin broker'a ulaşan payı
    (bugün **6/234 = %2.56**), son ack'in yaşı, red nedenleri bar olarak.
  - Portfolio hero rozeti + Settings karnesi: GO rozetinin yanına `⚠︎ donmuş kitap · 8g`,
    karneye tek satır caveat — **sadece** akış inert'ken. Her zaman uyaran rozet, uyarıyı öğretmez.
  Dürüstlük kuralları saf helper'larda (`utils/actionability.ts`), yani test edilebilir:
  0 emir "hiç denenmedi"dir, "%0 gönderildi" DEĞİL (ikisi stratejiye dair zıt şeyler söyler);
  boş pencere **idle** (eksik cron), asla inert; bilinmeyen verdict verbatim render ediliyor;
  bar'lar `refused`'a değil **en sık nedene** göre ölçekleniyor (bir emir aynı anda birden çok
  nedenle reddedilebildiği için toplam refused'ı aşar, refused-paydalı bar %100'ü geçerdi);
  atalet **run günü** sayıyor, pazartesi hafta sonunu 2 günlük sessizlik diye raporlamasın diye.
  Yan iş: `relativeAgeTr`/`parseUtc` → `utils/format` (realized kartıyla tek yaş merdiveni + tek
  naive-timestamp kuralı; cihaz-local parse her yaşı UTC offset'i kadar kaydırır — İstanbul'da 3sa,
  bir stale bayrağını ters çevirmeye yeter) ve `trimmed_to_zero_by_cash_cap` TR etiketi eklendi
  (08-14'ten beri ham İngilizce render ediliyordu). 22 yeni jest (**189**), **327 backend** yeşil,
  tsc temiz (2 bilinen pre-existing hariç).
- **CANLI TEŞHİS kötüleşti:** 30 günde **234 emir, 6'sı broker'a ulaştı (%2.56)**, 228 red.
  Son gönderim **08-11** → `inert_run_days` 6 → **8**. Nedenler: `non-actionable rating=Hold` 130 ·
  `trimmed_to_zero_by_portfolio_caps` 93 · `rating=Underweight` 2 · `cash_cap` 1.
  Cash **−$856.29** (7. gün, hiç geri ödenmiyor), 10 pozisyon, hepsi %10 cap'te ya da üstünde.
- Live: verdict GO — Sharpe 1.14, Sortino 1.83, MaxDD −2.69%, Calmar 8.15, 22/10 gün.
  Getiri **+1.67% vs SPY +2.78% → α −1.11pp** (SPY gate ❌). Equity $107,864, günlük +$345 (+0.32%).
  **Sharpe 1.14, gate'in (1.0) hemen üstünde** — 1M penceresi kaydıkça eriyor (08-16: 1.92 → bugün 1.14).
- Sıradaki: (a) **Underweight→SELL exit path** (sizer.py:45 `_side_from_rating` None dönüyor,
  hiçbir caller karar vermiyor → tek çıkış yolu `Sell`; trim yok → cap altına inilmiyor → yeni BUY
  sonsuza kadar bloke). Decision-path + HIGH blast, boyutlandırma politikası Canberk'in çağrısı —
  supervised paper run şart. (b) inert>=3 run günü için günlük push (backend, off-decision-path).
  (c) SPY hard-gate kararı.

## Daily loop 2026-08-18 (eval CLOSED — GO 21/10d, ama kitap DONMUŞ)
- [x] **`/v1/diagnostics/actionability`** (read-only, DB-only, off-decision-path — deployed 44d1df5).
  /v1/eval equity curve'ü ölçüyor; tamamen yatırımlı, kendi sizing cap'lerini artık geçemeyen bir
  sepetin de equity curve'ü var. Yani "strateji çalışıyor" ile "strateji donmuş" bugüne kadarki
  HER metrikte aynı görünüyordu. Yeni endpoint eksik soruyu emir satırlarından cevaplıyor:
  kaç emir broker'a ulaştı, geri kalanını ne engelledi, kaç ardışık **run günü** sıfır gönderimle
  kapandı. İki kasıtlı kural: `submitted` = broker ack'i, `risk_approved` DEĞİL (risk_approved
  ajanın kendine verdiği not; niyeti icra saymak tam da bu endpoint'in yakalamak için var olduğu
  iyimserlik). `inert_run_days` **run günü** sayıyor, takvim günü değil — hafta sonu satır
  üretmediği için takvimle sayarsak her pazartesi 2 gün atalet raporlanır; boş pencere ise
  "idle", asla "inert" (run hiç olmamış olabilir, bu stratejinin suçu değil). Reason'lardaki
  canlı değer parantezi normalize ediliyor (`(spendable=$0.00)` / `(spendable=$18.40)` → tek
  bucket), yoksa kalıcı bir blocker tek tek anomalilere dağılıp görünmez oluyor.
  23 yeni test (327 backend yeşil).
- **CANLI TEŞHİS (asıl mesele):** 30 günde **224 emir, sadece 7'si broker'a ulaştı** (%3.1),
  217 reddedildi. Son gönderim **08-11** → `inert_run_days=6`, verdict **inert**.
  Reddedilme nedenleri (30g): `non-actionable rating=Hold` 121 · **`trimmed_to_zero_by_portfolio_caps` 91**
  · `non-actionable rating=Underweight` 2 · `trimmed_to_zero_by_cash_cap` 1.
  Yani kitap 10 isimde, her biri %10 per-position cap'inde ya da üstünde (MSFT %12.0, NVDA %11.4,
  JPM %10.4, V %10.0) → her Overweight konviksiyonu sıfıra kırpılıyor. Sharpe 2.59 / GO verdict'i
  bir STRATEJİYİ değil, donmuş bir buy-and-hold sepetinin mark-to-market'ini ölçüyor.
- **KÖK NEDEN — FİLED, KASITLI DÜZELTİLMEDİ (decision-path, HIGH blast, Canberk'in kararı):**
  `risk/sizer.py:45 _side_from_rating` → `Underweight` **None** dönüyor, yorumu "caller decides"
  ama **hiçbir caller karar vermiyor** — trade.py sadece reddi kaydediyor. Sistemdeki TEK çıkış
  yolu en sert tier olan `Sell`. Ajanın fiilen ürettiği "azalt" sinyali (60 günde 6 kez) yere
  düşüyor. Sonuç zinciri: trim yok → pozisyonlar %10 cap'in altına inmiyor → yeni BUY sonsuza
  kadar bloke; ayrıca negatif cash (−$856.29, 5. gün) hiç geri ödenmiyor.
  Fix = Underweight → kısmi SELL (cap'e geri kırp). Bunu tek taraflı deploy ETMEDİM: gerçek emir
  üreten bir exit politikası, tests + supervised paper run kuralına tabi ve boyutlandırma
  politikası (ne kadar trim?) Canberk'in çağrısı.
- Live: verdict GO (gates: Sharpe 2.59>1.0 ✅, MaxDD −2.69%<15% ✅, 21/10 gün ✅, SPY ❌ +3.29% vs
  +4.62% → α −1.33pt). Equity $107,352, cash −$856.29, 10 pozisyon, günlük −$1,602 (−1.47%).
  Realized (eval penceresi): 4 işlem, +$130.56, 4/0 — ama son fill 07-31.
- Sıradaki: (a) Underweight→SELL exit path (supervised, Canberk onayı), (b) actionability'yi
  mobil Settings/scorecard'a bağla + günlük push (inert>=3 gün), (c) SPY gate'i hard yapma kararı.

## Daily loop 2026-08-17 (eval CLOSED — GO 24/10d)
- [x] **sp500_history scraper fix** (backend, off-decision-path — 81f4976). Dün "bayat selector"
  diye not düşülen test'in kökü: Wikipedia **additions/removals tablosunu ayrı bir makaleye taşımış**
  ("List of S&P 500 companies" → "Historical components of the S&P 500", 1994'e kadar 408 satır).
  Scraper sadece eski makaleye bakıyordu.
  Asıl mesele test'in kırmızı olması değil, **kırmızı olmasaydı fark edilmeyecek olması**:
  `fetch_changes()` tabloyu bulamayınca `log.warning` + **boş liste** dönüyordu. Boş change listesi
  bozuk bir cevap değil, YANLIŞ bir cevap — `members_as_of()` geçmişi "bugünden geriye doğru her
  değişikliği geri alarak" kuruyor, dolayısıyla hiç değişiklik yoksa **2007 için bugünün endeksini**
  döndürüyor. Yani modülün var olma sebebi olan survivorship bias, hiçbir hata sinyali vermeden
  geri geliyordu (LEH, WaMu, Bear Stearns hiç var olmamış gibi).
  - `fetch_changes()` önce yeni makaleyi, sonra eskisini deniyor; ikisinde de tablo yoksa
    **`SP500HistoryUnavailable` raise ediyor** ve mesajda iki URL'i de adıyla anıyor (bir sonraki
    selector bayatlamasında sıfırdan başlanmasın diye).
  - `parse_changes(html)` fetch'ten ayrıldı → layout drift'i **network'süz** test edilebiliyor;
    pandas'ın spanning header hücrelerine verdiği `Unnamed: N_level_M` adları da temizleniyor
    (yeni makale iki satırlı MultiIndex header kullanıyor).
  - `members_as_of()` change listesinin **erişemediği tarihleri reddediyor** (boş liste ya da
    as_of < en eski değişiklik). Geriye yürüyüş no-op olduğunda sessizce bugünün evrenini
    "1990 evreni" diye vermek, veriyle desteklenmeyen bir cevabı emin görünümlü hale getiriyordu.
  - Canlı smoke test artık `SP500HistoryUnavailable`'ı yutmuyor: **sadece offline makine skip**
    (network probe'u constituents fetch'i), bayat selector fail eder.
  12 offline test + canlı scrape yeşil (**304 backend**). Backtest universe reconstruction dışında
  çağıranı yok — canlı karar yolunda değil. Box'a deploy + API restart, /healthz + /v1/eval 200.
- Live: verdict GO, Sharpe 1.92, Sortino 3.20, MaxDD −4.25%, Calmar 9.11, 24/10 gün, eval_complete.
  Getiri +3.03% vs SPY +2.85% → **α +0.18pp** (dünle birebir aynı — hafta sonu, yeni bar yok).
  Equity $108,923, cash −$856.29 (3. gün sabit → sizer cash cap tutuyor, yeni exposure yok),
  10 pozisyon, günlük −$30.70. Realized (eval penceresi): 4 işlem, +$130.56, 4W/0L.
- GO/NO-GO durumu **değişmedi**: tek açık kalem SPY hard-gate kararı. α +0.18pp gürültü seviyesinde
  ve eval penceresinde hâlâ sadece 4 kapanmış işlem var → exit disiplini hakkında istatistiksel
  olarak hiçbir şey bilmiyoruz. Gerçek para kararı Canberk'in.
- Sıradaki: (a) SPY hard-gate kararı, (b) reflection memory on realized fills (branch),
  (c) cost-opt merge hazırlığı (önce stale model ID'leri tazele: opus-4-7/sonnet-4-6 → 4.8/5).

## Daily loop 2026-08-16 (eval CLOSED — GO 24/10d)
- [x] **Realized ledger eval penceresine göre kapsandı** (backend + OTA, off-decision-path — d1a3c5e).
  Üç loop'tur "agent kapattığı her işlemde para kaybediyor" diye okunan tablo yanlış kitabı ölçüyormuş.
  Forensic: 30 kapanmış round trip'in **24'ü tek gün** (2026-06-24 13:30–13:35 UTC, −$656.86) kapanmış.
  Alpaca order feed'i net: `market SELL 99 AAPL` + `market SELL 103 MSFT`, `order_class=None`,
  client_order_id broker-üretimi UUID, tüm bracket stop leg'leri aynı anda `canceled` →
  bu bir `close_all_positions` (FLATTEN). Günlük run log'larında o gün hiç SELL kararı yok, box'ta
  o saatte hiç aktivite yok. Yani strateji çıkışı değil: **over-buying bug'ı düzeltilirken
  (2cf196a) biriken AAPL/MSFT lot'ları kasıtlı olarak flatten edilip kitap temizlendi**, universe
  3→11'e çıktı ve ertesi gün `EVAL_START_DATE=2026-06-24` cutoff'u eklendi (78fadb3).
  `/v1/eval` o günden beri cutoff'u uyguluyordu, ledger uygulamıyordu — iki kart iki farklı kitabı
  ölçüyordu.

  | | window=eval (yeni default) | window=all (eski davranış) |
  |---|---|---|
  | Kapanan işlem | 4 | 30 |
  | Net realized | **+$130.56** | −$531.93 |
  | Win rate | 4W / 0L (%100) | %26.7 |
  | Expectancy | +$32.64 | −$17.73 |
  | Profit factor | — (henüz kayıp yok) | 0.22 |
  | Ort. tutma | 26.0 gün | 5.6 gün |

  Cutoff **giriş** tarihine uygulanıyor, çıkışa değil: girişi temiz kitap öncesi olan bir round trip'i
  bugünkü exit disiplininin kanıtı saymak yanlış — pozisyonu seçen ve boyutlandıran eski agent.
  `eval_window.py` tek parser: naive değer UTC okunuyor (broker fill'leri UTC; local anchor cutoff
  günündeki işlemleri yeniden sınıflandırırdı) ve bozuk değer **raise ediyor** — sessizce "cutoff yok"a
  düşmek, all-time rakamları eval-penceresi etiketiyle göstermek olurdu. Filtre SQL'de (limit
  kullanıcının göreceği satırları saysın diye), `excluded_pre_eval` her iki window'da da raporlanıyor;
  mobil kart "Eval penceresi (2026-06-24): temiz kitap öncesi 26 işlem hariç." satırını gösteriyor —
  30 işlemden 4'e sessizce düşen bir sicil cherry-picking gibi okunur. 4 işlem MIN_SAMPLE=30'un çok
  altında olduğu için kart hâlâ renksiz + "anlamlı değil" caveat'ıyla render ediyor.
  34 yeni test (291 backend, 167 jest), tsc temiz (2 bilinen pre-existing hariç).
- Live: verdict GO, Sharpe 1.92, Sortino 3.20, MaxDD −4.25%, Calmar 9.11, 24/10 gün, eval_complete.
  Getiri +3.03% vs SPY +2.85% → **α +0.18pp** (dün −0.04pp'den toparladı ama marj çok ince).
  Equity $108,954, cash −$856.29 (dünkü ile birebir aynı → sizer cash cap tutuyor, yeni exposure yok),
  10 pozisyon. Realized (eval penceresi): 4 işlem, +$130.56.
- **GO/NO-GO durumu değişmedi ama zemini değişti:** 08-14'te "NO-GO" gerekçesi iki ayaktı —
  (a) SPY gate'i hard yapılsa geçemez, (b) realized expectancy negatif. (b) artık geçersiz: negatif
  expectancy pre-eval flatten'ın artefaktıydı. (a) hâlâ açık ve asıl mesele: α +0.18pp ile SPY'ı
  "yenmek" gürültü seviyesinde, ayrıca eval penceresinde sadece 4 kapanmış işlem var → exit disiplini
  hakkında hâlâ istatistiksel olarak hiçbir şey bilmiyoruz. Gerçek para kararı Canberk'in.
- Not (bu değişiklikle ilgisiz, pre-existing): `tests/test_dataflows_smoke.py::test_sp500_history_survivor_safe`
  temiz tree'de de fail — Wikipedia S&P 500 makalesinin "changes" tablosu artık bulunamıyor
  (scraper selector'ı bayat). Sadece backtest universe reconstruction'ı etkiliyor, canlı karar
  yolunda değil.
- Sıradaki: (a) SPY hard-gate kararı, (b) reflection memory on realized fills (branch), ya da
  (c) sp500_history scraper fix.

## Daily loop 2026-07-16 (eval CLOSED — GO 15/10d)
- [x] scorecard: Sortino + Calmar render (were typed+returned, never shown) → OTA preview (f885455)
- Live: verdict GO, Sharpe 9.47, MaxDD 1.04%, +6.3% vs SPY +2.9%; equity $107.7k, net P&L +$8.3k
- Next: item 7 home equity-curve dashboard (backend /v1/portfolio/history done, absent from endpoints.ts)

## Daily loop 2026-08-09 (eval CLOSED — GO 24/10d)
- [x] **Realized P&L ledger** (backend + deployed, off-decision-path). The system reported
  unrealized P&L from day one but could not say whether it *wins*: equity being up says nothing
  about how many round trips paid, and win rate / expectancy are not computable from an open
  position. Added: pure FIFO matcher `execution/reconcile.py` (shorts first-class; a fill crossing
  zero splits into a close + an open; partials stay separate lots at their own prices; deterministic
  trade ids = hash of entry+exit activity ids so a full replay converges instead of duplicating),
  `AlpacaClient.list_fill_activities` (paginated `/account/activities/FILL` — the ORDER record
  reports an average fill price and gets pruned, so the activities feed is the only correct input),
  derived `closed_trades` table + `scripts/reconcile.py` + hourly `ai-trader-reconcile.timer`, and
  `GET /v1/trades` (stats computed over exactly the rows returned, so a ticker filter yields that
  name's record; `profit_factor` is None not ∞ with no losses yet). Full replay by design — a
  "fills since last run" window mis-pairs every trade whose entry predates it.
  **Matcher validated against live data:** 78 fills → 30 closed trades, and every symbol's leftover
  open lots reconcile EXACTLY to the live position quantity (AAPL 11+4+17+1=33, NVDA 8+41+3+2+1=55,
  all ten names). 33 new tests, 250 backend green.
- ⚠️ **What the ledger immediately revealed** — realized and unrealized disagree sharply:

  | | Equity curve (/v1/eval) | Realized ledger (/v1/trades) |
  |---|---|---|
  | Verdict | GO, 24/10 gün | — |
  | Return / P&L | +5.38% (Sharpe 3.46) | **−$531.93 net** |
  | Win rate | — | **26.7%** (8W / 22L) |
  | Profit factor | — | **0.22** |
  | Expectancy | — | **−$17.73/trade** |

  Read it carefully before drawing conclusions: the 30 round trips are small trims (avg win $18,
  avg loss $31, best $47, worst −$94) against a $109.5k book — the core thesis positions are ALL
  still open, and 100% of the +$9.5k gain is unrealized mark-to-market. So this is not "the
  strategy loses money"; it is **the trimming/exit discipline has had negative expectancy, and the
  entire reported performance is unrealized**. The GO gates measure a fully-invested long book in a
  rising tape. Worth weighing against the go-live decision; 30 trades is still a small sample.
- Live: verdict GO, Sharpe 3.46, Sortino 6.02, MaxDD −4.25%, Calmar 18.23, +5.38% vs SPY +3.74%
  (α +1.64pt), 24/10 gün, eval_complete. Equity $109,574, cash **−$510.54** (margin!), 10 pozisyon.
- Found + filed, NOT silently fixed: sizer has no cash cap → the book is 0.47% levered (see the
  Round-4-later item above). `assert acct.cash >= 0` removed from the Alpaca smoke test because it
  was a portfolio-policy claim smuggled into an account-parse test, with the reason documented at
  the call site.
- Sıradaki: reflection memory on realized fills (now unblocked — closed_trades is the input), ya da
  mobil "Gerçekleşen" kartı (/v1/trades wrap: win rate + expectancy on the scorecard), ya da sizer
  cash cap (decision-path → tests + supervised run).

## Daily loop 2026-08-03 (eval CLOSED — GO 22/10d)
- [x] **item 5 slice-2 — cancel order** (backend + OTA). Backend `POST /v1/orders/{id}/cancel` was
  three lies in twelve lines: it answered `{"status":"cancelled"}` for orders that were never at the
  broker (nothing cancelled), it never wrote an `OrderUpdate` (local DB kept the order live), and an
  Alpaca refusal (422 "order is not cancelable" = it already filled) surfaced as an opaque 500 *after*
  reporting success. Rewritten: `s.get(TradeOrderRow, id)` (was an O(n) scan of list_open_orders) →
  404 unknown / 409 "not at broker, use /reject" / 502 with the broker message on refusal, and a
  CANCELLED+`user_cancelled` update persisted ONLY after the broker acks. Deliberately no row on
  failure — the order is still live and can still fill; the broker-enriched /v1/orders view stays SoT
  (Alpaca acks with 204→`pending_cancel`, terminal `canceled` lands async).
  Mobile: `useCancelOrder` (invalidates orders+portfolio — a cancel that loses the race to a fill
  changes the book), Geçmiş cards get a confirm-gated destructive "Emri iptal et" (44pt, a11y label
  naming ticker+side+size, error Alert says the order may still be open) rendered only when
  `isCancellable(status, broker_order_id)`; unknown statuses → no button (a 502 on a money screen is
  worse than a missing button). `rejection_reasons` now render on the card, TR-translated by key with
  the numbers kept verbatim (`position_pct=12.40% exceeds 10%` → "Tek isim limiti (12.40% exceeds 10%)").
  Pure helpers in `utils/orders.ts`, 13 new jest (139 total), tsc clean except the 2 known pre-existing.
- [x] **latent test-isolation bug** found by the new tests: `tests/test_api_analyze.py` patched
  `api.deps.get_repo` BEFORE first importing `api.main`, so every route module froze
  `Depends(<_NoOpRepo lambda>)` for the whole session — any later test overriding `get_repo` silently
  got _NoOpRepo (kill-switch tests only escaped because their repo calls are try/except'd). Fixed by
  importing the app before the patch. 217 backend tests green.
- Live: verdict GO, Sharpe 2.88, Sortino 4.54, MaxDD -4.25%, Calmar 13.58, +3.87% vs SPY +0.30%
  (α +3.57pt), 22/10 gün, eval_complete. Equity $106,743 (cash $4,396 + 10 pozisyon).
- Sıradaki: NATIVE rebuild kalanları (tab bar icons + userInterfaceStyle 'dark') ya da
  cost-opt merge hazırlığı (önce stale model ID'leri tazele: opus-4-7/sonnet-4-6 → 4.8/5).

## Daily loop 2026-08-02 (eval CLOSED — GO 22/10d)
- [x] Quick UI wins KALAN: **44pt touch targets + accessibilityLabels on money actions** (OTA) —
  new pure `utils/a11y.ts` (MIN_TOUCH_TARGET, hitSlopFor, sideLabelTr, orderActionLabel,
  killSwitchLabel) + 12 jest. Approve screen: TR copy sweep (Back/Reject/Quantity/Stop/Type/
  Rating/Entry/Target/Horizon/"Portfolio Manager output"/"Loading reasoning…" → TR), spoken
  labels naming ticker+side+size on onayla/reddet, biometric hint, **reject confirm gate**
  (was one-tap, irreversible for the day), 44pt min on both buttons + back link hitSlop.
  Orders: pending card role+label+stop hint, segment tabs → role 'tab' + 44pt. Settings:
  kill-switch chips get label/hint/selected + 44pt (RUN/PAUSE/FLATTEN was announced as bare
  glyph text), push buttons 44pt. Portfolio/Charts/Ask/Agents: period pills, mode/range chips,
  ticker chips, position + decision cards → labels and 44pt. Fixed inbox.test.ts missing
  @jest/globals import (92 latent tsc errors) → tsc clean except 2 pre-existing unrelated.
  128 jest + 213 backend green. Read-only, off-eval-path.
- Live: verdict GO, Sharpe 2.88, Sortino 4.54, MaxDD -4.25%, Calmar 13.58, +3.87% vs SPY +0.30%
  (α +3.57pt), 22/10 gün. Equity $106,743, cash $4,396, 10 pozisyon (hafta sonu → günlük P&L 0).
  1M penceresi kaydıkça Sharpe 1.34 → 2.88 toparladı (zayıf günler pencereden çıktı).
- Sıradaki: item 5 slice-2 (cancel order — backend persist + confirm gate; execution path, dikkat)
  ya da NATIVE rebuild gerektiren kalanlar (tab bar icons + userInterfaceStyle 'dark').

## Daily loop 2026-08-01 (eval CLOSED — GO 21/10d)
- [x] item 12 notification inbox + contextual push permission → OTA preview (d02b5e8)
- Live: verdict GO, Sharpe 1.34, Sortino 1.85, MaxDD -4.25%, Calmar 5.46, +1.67% vs SPY +0.30% (α +1.37pt), 21/10 gün
- Portfolio: equity $106,743, cash $4,396, 10 pozisyon, günlük +$2,261 (+2.16%), net P&L +$7,258
- NOT: 1M penceresi kayıyor — Sharpe 1.34/getiri +1.67% eski günlerin (9.47/+6.3%) yerine son 1 ayı ölçüyor;
  4 hard gate hâlâ yeşil ama marj daralmış, Sharpe gate'e (1.0) yakın. İzlenecek.
- Sıradaki: item 5 slice-2 (cancel order — backend + confirm gate, execution path → dikkat) ya da
  Quick UI wins KALAN (tab bar icons + userInterfaceStyle 'dark' = NATIVE rebuild, OTA değil)

## Daily loop 2026-08-14 (eval CLOSED — GO 23/10d, ama aşağıdaki NOT'u oku)
- [x] **Sizer settled-cash cap** (decision-path, eval kapalı olduğu için deploy edildi — 6ebafe5).
  `position_sizing` içindeki HER cap equity'nin bir yüzdesi; tam yatırımlı long bir kitabın equity'si
  mark'larla birlikte büyüdüğü için her günün alımı zaten harcanmış parayla boyutlandırılıyordu.
  Canlı paper hesap 3. gündür **cash −$856.29 / equity $109k** — Alpaca'nın 4x margin buying power'ı
  ($304k) finanse ediyordu; strateji hiç margin istemedi. İki delik kapatıldı:
  1. `apply_cash_cap` — settled cash cinsinden tek cap. Aşağı yuvarlar, negatif cash'te 0 verir
     (levered kitap yeni exposure almaz). **SELL muaf**: satış cash üretir, onu cash'e bağlamak
     pozisyonu tam da çıkılması gereken drawdown'da kilitlerdi.
  2. `risk/cash_budget.py` — `daily_run.sh` trade.py'ı ticker başına AYRI process çalıştırıyor ve
     post-close emirler dolmadan hepsi bitiyor; 11 process aynı `account.cash`'i okuyup her biri
     "hepsini harcayabilirim" diyordu. Process'leri kapsayan tek state broker'ın **open order book**'u:
     pending BUY notional'ı cash'ten rezerve ediliyor → 11 bütçe tek yürüyen bütçe olur, lock/DB yok.
  Fiyatlanamayan pending BUY `None` döner (0.0 DEĞİL) ve çağıran bunu reddetmeye çevirir — bilinmeyen
  taahhüdü "taahhüt yok" saymak düzeltilen iyimserliğin ta kendisi. `available_cash=None` hâlâ
  "çağıran bir şey vermedi" demek ve cap'i atlar, yani eksik input her emri sessizce reddedemez.
  21 yeni test (270 backend yeşil). Canlı dry-run doğrulandı: AAPL buy artık
  `trimmed_to_zero_by_cash_cap (spendable=$0.00)` ile reddediliyor.
- Live: verdict GO, Sharpe 2.23, Sortino 3.74, MaxDD −4.25%, Calmar 11.04, 23/10 gün, eval_complete.
  Getiri **+3.42% vs SPY +3.46% → α −0.04pp**. Equity $109,095, cash −$856.29, 10 pozisyon.
- **NOT (GO/NO-GO için asıl mesele):** verdict "GO" diyor ama `_verdict()` içinde "beats SPY"
  bilerek *hard gate değil, flag* (kod yorumu: "not a hard gate, but a flag"). task_plan'ın kendi
  GO tanımı ise Sharpe>1.0 **VE** MaxDD<15% **VE** SPY'ı yener. Bu tanıma göre dürüst sonuç **NO-GO**.
  Üstüne realized ledger: **30 kapanmış işlem, net −$531.93, %27 win rate, profit factor 0.22,
  expectancy −$17.73/işlem**. Yani +3.42%'nin tamamı hâlâ TUTULAN pozisyonların mark-to-market'i;
  agent'ın fiilen KAPATTIĞI her şey para kaybetmiş. Gate semantiğini tek taraflı değiştirmedim —
  gerçek para kararını çeviren bir tanım değişikliği, Canberk'in çağrısı.
- Sıradaki: (a) SPY gate'i hard yap kararı, (b) realized negatif expectancy'nin kökü — exit mantığı
  (bracket TP/SL seviyeleri erken kesiyor mu? avg_win $18 vs avg_loss $31), (c) reflection memory.

## Daily loop 2026-08-10 (eval CLOSED — GO 24/10d)
- [x] **Mobil "Gerçekleşen" kartı** (OTA, off-decision-path). Dünkü ledger backend'de duruyordu ama
  app hâlâ SADECE unrealized gösteriyordu: hero equity, günlük P&L, eval scorecard — hepsi açık
  pozisyonların mark-to-market'i. Portfolio ekranına equity chart'ın hemen altına realized kart:
  net gerçekleşen P&L (büyük, tonlu), kapanan işlem sayısı + K/Z/B dağılımı, win rate / expectancy /
  profit factor, ve asıl mesele olan **realized-vs-unrealized split bar** (accent = bankaya yazılan,
  gri = hâlâ değerlemede) + tek satırlık dürüst okuma.
  Dürüstlük kuralları koda gömüldü (`utils/realized.ts`, saf + test edilebilir):
  `MIN_SAMPLE=30` altında hiçbir istatistik yeşile/kırmızıya boyanmıyor (3 işlemde %100 win rate
  gürültüdür, yeşile boyamak app'in olmayan bir edge'i iddia etmesidir) — değer yine render ediliyor,
  sadece renksiz ve caveat satırıyla. `profit_factor: null` (henüz kayıp yok) em dash, asla ∞ veya
  uydurma tavan. Split payı **net toplama değil, mutlak büyüklüklere** bölünüyor: realized −$532'ye
  karşı unrealized +$9.5k'da net-paydalı oran "−%5 gerçekleşti" gibi anlamsız bir sayı verirdi.
  `reconcileFreshness` naive backend timestamp'ini UTC olarak parse ediyor (cihaz saat farkı yaşı
  kaydırıp stale bayrağını ters çevirirdi) ve saatlik timer 3 saati geçince ⚠︎ gösteriyor —
  endpoint bilerek istek üzerine reconcile ETMİYOR, o yüzden bayatlık görünür olmak zorunda.
  23 yeni jest (162 toplam), tsc temiz (2 bilinen pre-existing hariç), 250 backend yeşil.
- Live: verdict GO, Sharpe 3.46, Sortino 6.02, MaxDD −4.25%, Calmar 18.23, +5.38% vs SPY +3.74%
  (α +1.64pt), 24/10 gün, eval_complete. Equity $109,765, cash **−$510.54** (hâlâ margin), 10 pozisyon.
  Realized ledger: 30 işlem, −$531.93 net, %26.7 win rate — yani app artık iki rakamı da gösteriyor.
- Sıradaki: sizer cash cap (decision-path → test + supervised run; negatif cash 2. gün) ya da
  reflection memory on realized fills (branch) ya da per-ticker realized alt kırılımı (trade detay).
