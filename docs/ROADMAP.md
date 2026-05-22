# Roadmap

Phased delivery plan. Each phase has a hard exit gate; we do **not** advance until the gate is met.

## Phase 0 — Architecture & scaffolding (current, week 0)

- [x] Research synthesis (4 parallel streams)
- [x] ADRs written
- [x] Monorepo structure
- [x] GitHub repo initialized
- [ ] CI scaffolding (GitHub Actions)
- [ ] Local dev environment runnable (`docker-compose up`)

**Gate:** Any new contributor can read `docs/RESEARCH.md` + `docs/ARCHITECTURE.md` and understand the system in <30 minutes.

## Phase 1 — Data pipeline (week 1)

- [ ] Polygon.io account + API key in Secrets Manager
- [ ] Finnhub account + key
- [ ] SEC EDGAR client (no auth, just User-Agent compliance)
- [ ] KAP scraper (Playwright, hourly Lambda)
- [ ] Matriks IQ trial application + socket client stub
- [ ] OHLCV bulk historical → S3 parquet (10yr × S&P 500 + BIST 30)
- [ ] Data quality checks: gap fill, outlier flagging, survivorship-safe universe

**Gate:** `python -m tradingagents_tr.dataflows.fetch --ticker AAPL --start 2015-01-01` returns clean parquet from S3 in <2s.

## Phase 2 — Agent fork & adaptation (week 2)

- [ ] Fork TradingAgents v0.2.5 (frozen tag)
- [ ] Add `tradingagents_tr/` namespace
- [ ] KAP dataflow + TR news dataflow
- [ ] Turkish language output flag wired
- [ ] Per-agent LLM routing (Dict[str, LLM])
- [ ] Prompt caching enabled on Anthropic client
- [ ] Single-ticker end-to-end test (AAPL + ASELS.IS)

**Gate:** `python -m tradingagents_tr.run --ticker AAPL --date 2025-12-01` produces a `PortfolioDecision` with all 4 reports + debate transcripts, in <5min, costing <$0.20.

## Phase 3 — Risk & backtest (week 3)

- [ ] Position sizing module (Kelly + ATR + vol target)
- [ ] Stop-loss strategies module
- [ ] Circuit breaker + kill switch
- [ ] vectorbt backtest harness
- [ ] Walk-forward CV (2015–2023 train, 2024–2025 holdout)
- [ ] Bayesian optimization (Optuna) on data weights

**Gate:** Out-of-sample (2024–2025) Sharpe ratio > 1.0 net of modeled costs (5–15 bps slippage). If < 1.0, halt and re-evaluate.

## Phase 4 — Infra & paper trade (week 4)

- [ ] Terraform: VPC + EC2 agent runner + Aurora + Redis + ALB
- [ ] FastAPI mobile backend (skeleton endpoints)
- [ ] Alpaca paper account integration
- [ ] EventBridge schedules (US 22:30 UTC, BIST 13:00 UTC)
- [ ] Grafana dashboard: equity curve, position book, agent decisions
- [ ] CloudWatch alarms → Slack

**Gate:** Paper account makes decisions daily for 7 consecutive trading days with no infra failures.

## Phase 5 — Mobile MVP (weeks 5–6)

- [ ] Expo project setup + EAS Build
- [ ] Cognito auth flow (signup, login, MFA, biometric)
- [ ] Portfolio screen (positions + P&L)
- [ ] Agent dashboard (7 cards with reasoning expand)
- [ ] Trade approval modal (TTL countdown)
- [ ] Push notifications (trade executed, drawdown alert)
- [ ] TestFlight internal release

**Gate:** End-to-end demo: signup on TestFlight build → see paper portfolio → receive trade approval push → approve via FaceID → trade executes in Alpaca paper → confirmation push received.

## Phase 6 — Paper trade observation (weeks 7–16, 90 days minimum)

No new code. Pure observation:

- Daily review of agent decisions
- Weekly Sharpe/DD review
- Track regime-change behavior (volatility spike, earnings surprises)
- Track LLM cost drift
- Track wash-sale tracking accuracy
- Identify edge cases needing risk-layer improvements

**Gate to Phase 7 (live):**
- Sharpe > 1.0 net
- Max DD < 15%
- No infra incidents in last 30 days
- Across at least one regime change (Fed meeting, earnings season, geopolitical event)

## Phase 7 — Live (deferred, conditional)

- Separate AWS account (compliance isolation)
- $2–5K initial capital, 25% of paper sizing
- Daily monitoring for 30 days
- Scale-up only after 30 days live with paper-comparable metrics

## Phase 8+ — Future (parking lot)

- Cross-asset portfolio optimization (TradingAgents only does per-ticker — needs custom layer)
- Real semantic memory (pgvector replacing TradingMemoryLog markdown)
- Multimodal agent (chart-as-image via FinAgent approach)
- Trading-R1 style RL on top of LLM signals
- Watch app companion
- Multi-user / SaaS — requires SPK `yatırım danışmanlığı` license + US RIA registration

## Hard stops

We halt and re-evaluate at any of these:

- Phase 3 Sharpe < 1.0 on holdout → strategy doesn't work, do not deploy
- Phase 6 live paper Sharpe drops below 0.8 for 2 consecutive weeks
- Any data-licensing violation flagged by a provider
- KVKK / SPK enforcement action (extremely unlikely at personal-use scale)
- Anthropic API cost overrun > $500/mo for 2 consecutive months
