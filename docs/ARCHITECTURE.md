# Architecture

Full technical architecture for the AI trading agent system. Companion to [RESEARCH.md](RESEARCH.md) (the why) and the [ADRs](adr/) (individual decisions).

## System overview

```
   ┌─────────────────────────────────────────────────────────────────┐
   │                        DATA SOURCES                              │
   │  Polygon │ Finnhub │ Reddit │ X │ SEC EDGAR │ KAP │ Matriks IQ  │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────────────┐
   │                       INGESTION LAYER                            │
   │  EventBridge cron → Lambda → S3 parquet (EOD batch)             │
   │  Data Daemon EC2: Polygon WS + Matriks IQ socket → Redis pub/sub │
   │  Lambda + Playwright: KAP scrape (TR public disclosures)         │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────────────┐
   │                       STORAGE LAYER                              │
   │  S3 (parquet historical) │ TimescaleDB (tick) │ Redis (quotes)  │
   │  Aurora Serverless v2 (trade log + agent decisions JSONB)        │
   │  pgvector on Aurora (semantic memory, upgrade from markdown log) │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────────────┐
   │                       AGENT LAYER                                │
   │  TradingAgentsGraph (LangGraph) on t4g.medium                    │
   │  ┌─────────────────────────────────────────────────────────┐    │
   │  │  Phase 1: Analysts (concurrent)                          │    │
   │  │    market | sentiment | news | fundamentals             │    │
   │  │  Phase 2: Bull ⇄ Bear debate                            │    │
   │  │  Phase 3: Research Manager → ResearchPlan               │    │
   │  │  Phase 4: Trader → TraderProposal                       │    │
   │  │  Phase 5: Risk debate (aggressive ⇄ neutral ⇄ conservative)│  │
   │  │  Phase 6: Portfolio Manager → 5-tier decision           │    │
   │  └─────────────────────────────────────────────────────────┘    │
   │  Per-agent LLM routing: Opus 4.7 (managers) / Sonnet 4.6 / Haiku│
   │  Prompt caching: ~80% input cost reduction within 5-min TTL      │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────────────┐
   │                       RISK LAYER                                 │
   │  Position sizing: fractional Kelly (0.25x) + ATR + vol target   │
   │  Stop-loss: trailing ATR / fixed % / time-based                  │
   │  Circuit breakers: drawdown, vol z-score, API error rate         │
   │  Kill switch: DynamoDB flag (mobile-controlled) polled @5s       │
   │  Portfolio limits: max position/sector/correlation/leverage      │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────────────┐
   │                       EXECUTION LAYER                            │
   │  SQS trade-signal queue → Executor                               │
   │  US: Alpaca paper → live (via Broker API)                        │
   │  TR: Matriks IQ socket / Algolab REST (paper → live)             │
   │  Slippage modeling: 5–15 bps + SEC TAF fees                      │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────────────┐
   │                       API LAYER                                  │
   │  FastAPI on EC2 t4g.medium × 2 (AZ-spread, behind ALB)          │
   │  REST endpoints: portfolio, orders, agents, backtest             │
   │  API Gateway WebSocket: quote stream + agent reasoning stream    │
   │  Cognito JWT (ALB Lambda authorizer)                             │
   └──────────────────────────┬──────────────────────────────────────┘
                              │
   ┌──────────────────────────▼──────────────────────────────────────┐
   │                       MOBILE LAYER                               │
   │  React Native + Expo SDK 52 (iOS + Android)                      │
   │  TradingView Lightweight Charts (WebView)                        │
   │  Zustand + TanStack Query + expo-sqlite (Drizzle)                │
   │  expo-local-authentication (biometric) + TOTP MFA                │
   │  Push: expo-notifications → SNS → APNs/FCM                       │
   │  i18n: TR + EN (i18next)                                         │
   └─────────────────────────────────────────────────────────────────┘

   ┌─────────────────────────────────────────────────────────────────┐
   │   OBSERVABILITY (reused from existing EKS wptg-ops-services)     │
   │   CloudWatch + ADOT → remote_write → Prometheus → Grafana        │
   │   Tempo (traces) │ Slack alerts                                  │
   └─────────────────────────────────────────────────────────────────┘
```

## Component contracts

### Agent → Risk

```python
# tradingagents_tr/schemas.py
class AgentDecision(BaseModel):
    ticker: str
    market: Literal["US", "BIST"]
    rating: Literal["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    entry_price: float
    stop_loss: float
    take_profit: float | None
    suggested_size_pct: float       # 0.0–1.0 of portfolio
    reasoning: dict[str, str]       # per-agent reasoning blobs
    quote_currency: Literal["USD", "TRY"]
    timestamp_utc: datetime
```

### Risk → Executor

```python
class TradeOrder(BaseModel):
    ticker: str
    market: Literal["US", "BIST"]
    side: Literal["BUY", "SELL"]
    quantity: int                    # shares (int — BIST has lot rules)
    order_type: Literal["MARKET", "LIMIT"]
    limit_price: float | None
    stop_loss: float
    risk_approved: bool              # False if any circuit breaker tripped
    rejection_reasons: list[str]
```

### Executor → API → Mobile

```python
class OrderUpdate(BaseModel):
    order_id: str
    status: Literal["PENDING", "ACCEPTED", "PARTIAL", "FILLED", "REJECTED", "CANCELLED"]
    filled_qty: int
    avg_fill_price: float | None
    slippage_bps: float | None
    timestamp_utc: datetime
```

WebSocket frames are Protobuf-encoded; envelope:

```protobuf
message WSFrame {
  string type = 1;        // "quote", "order_update", "agent_reasoning", "kill_switch"
  bytes payload = 2;      // type-specific message
  int64 seq = 3;          // monotonic per-connection
  int64 ts_unix_ms = 4;
}
```

## Trading calendar

| Market | Timezone | Open | Close | Special |
|---|---|---|---|---|
| US (NYSE/NASDAQ) | America/New_York | 09:30 | 16:00 | Pre/post limited support |
| BIST | Europe/Istanbul | 10:00 | 18:00 | Half-day Fridays after religious holidays |

Decision schedules (EventBridge cron in UTC):
- US tickers: 22:30 UTC (30 min post-close, after Polygon EOD reconciliation)
- BIST tickers: 16:00 Europe/Istanbul → 13:00 UTC

## Multi-environment topology

| Env | Purpose | AWS Account | Region | Capital |
|---|---|---|---|---|
| dev | Local + single t4g.small | rootingo (674594306499) | eu-west-1 | None (mocks) |
| paper | Full stack, Alpaca paper key | rootingo | eu-west-1 | Paper |
| live | Future: separate AWS account | TBD | eu-west-1 | Real |

State buckets: `s3://betcorewin-tfstate/ai-trader/<env>/terraform.tfstate`

## Security

- **Secrets:** AWS Secrets Manager, separate secret per provider (Polygon, Finnhub, Anthropic, Alpaca, Reddit, X, Matriks). Rotation where supported.
- **IAM:** Instance profiles per role with least-privilege (agent EC2 reads only its needed S3 prefix + specific Secret ARNs).
- **KMS:** Customer-managed CMK per env, aliased `alias/ai-trader-<env>`.
- **Network:** Private subnets only for compute; SSM Session Manager (no bastion); VPC flow logs to S3.
- **ALB:** WAFv2 (AWS managed Core + Known-bad-inputs + Bot Control), rate-limit 2000 req/5min per IP.
- **Mobile auth:** Cognito JWT, ALB Lambda authorizer validates `aud`/`iss`, scope-checks `/trade/*`.
- **Broker keys NEVER on mobile.** Backend proxies all order placement.
- **Pinning:** Mobile cert-pins intermediate CA (not leaf — rotation safety).

## Observability

| Layer | Tool | Reused? |
|---|---|---|
| Logs | CloudWatch Logs (1-day retention) → S3 export | New |
| Metrics | CloudWatch + ADOT → remote_write to existing Prometheus | **Yes** |
| Dashboards | Existing Grafana on EKS | **Yes** |
| Tracing | OpenTelemetry → existing Tempo/Jaeger | **Yes** |
| Alerting | CloudWatch Alarm → SNS → existing Slack webhook | **Yes** |
| Crash (mobile) | Sentry | New |
| Analytics (mobile) | PostHog (self-hosted on existing EKS) | **Yes** (new instance) |

Key alerts:
- Drawdown > 2% intraday → Slack
- Agent decision latency p99 > 30s → Slack
- Alpaca API 5xx rate > 1% → PagerDuty
- WebSocket disconnect from Polygon > 5min → page
- Kill switch toggled → audit log + Slack confirm

## CI/CD

```
GitHub PR
  ├─ trivy scan (image + Terraform)
  ├─ terraform plan (PR comment)
  ├─ pytest (agent unit tests)
  └─ EAS build preview (mobile)

GitHub main push
  ├─ Build → ECR eu-west-1 (immutable tags = git SHA)
  ├─ Deploy dev (SSM run-command pulls new image)
  ├─ Manual approval gate
  └─ Deploy paper (rolling, 1-at-a-time across 2 EC2)
```

Mobile distribution:
- EAS Build → TestFlight (internal 100 testers, no review)
- EAS Update OTA for JS-only fixes (Apple permits non-functional changes)

## Out of scope (Phase 0)

Explicitly excluded until paper-trade validation succeeds:
- HFT / sub-second strategies (latency mismatch)
- Options trading (separate risk framework)
- Crypto (regulatory complexity, user opted out)
- Multi-user / SaaS mode (requires RIA/PYŞ licensing)
- GPU LLM hosting (Anthropic API is cheaper at our volume)
- Watch app (LOE too high vs user value)
