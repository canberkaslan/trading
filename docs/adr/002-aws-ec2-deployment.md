# ADR-002: Deploy on AWS EC2 (rootingo / eu-west-1)

**Status:** Accepted
**Date:** 2026-05-22

## Context

Need a cloud deployment target for:
- Agent runner (LLM orchestration, I/O bound)
- Data ingestion daemon (persistent WebSockets to Polygon + Matriks)
- API + WebSocket layer for mobile
- Backtest sweeps (compute-heavy, bursty)
- Trade log + agent decisions (durable storage)

User explicitly requested **EC2 on the rootingo AWS profile** (BetCoreWin account `674594306499`, eu-west-1).

Existing infra: three EKS clusters (dev-wptg, stg-wptg-services, prod-wptg-services on a different account), Prometheus/Grafana/Tempo stack on `wptg-ops-services`.

## Decision

**Deploy paper-trade phase on EC2 (rootingo / eu-west-1).** Reuse existing EKS observability stack via remote-write Prometheus and Grafana datasource. Plan migration to dedicated AWS account when going live with real capital.

Phase-0 instance fleet:

| Role | Instance | Why |
|---|---|---|
| Agent runner | t4g.medium (1yr SP) | I/O bound, Graviton -20% cost |
| API + WebSocket | t4g.medium × 2 | AZ-spread, sticky WS |
| Data daemon | t4g.small | Persistent Polygon/Matriks sockets + TimescaleDB |
| Backtest | c7g.4xlarge spot, 4h/day | NumPy-bound, embarrassingly parallel |
| NAT | t4g.nano fck-nat | $4 vs $32 managed |

Storage:

| Store | Choice |
|---|---|
| Historical OHLCV | S3 Standard + Intelligent-Tiering, Glacier IR after 90 days |
| Trade log | Aurora Serverless v2 (0.5–2 ACU) |
| Real-time quotes | ElastiCache Redis cache.t4g.micro |
| Tick data | TimescaleDB on data daemon EBS gp3 200GB |

## Alternatives considered

| Option | Why rejected (for Phase 0) |
|---|---|
| **EKS reuse (`wptg-ops-services` namespace)** | Saves ~$200/mo and 10 days of work. **Strong second choice.** Rejected only because user explicitly said EC2; promote to this when scale demands |
| ECS Fargate | $36/mo per task vs $48 for 2× EC2 — EC2 wins at this scale |
| Lambda + API Gateway for all REST | Cold start (50–200ms) bad for trading UI; flat $48 EC2 beats per-req pricing at >1M req/mo |
| Amazon Timestream for tick data | $0.50/M writes; Polygon streams 100M+/day; TimescaleDB on EC2 = $20 vs $50+ |
| Managed NAT Gateway | $32/mo + egress charges vs $4 fck-nat at paper-phase blast radius |
| Kinesis for data streams | Adds $30/mo for zero benefit at single-consumer scale |

## When EC2 wins (defer EKS to a later phase)

- **Live trading phase** will be on a separate AWS account anyway (regulatory isolation)
- **GPU LLM hosting** if we ever go that path would taint EKS pool anyway
- **Compliance**: separate VPC = clean blast-radius story

## When to migrate to EKS

- If paper-phase compute >$100/mo and stable workload
- If we add multiple users / SaaS mode (replicas, HPA help)
- If the existing Karpenter spot pool has consistent free capacity

## Consequences

### Cost (paper trade phase)

| Item | Monthly |
|---|---|
| EC2 (agent + API×2 + data) with 1yr Savings Plans | $60 |
| EC2 backtest spot (4h/day) | $40 |
| NAT instance | $4 |
| EBS gp3 (400GB total) | $32 |
| Aurora Serverless v2 | $45 |
| ElastiCache Redis | $13 |
| S3 | $5 |
| ALB | $18 |
| API Gateway WebSocket | $12 |
| Lambda + SQS + SNS + EventBridge | $7 |
| CloudWatch | $15 |
| VPC interface endpoints (Secrets, ECR) | $14 |
| Misc | $9 |
| **Total** | **~$274/mo** |

### Cost levers

If we blow past $500/mo budget:
1. Drop API to single t4g.medium (-$17), accept single-AZ in paper
2. Move Aurora → RDS db.t4g.micro provisioned (-$31)
3. Run backtest weekly not nightly (-$30)

### Operational

- AMI patching: SSM Patch Manager (no SSH bastion)
- Logs: CloudWatch → S3 export
- Metrics: CloudWatch + ADOT collector → remote_write to existing Prometheus
- Alerts: CloudWatch alarm → SNS → existing Slack webhook
- Deployment: GitHub Actions → ECR → SSM run-command

### Terraform state

- S3 bucket: `betcorewin-tfstate`
- Path: `ai-trader/<env>/terraform.tfstate`
- DynamoDB lock: existing table reused
- Profile: `rootingo`

## Architecture diagram

See [ARCHITECTURE.md](../ARCHITECTURE.md).

## Sources

- Research output preserved in `docs/RESEARCH.md` §4
- AWS pricing as of 2026-05-22, eu-west-1
