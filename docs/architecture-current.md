# Current architecture (live)

What is actually deployed and running today — distinct from the planned AWS
design in [`ARCHITECTURE.md`](ARCHITECTURE.md). The live system runs entirely
on a single Hetzner box, with an LLM debate-plus-council decision engine that
learns from its own outcomes (reflection-memory), a deterministic risk layer,
and a React Native app.

> Status: paper-trading eval phase. No real money until the eval scorecard
> clears the ADR-005 gate. Forward paper is the only clean test of the signal.

## System diagram

```mermaid
flowchart TD
  classDef ext  fill:#f1f1f0,stroke:#b9b7b2,color:#4a4844
  classDef box  fill:#e7f0fb,stroke:#5b8def,color:#1c3f72
  classDef key  fill:#dbe8fb,stroke:#3a6fd0,color:#1c3f72
  classDef learn fill:#f3e9fb,stroke:#a855f7,color:#6b21a8

  subgraph DATA["1 · Data sources"]
    direction LR
    POLY[Polygon<br/>price · technicals]:::ext
    SEC[SEC EDGAR<br/>fundamentals]:::ext
    FH[Finnhub<br/>analyst · earnings]:::ext
    ST[StockTwits<br/>retail sentiment]:::ext
    FRED[FRED<br/>macro]:::ext
  end

  subgraph ENGINE["2 · Decision engine — Hetzner box · weekday 22:30 UTC"]
    AN[4 analysts<br/>market · fundamentals · news · sentiment<br/>Sonnet 4.6]:::box
    DEB[Bull and Bear debate<br/>Sonnet 4.6]:::box
    RM[Research Manager<br/>judge · Opus 4.8]:::key
    TR[Trader + 3 risk debators]:::box
    PM[Portfolio Manager<br/>house view · Opus 4.8]:::key
    subgraph COUNCIL["LLM council — cross-family review"]
      direction LR
      VOT[DeepSeek · GLM · qwen local]:::box
      CH[Chair · Opus 4.8]:::key
    end
    FIN[Final rating + confidence]:::key
    RISK[Risk layer<br/>sizing · stop · TP · circuit breaker · kill switch]:::box
    EX[Executor<br/>bracket order]:::box
    MEM[Reflection-memory<br/>outcome to lesson to inject]:::learn
  end

  ALP[Alpaca · paper broker<br/>NASDAQ / NYSE]:::ext

  subgraph DELIVERY["3 · Delivery"]
    direction LR
    API[FastAPI backend<br/>/portfolio /eval /analyze /prices]:::box
    CF[Cloudflare tunnel<br/>trader.fusapp.com · HTTPS]:::ext
    APP[Mobile app — Xiaomi<br/>Portfolio · Sor · Charts · Agents · Settings]:::box
  end

  subgraph VALID["4 · Validation · eval-safe"]
    direction LR
    BT[Backtest harness<br/>risk · baseline · PIT-LLM · param-opt]:::box
    EV[Eval scorecard<br/>Sharpe / MaxDD · GO/NO-GO]:::box
    LOOP[Daily loop scheduler<br/>improve + test + report]:::learn
  end

  DATA --> AN --> DEB --> RM --> TR --> PM --> COUNCIL
  VOT --> CH --> FIN
  PM --> FIN --> RISK --> EX --> ALP
  MEM -. feedback .-> PM
  ALP -. outcome .-> MEM
  ALP --> API --> CF --> APP
  EX --> EV
  BT --> EV
```

A static render is also kept at [`architecture-current.svg`](architecture-current.svg).

## Layers

| Layer | What runs | Notes |
|---|---|---|
| **Data** | Polygon, SEC EDGAR, Finnhub, StockTwits, FRED | External APIs, fetched fresh each run |
| **Decision engine** | 7-agent pipeline → LLM council → risk layer → executor | On the Hetzner box; reflection-memory learns from outcomes |
| **Delivery** | FastAPI → Cloudflare tunnel → mobile | HTTPS, reachable on any network |
| **Validation** | backtest harness, eval scorecard, daily loop | Deterministic backtests are leakage-free; LLM backtests are indicative only |

## Models

| Role | Model |
|---|---|
| Research Manager, Portfolio Manager, council chair | Opus 4.8 |
| Analysts, researchers, trader | Sonnet 4.6 |
| Council voters | DeepSeek, GLM (OpenRouter) + qwen2.5 (local ollama) |
| Heuristic agents (cost-opt branch, not deployed) | Haiku 4.5 |

## Where it runs

Everything executes on one Hetzner box (`167.233.102.179`): the systemd timer,
the agent pipeline, ollama, the FastAPI backend, the cloudflared tunnel, SQLite,
the reflection-memory store, and the backtest harness. The box calls out to the
LLM providers, the data APIs, and Alpaca. The phone is just the UI.

## Honesty notes

- LLM backtests suffer information leakage (the model trained on the test
  window) — indicative only; forward paper is the clean test.
- No demonstrated edge yet; the eval scorecard is the gate before real capital.
  See [`adr/005-risk-management.md`](adr/005-risk-management.md).
