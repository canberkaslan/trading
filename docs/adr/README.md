# Architecture Decision Records

ADRs capture the reasoning behind significant architectural choices. Each one names:
- Status (Proposed / Accepted / Superseded)
- Context (what problem)
- Decision (what we chose)
- Alternatives considered (with rejection rationale)
- Consequences (what we accept by choosing this)

| # | Title | Status |
|---|---|---|
| [001](001-multi-agent-fork.md) | Fork TradingAgents v0.2.5 as the agent core | Accepted |
| [002](002-aws-ec2-deployment.md) | Deploy on AWS EC2 (rootingo / eu-west-1) with EKS reuse as future path | Accepted |
| [003](003-mobile-react-native.md) | React Native + Expo for mobile, .NET MAUI rejected | Accepted |
| [004](004-data-providers.md) | Polygon + Finnhub + SEC EDGAR (US), Matriks IQ + KAP (BIST) | Accepted |
| [005](005-risk-management.md) | Fractional Kelly (0.25x) + ATR sizing + remote kill switch | Accepted |
| [006](006-llm-cost-optimization.md) | Per-agent LLM routing + prompt caching + Batch API | Accepted |
| [007](007-regulatory-stance.md) | Personal use only initially; commercial path requires licensing | Accepted |
