# ADR-001: Fork TradingAgents v0.2.5 as the agent core

**Status:** Accepted (BIST adaptation section superseded by [ADR-008](008-scope-us-only.md))
**Date:** 2026-05-22

## Context

We need a multi-agent LLM orchestration framework that can:
- Run multiple specialized agents (fundamental, technical, sentiment, news analysis)
- Synthesize their outputs via debate
- Produce a structured trading decision (rating + entry + stop + size)
- Be extensible enough to add BIST-specific data sources and Turkish-language output

Options surveyed:
- Build from scratch
- Use LangChain/LangGraph directly with custom agents
- Fork TradingAgents (Tauric Research)
- Use FinMem / FinAgent / TradingGPT

## Decision

**Fork TradingAgents v0.2.5** (Apache-2.0, ~78k stars), namespaced as `tradingagents_us/` for our TR additions. Vendored fork model: keep upstream `tradingagents/` lightly patched, add TR-specific layer in a sibling package, merge upstream quarterly.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Build from scratch | 6+ weeks of orchestration plumbing we'd get for free; 78k-star community will outpace us |
| LangChain/LangGraph alone | Same as above — TradingAgents IS a LangGraph project, we'd reinvent its node topology |
| FinMem | Smaller community (~200 stars), single research codebase, less actively maintained; **steal its memory layer instead** |
| FinAgent (multimodal) | Promising but research code; v2 enhancement candidate, not core foundation |
| TradingGPT | Superseded by FinMem (same authors) |
| Trading-R1 | More credible Sharpe (2.72 vs paper-inflated 8.21) but RL-trained reasoning is a different beast; revisit Phase 8 |
| TradingGoose (fork w/ portfolio mgmt) | Study as forking precedent; their portfolio layer is a candidate to vendor |

## Consequences

### Accepted

- 12 LLM calls per decision baseline (~$0.50 uncached at Sonnet 4.6)
- Single-ticker per `propagate()` call → we add a portfolio aggregation layer ourselves
- Memory is a markdown file in v0.2.5 → we upgrade to pgvector in Phase 8
- No native Finnhub in v0.2.5 → we add it back via custom dataflow
- Quarterly upstream merge maintenance cost
- LLM training-set leakage in published backtests → we re-run with frozen pre-cutoff model snapshots

### Mitigations

- **Per-agent LLM routing patch:** ~50 LOC in `graph/trading_graph.py` to accept `Dict[str, LLM]` keyed by agent name
- **Prompt caching:** Anthropic 5-min TTL applied to `instrument_context` + analyst reports → 70–85% input cost reduction
- **Honest backtest:** use a Claude snapshot dated before our test window's start, never the latest model
- **Portfolio layer:** new `tradingagents_us/portfolio/aggregator.py` runs after per-ticker decisions, applies portfolio-level limits

### Critical finding propagated to risk layer

Paper claims Sharpe 8.21; Trading-R1's 2.72 on held-out NVDA is the realistic upper bound. **Our paper-to-live gate uses Sharpe > 1.0 net of costs, not the paper number.** See ADR-005.

## File-level adaptation plan

| Concern | File | Effort |
|---|---|---|
| Ticker suffix `XU100.IS` | `dataflows/interface.py`, `default_config.py` | Low |
| FX / currency rendering | `agents/utils/agent_utils.py`, `schemas.py` | Medium |
| KAP disclosure | new `tradingagents_us/dataflows/kap.py` (wrap `pykap`) | Medium |
| TR news/sentiment | new `tradingagents_us/dataflows/tr_news.py` | High |
| Turkish output | `output_language="Turkish"` config (already supported) | Low |
| Trading calendar | `graph/propagation.py` patch for `Asia/Istanbul` | Low |
| BIST lot sizes | new `tradingagents_us/dataflows/tr_utils.py` | Medium |

## Sources

- https://github.com/TauricResearch/TradingAgents
- TradingAgents arXiv 2412.20138 v7
- Trading-R1 arXiv 2509.11420
- FinMem arXiv 2311.13743
- TradingGoose https://github.com/TradingGoose/TradingGoose.github.io
