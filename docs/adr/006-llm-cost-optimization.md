# ADR-006: LLM cost optimization

**Status:** Accepted
**Date:** 2026-05-22

## Context

Baseline TradingAgents per-decision cost:
- ~12 LLM calls
- 80–110k input / 8–12k output tokens
- ~$0.50–0.70 at Sonnet 4.6 uncached
- For 50 tickers × 22 trading days: **$600–800/mo just on LLM if naïve**

We need this under ~$200/mo without sacrificing decision quality at the synthesis points.

## Decision

Three concurrent optimizations:

### 1. Per-agent LLM routing

TradingAgents v0.2.5 only exposes `deep_think_llm` / `quick_think_llm`. We patch `graph/trading_graph.py` to accept `Dict[str, LLM]` keyed by agent name (~50 LOC).

| Agent role | Model | Reason |
|---|---|---|
| Market analyst (technical) | **Haiku 4.5** ($1/$5 per MTok) | Deterministic indicator selection — no deep reasoning |
| Fundamentals analyst | **Sonnet 4.6** ($3/$15) | Number-heavy, financial statement comprehension |
| News analyst | **Sonnet 4.6** | Long context (20 articles), nuanced sentiment |
| Social/sentiment analyst | **Haiku 4.5** | Volume-heavy, surface-level |
| Bull researcher | **Sonnet 4.6** | Argumentation quality matters |
| Bear researcher | **Sonnet 4.6** | Argumentation quality matters |
| Research Manager | **Opus 4.7** ($5/$25) | Judges debate — quality leverage point |
| Trader | **Sonnet 4.6** | Plan-to-action translator |
| Risk debator: aggressive | **Haiku 4.5** | Heuristic stance |
| Risk debator: neutral | **Haiku 4.5** | Heuristic stance |
| Risk debator: conservative | **Haiku 4.5** | Heuristic stance |
| Portfolio Manager | **Opus 4.7** | Final call — quality leverage |

### 2. Anthropic prompt caching (5-min TTL)

Cache write = 1.25× input price; cache read = 0.10× input (90% off). High-leverage cache points within one ticker's decision cycle (all under 5 min):

| Cache point | Static prefix | Reused by |
|---|---|---|
| Each analyst system prompt (~2k tokens — indicator definitions, rating scale) | 2k | 100% across all tickers same day |
| `instrument_context` (company info) | 500 | All 12 nodes within one ticker |
| Market report → reused by 8 downstream nodes | 1.5k | 100% within ticker |
| All 4 analyst reports → reused by Research Manager, Trader, both PMs | 8k | 100% |
| Investment debate history → reused by RM + Trader + PM | 3k | 100% |

The entire pipeline runs in <2 minutes — comfortably within 5-min TTL.

**Realistic savings: 70–85% of input token cost.**

Implementation: `cache_control` markers on system prompts and report blocks in our Anthropic client wrapper.

### 3. Batch API for non-realtime workloads

50% discount on inputs + outputs. Use for:
- Nightly backtest sweeps
- End-of-day decision generation (US 22:30 UTC has hours of grace before next session)
- Reflection/lesson learning (post-hoc, not time-sensitive)

Real-time use cases stay on standard API:
- Intra-day news event response (rare — we're swing/positional)
- Mobile-initiated re-analysis requests

## Cost projection (50 tickers × 1 decision/day × 22 trading days/mo)

| Scenario | Monthly cost |
|---|---|
| All Opus 4.7, no caching, real-time | **$1,650** |
| Mixed (per-agent routing), no caching | $605 |
| **Mixed + prompt caching** | **$200** |
| Mixed + caching + Batch API (overnight) | $100 |
| Haiku-only + caching + Batch (quality floor) | $44 |

**Target: $200/mo for paper phase. Quality floor: $44/mo Haiku-only if we need to compress.**

## Provider failover

| Primary | Fallback |
|---|---|
| Claude (Anthropic direct) | Claude via AWS Bedrock (same prices, redundant infra) |
| Claude (any) | OpenAI GPT-5.4 / GPT-5.4-mini (LangChain swap) |
| All paid | Self-hosted Qwen 3.6 on EC2 g5.xlarge (~$1/hr on-demand) |

Failover triggered by:
- 5xx rate > 5% over 1 minute
- p99 latency > 30s
- Budget alarm: monthly spend > $400 → temporary downgrade to Haiku-only

## Alternatives considered

| Option | Why rejected |
|---|---|
| GPT-5.4 as primary | Slightly cheaper but worse on long-context financial reasoning in our tests |
| Self-hosted Llama 3.3 70B on Groq | Latency competitive but reasoning quality below Sonnet 4.6 |
| DeepSeek V4 via OpenRouter | Promising; revisit when stable. Currently unproven on financial reasoning |
| Skip prompt caching | Leaves 70–85% on the table — no |
| Skip per-agent routing | Easiest path; costs 3× more for no quality gain on heuristic agents |

## Implementation

`agent/tradingagents_tr/llm/routing.py`:

```python
from langchain_anthropic import ChatAnthropic

def build_agent_llms() -> dict[str, ChatAnthropic]:
    opus = ChatAnthropic(model="claude-opus-4-7", temperature=0.1)
    sonnet = ChatAnthropic(model="claude-sonnet-4-6", temperature=0.1)
    haiku = ChatAnthropic(model="claude-haiku-4-5", temperature=0.1)
    return {
        "market_analyst":         haiku,
        "fundamentals_analyst":   sonnet,
        "news_analyst":           sonnet,
        "social_media_analyst":   haiku,
        "sentiment_analyst":      haiku,
        "bull_researcher":        sonnet,
        "bear_researcher":        sonnet,
        "research_manager":       opus,
        "trader":                 sonnet,
        "aggressive_debator":     haiku,
        "neutral_debator":        haiku,
        "conservative_debator":   haiku,
        "portfolio_manager":      opus,
    }
```

Prompt-caching helper in `agent/tradingagents_tr/llm/cache.py`:

```python
def with_cache(content: str, ttl: Literal["5m", "1h"] = "5m") -> dict:
    """Anthropic cache_control marker. Apply to static prefix blocks."""
    return {
        "type": "text",
        "text": content,
        "cache_control": {"type": "ephemeral", "ttl": ttl},
    }
```

## Cost guardrails

- AWS Cost Explorer budget alarm at $300, $400, $500
- CloudWatch metric: per-decision-cycle token spend (custom metric from agent runtime)
- LiteLLM proxy (optional) for per-user spend caps if we go multi-user

## Sources

- Research `docs/RESEARCH.md` §3
- Anthropic prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic Batch API: https://claudeapi.com/en/blog/dev-guides/claude-batch-api-cost-optimization
- Finout pricing tracker: https://www.finout.io/blog/anthropic-api-pricing
