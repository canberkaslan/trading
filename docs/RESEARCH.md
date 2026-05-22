# Research Synthesis — AI Trading Agent Landscape

> Synthesis of 4 parallel deep-research streams conducted 2026-05-22.
> Sources are linked inline; full deep-dives are preserved as ADRs (`docs/adr/`).

---

## 1. Existing landscape — what already exists

### Retail-facing platforms (assisted vs autonomous)

| Platform | Type | Truly agentic? | Monthly cost |
|---|---|---|---|
| Composer | No-code strategy builder + auto-execute | Partial (rule-based + AI prompt-to-strategy) | $32 |
| Trade Ideas (Holly AI) | AI signal generator | Assisted only | $118–228 |
| TrendSpider | AI chart analysis + rule bots | Partial | $107–197 |
| Tickeron Brokerage Agents | AI agents that execute | **Yes** | $60–250 |
| StockHero | No-code bot marketplace | Partial | $14–80 |
| Capitalise.ai | Plain-English automation | No (rules only) | Free w/ broker |
| AInvest / FinChat / Perplexity Finance | LLM research copilots | Research-only | $20 |
| Wealthfront / Betterment | Robo-advisor | No — deterministic MPT | 0.25–0.40% AUM |

**Key finding:** Most "AI" branded retail platforms are AI-assisted dashboards, not autonomous decision-makers. Only Tickeron Brokerage Agents and academic frameworks like TradingAgents make real LLM-driven autonomous decisions.

### Open-source frameworks (DIY stack)

| Framework | Purpose | Maturity | Notes |
|---|---|---|---|
| **TradingAgents** (Tauric) | 7-role LLM debate framework | **v0.2.5, ~78k stars** | Our base — see ADR-001 |
| FinGPT | Open finance LLM, beats BloombergGPT on benchmarks | Active research | Signal generator |
| FinRL | Deep RL for trading | Stable | Position sizing layer |
| Microsoft Qlib + RD-Agent | Full ML quant pipeline + autonomous research agent | MS-maintained | Long-term factor research |
| OpenBB Platform | Bloomberg Terminal alternative, MCP server | Mature | Data normalization layer |
| FinMem | Layered memory + persona trading agent | Research code | Memory upgrade for TradingAgents |
| FinAgent | Multimodal (chart-as-image) | KDD 2024 | v2 enhancement |
| Trading-R1 | RL-trained reasoning (same Tauric team) | 2025 | More credible Sharpe (2.72 vs 8.21 paper claims) |
| vectorbt | NumPy+Numba vectorized backtest | Active | Backtest engine |
| Backtrader / Zipline-reloaded | Event-driven backtest | Mature | Alternative |
| QuantConnect / LEAN | C# engine + Python API, end-to-end | Production-grade | Heavyweight alternative |
| NautilusTrader | High-perf Rust+Python | Pro-grade | If we ever go HFT |

### Turkish market reality

| Broker / Platform | Retail API? | Algorithmic Support |
|---|---|---|
| **Midas** | **NO** — no public API (despite using Alpaca internally) | None |
| Matriks IQ | Yes — C# wizard + custom code | Mature, Garanti BBVA Yatırım integrated |
| Algolab (Deniz Yatırım) | Yes — first online TR algo platform | Custom API on request, approval needed |
| İş Yatırım TradeMaster | MT4 for FX only, **no BIST equity API** | FX-only |
| Foreks | Data terminal, limited execution | 3rd-party integrations |
| Garanti BBVA eTrader | Banking-focused API, no retail equity | None for retail algo |
| Yapı Kredi / Ak Yatırım / ICBC Yatırım | No public API | None |

**Hard fact:** For BIST retail algo we have two paths: **Matriks IQ** (mature wizard, immediate access) or **Algolab** (custom API, application required). No way around it.

### Multi-agent research (cutting edge)

| Paper / System | Year | Key innovation |
|---|---|---|
| TradingAgents (Tauric) | Dec 2024 → v0.2.5 May 2026 | 7-role debate (Fund/Sent/News/Tech + Bull/Bear + Trader + Risk + PM) |
| FinAgent | KDD 2024 | Multimodal foundation agent |
| FinMem | 2023-24 | Layered memory + persona |
| TradingGPT | 2024 | Heterogeneous debate framework |
| Trading-R1 | 2025 | **RL reasoning over CoT — NVDA Sharpe 2.72** (more credible) |
| Qlib + RD-Agent | 2025 | Autonomous alpha discovery |
| TradingGoose | 2025 | TradingAgents fork with portfolio management |

---

## 2. TradingAgents deep dive (our chosen base)

### Internal architecture (v0.2.5)

```
Phase 1: Analyst pipeline      market → social → news → fundamentals
Phase 2: Investment debate     bull_researcher ⇄ bear_researcher (1 round = 2 turns)
Phase 3: Research Manager      synthesizes debate → ResearchPlan
Phase 4: Trader                ResearchPlan → TraderProposal (Buy/Hold/Sell + entry/stop/size)
Phase 5: Risk debate (3-way)   aggressive ⇄ neutral ⇄ conservative (1 round = 3 turns)
Phase 6: Portfolio Manager     final 5-tier rating (Buy/Overweight/Hold/Underweight/Sell)
```

Per decision: **~12 LLM calls, 80–110k input tokens, 8–12k output tokens, ~$0.50–0.70 at Sonnet 4.6 uncached.**

### Critical findings

1. **Memory is a markdown file, not a vector DB** — `agents/utils/memory.py` defines `TradingMemoryLog` writing to `~/.tradingagents/memory/trading_memory.md`. No embeddings. We must add pgvector for real semantic recall.

2. **Single-ticker per propagate() call** — there is no cross-asset portfolio optimization. "Portfolio Manager" is misleading; it decides per-ticker. We must add a portfolio aggregation layer.

3. **Paper backtest is unreliable** — Sharpe 8.21 in the v7 paper is almost certainly inflated by:
   - LLM training-set leakage (GPT-4o knows Jan–Mar 2024)
   - Only 7 mega-cap tickers (survivorship bias)
   - Only 3-month window (selection bias)
   - yfinance lookahead via revised fundamentals

   **Trading-R1's NVDA Sharpe 2.72 on a held-out window is the realistic upper bound.**

4. **News-sentiment latency = 3–20 minutes from publish to signal.** HFT desks react in <100ms. Implication: **TradingAgents is fundamentally swing/positional, not intraday or news-event trading.**

5. **No native Finnhub in v0.2.5** — Alpha Vantage replaced it. We add Finnhub back via custom dataflow.

### Files that must change for BIST adaptation

| Concern | Files | Effort |
|---|---|---|
| Ticker validation, suffix handling (`XU100.IS`) | `dataflows/interface.py`, `default_config.py` | Low |
| FX / currency rendering | `agents/utils/agent_utils.py`, `schemas.py` | Medium |
| KAP disclosure source | New `dataflows/kap.py` (wrap `pykap` PyPI) | Medium |
| TR news + sentiment | New `dataflows/tr_news.py` (Bloomberg HT, Mynet Finans, eksisozluk, TR Twitter) | High |
| Turkish output | Set `output_language="Turkish"`, prompts stay English (best quality) | Low |
| Trading calendar | `graph/propagation.py` — `Asia/Istanbul`, 10:00–18:00, half-day Fridays | Low |
| Lot sizes | New `dataflows/tr_utils.py` (BIST has dynamic lots) | Medium |

### Forking strategy (chosen)

**Vendored fork with quarterly upstream merge.** Keep `tradingagents/` near-pristine, add `tradingagents_tr/` namespace with TR-specific dataflows, prompts, and graph composition. See ADR-001.

---

## 3. LLM cost optimization

### Per-agent model routing

| Role | Model | Reason |
|---|---|---|
| Market analyst (technical) | Haiku 4.5 | Deterministic, no deep reasoning |
| Fundamentals analyst | Sonnet 4.6 | Arithmetic + financial comprehension |
| News analyst | Sonnet 4.6 | Long context, nuanced |
| Social/sentiment analyst | Haiku 4.5 | Volume-heavy, surface-level |
| Bull / Bear researchers | Sonnet 4.6 | Argumentation quality |
| Research Manager | **Opus 4.7** | Judges debate — quality leverage |
| Trader | Sonnet 4.6 | Plan-to-action translator |
| Risk debators (3×) | Haiku 4.5 | Heuristic stances |
| Portfolio Manager | **Opus 4.7** | Final call — quality leverage |

TradingAgents only exposes `deep_think_llm` / `quick_think_llm`. We patch `graph/trading_graph.py` to accept `Dict[str, LLM]` keyed by agent name (~50 LOC).

### Prompt caching strategy

Anthropic 5-min TTL caching applied to:

| Cache point | Static prefix | Hit ratio |
|---|---|---|
| Each analyst system prompt (~2k tokens) | 2k | 100% across all tickers same day |
| `instrument_context` (company info) | 500 | 100% within ticker |
| Market report (used by 8 downstream nodes) | 1.5k | 100% within ticker |
| All 4 analyst reports (used by RM, Trader, 2× PM) | 8k | 100% |

**Realistic savings: 70–85% of input token cost.** Combined with Batch API (50% off) for non-realtime backtests → **~90% total cost reduction**.

### Cost projection (50 tickers × 1 decision/day, 22 trading days/mo)

| Scenario | Monthly cost |
|---|---|
| All Opus 4.7, no caching, real-time | $1,650 |
| Mixed (per-agent routing), no caching | $605 |
| **Mixed + prompt caching** | **$200** |
| Mixed + caching + Batch API | $100 |
| Haiku-only + caching + Batch (quality floor) | $44 |

**Target operational cost: $200–400/mo** including data providers.

---

## 4. AWS EC2 infrastructure (rootingo / eu-west-1)

### Recommended architecture (paper trade phase)

| Component | Instance / Service | Monthly |
|---|---|---|
| LLM agent runner (I/O bound) | t4g.medium (1yr SP) | $17 |
| API + WebSocket | t4g.medium × 2 (1yr SP) | $34 |
| Data daemon (Polygon WS + Matriks) | t4g.small | $9 |
| Backtest sweep | c7g.4xlarge spot, 4h/day | $40 |
| NAT | fck-nat on t4g.nano | $4 |
| EBS gp3 | 400GB | $32 |
| **Aurora Serverless v2** (trade log) | 0.5–2 ACU avg | $45 |
| **ElastiCache Redis** | cache.t4g.micro | $13 |
| TimescaleDB (tick data) | on data daemon EBS | included |
| S3 (parquet historical) | 50GB Std + 200GB IT | $5 |
| ALB | 1 ALB | $18 |
| API Gateway WebSocket | 5k users, 10M msgs/mo | $12 |
| Lambda (ingestion) | 5M invocations | $4 |
| SQS / SNS / EventBridge | combined | $3 |
| CloudWatch | logs + alarms | $15 |
| VPC interface endpoints (Secrets, ECR) | 2 endpoints | $14 |
| Cognito | <50k MAU | Free |
| Misc (Route53, transfer, secrets) | | $9 |
| **Total** | | **~$274/mo** |

**Budget: <$500/mo** → $226/mo headroom for spikes.

### Storage decisions

- **TimescaleDB on EC2** beats Amazon Timestream at >50M writes/mo. Polygon free tier streams 100M+/day. Decision: TimescaleDB.
- **Aurora Serverless v2** beats provisioned RDS for our bursty trade-log workload (0.5 ACU floor).
- **ElastiCache Redis** for real-time quotes pub/sub + agent state idempotency.

### EKS reuse alternative

The user already has `wptg-ops-services` EKS. Deploying as a namespace there would save ~$200/mo (compute absorbed by existing Karpenter spot pool) and 10 days of work.

**Decision (ADR-002):** Build paper-trade phase on EC2 as user explicitly requested. EKS namespace is a future optimization, not day-one.

### Architecture diagram

```
                    ┌──────────────────────────────────────────────┐
                    │              Mobile (iOS / Android)          │
                    └──────────────┬─────────────────┬─────────────┘
                                   │ HTTPS           │ WSS
                                   ▼                 ▼
                          ┌────────────────┐  ┌────────────────┐
                          │   WAFv2 + ALB  │  │ APIGW WebSocket│
                          └────────┬───────┘  └────────┬───────┘
                                   │ Cognito JWT       │
                                   ▼                   ▼
   ┌───────────── VPC 10.20.0.0/16  eu-west-1 (rootingo) ──────────────────────┐
   │                                                                            │
   │   ┌──────────┐                              ┌──────────┐                   │
   │   │ fck-NAT  │                              │   ALB    │                   │
   │   └────┬─────┘                              └────┬─────┘                   │
   │        │                                         │                         │
   │        │     ┌──────────────────┐         ┌──────▼───────┐                 │
   │        │     │  Data Daemon     │         │  API EC2 x2  │                 │
   │        │     │  + TimescaleDB   │         │  t4g.medium  │                 │
   │        │     └────┬─────────────┘         └──────┬───────┘                 │
   │        │          │ WS Polygon/Matriks           │                         │
   │        │     ┌────▼───────┐                      │                         │
   │        │     │ ElastiCache│ ◄─── pub/sub ───── ┌▼───────────────────────┐  │
   │        │     │   Redis    │                    │  Agent Runner          │  │
   │        │     └────────────┘                    │  ┌───────────────────┐ │  │
   │        │     ┌────────────┐                    │  │ 7-Agent Pipeline  │ │  │
   │        │     │  Aurora    │ ◄── trade log ────►│  │ fund│sent│news   │ │  │
   │        │     │ Serverless │                    │  │ tech│bull│bear   │ │  │
   │        │     └────────────┘                    │  │      trader       │ │  │
   │        │                                       │  └─────────┬─────────┘ │  │
   │        │     ┌────────────────┐    ┌───────┐   └────────────┼───────────┘  │
   │        │     │ Spot Backtest  │    │  SQS  │ ◄─ signals ────┘              │
   │        │     │ c7g.4xlarge    │    └───┬───┘                               │
   │        │     │ (nightly 4h)   │        │                                   │
   │        │     └────────────────┘        ▼                                   │
   │        │                          ┌─────────┐                              │
   │        │                          │ Execute │ ──► Alpaca / Matriks         │
   │        │                          └─────────┘                              │
   └────────┼───────────────────────────────────────────────────────────────────┘
            │ egress
            ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │ Polygon │ Finnhub │ Reddit │ X │ SEC EDGAR │ KAP │ Anthropic    │
   └─────────────────────────────────────────────────────────────────┘
```

---

## 5. Mobile app architecture

### Framework decision: React Native + Expo

| Option | MVP weeks | Verdict |
|---|---|---|
| **React Native + Expo SDK 52** | 4–6 | **Chosen.** TS shared with backend, EAS Build/Update, TradingView lib first-class |
| Flutter | 5–7 | Rejected — Dart one-off, no TradingView official |
| Native (Swift + Kotlin) | 10–14 | Rejected — 2× codebase kills solo dev |
| .NET MAUI | 8–10 | Rejected despite C# familiarity — bare fintech ecosystem, weak chart options |
| Capacitor + React | 3–5 | Rejected — feels web-y, App Store risk |
| Tauri Mobile | n/a | Rejected — still beta in 2026 |

### Stack

```
Mobile:    React Native 0.76 + Expo SDK 52 + TypeScript (strict)
State:     Zustand + TanStack Query v5
Storage:   expo-secure-store (tokens) + expo-sqlite + Drizzle ORM (cache)
Charts:    TradingView Lightweight Charts (Apache 2.0) in react-native-webview
Realtime:  WebSocket + Protobuf
Auth:      AWS Cognito + expo-local-authentication + TOTP MFA
Network:   ky + react-native-ssl-pinning
Push:      expo-notifications → SNS → APNs/FCM
i18n:      i18next + react-i18next (TR/EN)
Payments:  RevenueCat (StoreKit2 + Play Billing)
Crash:     Sentry
Analytics: PostHog (self-hosted on existing EKS)
Build:     EAS Build + EAS Update (OTA)
CI:        GitHub Actions → EAS
```

### Critical UX/security decisions

1. **Broker API keys NEVER on device.** Mobile app authenticates as a user (Cognito JWT); backend holds Alpaca/Matriks keys in AWS Secrets Manager. Non-negotiable.
2. **Auto-execute toggle behind biometric + 24h delay before first auto-trade.** SMS confirm required to enable.
3. **Trade approval TTL countdown.** Manual-mode trades auto-reject after N seconds if user doesn't act.
4. **Streaming LLM reasoning** (SSE/WS) — users see agents "think" as the wow factor.
5. **Colors: green=up / red=down.** Provide colorblind-safe (blue/orange) variant.
6. **Widgets:** iOS WidgetKit + Android Glance for portfolio P&L. Watch app deferred to v2.

---

## 6. Risk management

### Position sizing

- **Fractional Kelly (0.25x)** — never full Kelly (empirically blows up)
- **ATR-based** — `risk_per_trade=0.5%`, `stop=2*ATR`
- **Volatility targeting** — target 15% annualized portfolio vol
- Hard caps: 10% per name, 30% per sector, 1.5x gross leverage

### Stop-loss strategy

| Type | Use case |
|---|---|
| Fixed % | Mean reversion, short hold |
| ATR trailing (3×ATR) | Trend following |
| Time-based (N bars) | Catalysts that didn't materialize |
| Vol-adjusted | Multi-asset book |

### Circuit breakers

```python
LIMITS = {
    "max_position_pct": 0.10,
    "max_sector_pct":   0.30,
    "max_correlated":   3,        # max 3 names with |rho| > 0.7
    "max_daily_dd":     0.03,     # halt at -3% intraday
    "max_consec_losses": 5,
    "max_gross_exposure": 1.5,
}
```

### Kill switch pattern

Mobile app writes to Firestore/DynamoDB doc `kill_switches/{user_id}`; agent polls every 5s. Three states:
- `RUN`
- `PAUSE_NEW` (no new entries, manage existing)
- `FLATTEN_ALL` (market-exit all positions)

### Backtest pitfalls — defenses

| Pitfall | Defense |
|---|---|
| Lookahead bias | Always `t-1` close → `t` open; assert `signal_time < trade_time` |
| Survivorship bias | Point-in-time universe (CRSP / Polygon historical tickers); include delisted |
| Data snooping | Walk-forward CV, not single 70/30; reserve final 20% as untouched holdout |
| Transaction costs | Alpaca $0 commission but model 5–15 bps slippage; SEC/TAF fees on sells |
| Selection bias | Run on 2007–2009, 2020, 2022 bear regimes — not just bull |
| Multiple testing | Bonferroni or deflated Sharpe (Bailey & López de Prado) |

### Paper → Live gate

| Gate | Minimum |
|---|---|
| Paper duration | 90 days minimum across at least one regime change |
| Live paper Sharpe | > 1.0 net of modeled costs |
| Max DD in paper | < 15% |
| Initial live capital | $2–5K |
| Position size in live | Start at 25% of paper sizing |
| Scale-up trigger | +30 days live with Sharpe > 0.8 and DD < paper max |

---

## 7. Regulatory landscape

### Turkey (SPK)

- **Personal use trading own capital algorithmically: legal.**
- Crosses into regulated activity (SPKn m.37-39) when you:
  - Manage third-party capital → `portföy yöneticiliği` (PYŞ license, min capital ~₺3M)
  - Give personalized investment advice → `yatırım danışmanlığı` (III-55.1 license)
  - Operate robo-advisor with discretionary execution
- **Grey zone:** "Genel yatırım tavsiyesi" (non-personalized commentary) without license is permitted. Personalization OR auto-execution → license required.
- **Tax:** BIST stocks stopaj = 0% for TR residents (current regime). Foreign stocks (Alpaca/US) taxable as `değer artış kazancı`, declared via annual `gelir vergisi beyannamesi` (15–40%).

### United States (SEC / FINRA)

- **Personal algo trading: legal, no license needed.**
- **PDT rule change (June 4, 2026):** SR-FINRA-2025-017 eliminates the 4-trades-in-5-days PDT designation and $25K minimum. Replaced by intraday margin monitoring under Rule 4210.
- **Wash sales (IRC §1091):** AI agents that cycle in/out of same ticker trip 30-day window constantly. Alpaca rejects intra-account wash patterns at the API; cross-broker tracking is our responsibility.
- **RIA registration triggers** (Investment Advisers Act §202(a)(11)):
  - <$100M AUM → state RIA
  - ≥$110M AUM → SEC RIA
  - **Internet Adviser Exemption** (amended March 2025): requires interactive website + advice generated exclusively by algorithm with no human intervention + full Form ADV

### Data licensing

| Provider | Personal use | Redistribute to app users |
|---|---|---|
| Polygon.io Individuals | OK | **NO** |
| Polygon.io Business | OK | Authorized Users only |
| Finnhub | Free non-commercial; paid for commercial | Requires Enterprise |
| SEC EDGAR | OK | OK (public domain, 10 req/s) |
| Reddit API | OK with auth | Restricted post-2023 |
| X API | Paid tiers only | Per tier ToS |
| Matriks / BIST | OK with sub | **NO without BIST written permission** |

### Monetization paths (if we ever ship to users)

| Model | Required licenses |
|---|---|
| Educational/general commentary | None |
| Signals service (impersonal, regular) | TR: arguably `genel yatırım tavsiyesi`; US: likely publisher exemption (Lowe v. SEC) |
| **Personalized signals** | TR: `yatırım danışmanlığı`; US: RIA |
| **Auto-trade for users** | TR: PYŞ; US: RIA + broker-dealer partnership (Alpaca Broker API) |

### KVKK / GDPR

- KVKK Article 9 (Jun 2024 amendment): cross-border transfer requires adequacy OR SCCs OR explicit consent
- TR has no US adequacy decision → AWS US/Alpaca transfers need SCCs or explicit informed consent
- Right to deletion: `DELETE /users/{id}` purges trade history (keep tax records 5 years per VUK m.253)

### DO / DON'T (high-level)

**DO**
- Fractional Kelly (0.25x), ATR sizing, hard portfolio caps from day one
- 90+ days paper across one volatile regime before live capital
- Remote kill switch before first live order
- Wash-sale tracking across 30-day windows
- Start live with 25% of paper sizing
- Frame any future commercial product as "general commentary / publisher" with strong disclaimers until licensed
- Point-in-time universe data in backtests

**DON'T**
- Full Kelly
- yfinance current S&P 500 universe (survivorship trap)
- Redistribute raw Polygon/Finnhub/Matriks data to app users without commercial license
- Personalize recommendations without SPK `yatırım danışmanlığı` or US RIA
- Auto-execute trades for other users without proper licensing
- Cherry-pick backtest start dates — always include 2008, 2020, 2022

---

## 8. Realistic expectations

**Will this actually make money?**

- Published TradingAgents Sharpe 8.21 is almost certainly inflated by LLM training-set leakage on the test window.
- Trading-R1's NVDA Sharpe 2.72 on held-out data is the realistic upper bound.
- News-sentiment latency (3–20 min) means **this is a swing/positional strategy, not intraday**.
- Capacity is fine for personal/small-fund AUM (<$1M slippage negligible). Above $10M needs TWAP/VWAP execution.
- Most quant strategies that look great in backtest fail in production.

**Honest expected value:**
- The infrastructure, agent stack, and skills we build are real and reusable.
- The "passive income machine" is a meme — at best we capture marginal alpha, at worst we match SPY.
- Treat this as **a research vehicle that pays for itself by saving on third-party tools and growing our AI/agent/infra skills**, not as a primary income stream.

---

## Sources

Full source list preserved in each ADR. Headline references:

- TradingAgents — https://github.com/TauricResearch/TradingAgents, arXiv 2412.20138
- Trading-R1 — arXiv 2509.11420
- FinMem — https://github.com/pipiku915/FinMem-LLM-StockTrading, arXiv 2311.13743
- OpenBB Platform — https://github.com/OpenBB-finance/OpenBB
- vectorbt — https://vectorbt.dev
- Anthropic prompt caching — https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- FINRA PDT change SR-FINRA-2025-017 — https://www.finra.org/sites/default/files/2025-12/SR-FINRA-2025-017.pdf
- SPK Portföy Yönetimi III-55.1 — https://www.prmfinans.com/iii-55-1-portfoy-yonetimi
- KVKK Yurt Dışı Aktarım Rehberi — https://www.kvkk.gov.tr/Icerik/8142/
- Polygon.io Terms — https://polygon.io/legal/individuals-terms-of-service
- Alpaca Algo Trading — https://alpaca.markets/algotrading
- Matriks IQ — https://www.matriksdata.com/website/urunlerimiz/kullanici-platformlari/algoritmik-islem-urunleri
- Algolab (Deniz Yatırım) — https://www.denizyatirim.com/AlgorithmicOperations
- PyKAP — https://github.com/cemsinano/pykap
