# Trading — AI-Powered Multi-Agent Trading System

Multi-agent LLM-driven autonomous trading system targeting **US stocks (via Alpaca)** and **Turkish BIST (via Matriks IQ / Algolab)**. Mobile-first delivery via React Native + Expo. Deployed on AWS EC2 in `eu-west-1` (BetCoreWin account, profile `rootingo`).

> **Status:** Phase 0 — Architecture & scaffolding. Not yet trading any capital.
>
> **Disclaimer:** Not investment advice. Past performance does not guarantee future results. Personal-use research project. See [docs/adr/007-regulatory-stance.md](docs/adr/007-regulatory-stance.md).

## High-level architecture

```
Mobile (React Native + Expo)
        │ HTTPS + WSS
        ▼
   ALB + WAF / API Gateway WebSocket
        │
        ▼
   FastAPI on EC2 (t4g.medium)
        │
        ▼
   Agent Runner (TradingAgents fork, 7-role LLM debate)
        │              │              │
        ▼              ▼              ▼
   Alpaca API     Matriks IQ      KAP / SEC EDGAR
   (US exec)     (BIST exec)     (filings)
```

## Repository layout

| Path | Purpose |
|---|---|
| `agent/` | Python — TradingAgents fork (`tradingagents_us/`) + FastAPI mobile backend |
| `infra/` | Terraform — VPC, EC2, RDS Aurora, ElastiCache, ALB, API Gateway WebSocket |
| `mobile/` | React Native + Expo app (iOS + Android) |
| `docs/` | Architecture, ADRs, research synthesis |
| `scripts/` | Setup / bootstrap / one-off scripts |

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — Full system design
- [Research](docs/RESEARCH.md) — Synthesis of 4 deep-research streams (landscape, infra, mobile, risk+regulatory)
- [Roadmap](docs/ROADMAP.md) — Phased delivery plan
- [ADRs](docs/adr/) — Architecture Decision Records

## Tech stack

| Layer | Choice |
|---|---|
| Agent framework | TradingAgents v0.2.5 fork (Apache-2.0) + Turkish/BIST adaptation |
| LLM | Claude Opus 4.7 (managers), Sonnet 4.6 (researchers), Haiku 4.5 (risk debators) — per-agent routing |
| US data | Polygon.io + Finnhub + SEC EDGAR + Reddit/X sentiment |
| BIST data | Matriks IQ + KAP (kap.org.tr) + TCMB EVDS |
| US execution | Alpaca paper → live |
| BIST execution | Matriks IQ / Algolab (Deniz Yatırım) |
| Backend | Python 3.12, FastAPI, LangGraph, uv |
| Infra | AWS EC2 + Aurora Serverless v2 + ElastiCache Redis + S3 + API Gateway WS |
| Mobile | React Native + Expo SDK 52, TypeScript strict, Zustand + TanStack Query, TradingView Lightweight Charts |
| CI/CD | GitHub Actions → ECR → SSM deploy |
| Observability | CloudWatch + remote-write to existing Prometheus/Grafana on EKS |

## Quick start

```bash
# Agent (local dev)
cd agent && uv venv && uv pip install -e .
cp .env.example .env  # Fill in API keys
uv run python -m tradingagents_us.graph.tr_setup --ticker AAPL --paper

# Infra (dev environment)
cd infra/envs/dev
terraform init -backend-config=../../backend.hcl
terraform plan -var-file=dev.tfvars

# Mobile
cd mobile/app && pnpm install && pnpm start
```

## License

MIT. See [LICENSE](LICENSE).
