# Roadmap

Phased delivery plan. Each phase has a hard exit gate; we do **not** advance until the gate is met.

**Scope: US equities only** (see [ADR-008](adr/008-scope-us-only.md)).

## Phase 0 — Architecture & scaffolding (DONE)

- [x] Research synthesis (4 parallel streams)
- [x] ADRs written (001-007 + 008 scope reduction)
- [x] Monorepo structure (`agent/` + `infra/` + `mobile/` + `docs/`)
- [x] GitHub repo + CI workflows

## Phase 1 — Data pipeline (DONE)

- [x] Polygon.io account + API key in Secrets Manager
- [x] Anthropic + FRED API keys
- [x] SEC EDGAR client (no auth, just User-Agent compliance)
- [x] Polygon REST client + S3 parquet bulk loader
- [x] FRED macro client (Fed funds, CPI, VIX, yield curve)
- [x] Reddit PRAW wrapper (client_id pending — Phase 3 trigger)
- [x] S3 dev bucket + AAPL 2024 (252 bars) end-to-end demo
- [x] 23/23 tests passing

**Gate met:** Polygon API + S3 parquet works end-to-end.

## Phase 2 — Agent fork wiring (DONE)

- [x] TradingAgents v0.2.5 vendored as git subtree (`agent/vendor/tradingagents/`)
- [x] Driven via `TRADINGAGENTS_*` env overrides (Anthropic provider, Opus 4.7 + Sonnet 4.6)
- [x] Output mapped to our `AgentDecision` schema (regex parser for rating/PT/horizon)
- [x] **First live decision: AAPL → Overweight, $310 PT, 12-18mo (17 LLM calls, all 200 OK)**

**Gate met:** `python -m tradingagents_us.graph.pipeline --ticker AAPL` produces a structured decision.

## Phase 3 — Risk & backtest (NEXT)

- [ ] Wire risk layer to pipeline output (Kelly + ATR sizing from LLM PT/stop)
- [ ] Per-agent LLM routing (Haiku for risk debators + market/sentiment analysts)
- [ ] Anthropic prompt-caching markers on analyst reports (70-85% input cost cut)
- [ ] Reddit client_id (when Responsible Builder Policy clears) or StockTwits substitute
- [ ] Finnhub news/earnings client
- [ ] Wikipedia historical S&P 500 universe scraper (survivorship-safe)
- [ ] vectorbt backtest harness
- [ ] Walk-forward CV (2015–2023 train, 2024–2025 holdout)
- [ ] Bayesian optimization (Optuna) on data weights

**Gate:** Out-of-sample (2024–2025) Sharpe > 1.0 net of modeled costs. If < 1.0, halt and re-evaluate.

## Phase 4 — Infra & paper trade

- [ ] Alpaca paper key (account verification + UI workaround)
- [ ] Terraform: VPC + EC2 agent runner + Aurora + Redis + ALB
- [ ] FastAPI mobile backend (skeleton endpoints)
- [ ] Alpaca paper account integration
- [ ] EventBridge schedule (US 22:30 UTC daily)
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

**Gate:** End-to-end demo: signup → see paper portfolio → trade approval push → biometric approve → Alpaca paper executes → confirmation push.

## Phase 6 — Paper trade observation (90 days minimum)

No new code. Pure observation:

- Daily review of agent decisions
- Weekly Sharpe/DD review
- Track regime-change behavior (volatility spike, earnings surprises)
- Track LLM cost drift
- Track wash-sale tracking accuracy

**Gate to Phase 7 (live):**
- Sharpe > 1.0 net
- Max DD < 15%
- No infra incidents in last 30 days
- Across at least one regime change

## Phase 7 — Live (deferred, conditional)

- Separate AWS account (compliance isolation)
- $2–5K initial capital, 25% of paper sizing
- Daily monitoring for 30 days
- Scale-up only after 30 days live with paper-comparable metrics

## Phase 8+ — Future (parking lot)

- Cross-asset portfolio optimization (TradingAgents only does per-ticker)
- Real semantic memory (pgvector replacing TradingMemoryLog markdown)
- Multimodal agent (chart-as-image via FinAgent approach)
- Trading-R1 style RL on top of LLM signals
- Watch app companion
- Multi-user / SaaS — requires US RIA registration
- **Reconsider BIST market addition** — only after US system is profitable in live

## Hard stops

We halt and re-evaluate at any of these:

- Phase 3 Sharpe < 1.0 on holdout → strategy doesn't work, do not deploy
- Phase 6 live paper Sharpe drops below 0.8 for 2 consecutive weeks
- Any data-licensing violation flagged by a provider
- SEC enforcement action
- Anthropic API cost overrun > $500/mo for 2 consecutive months
