"""Per-agent LLM routing — see ADR-006.

TradingAgents upstream only exposes deep_think_llm / quick_think_llm. We bind
a dict keyed by agent role to enable Opus for managers, Sonnet for analysts
that benefit from reasoning, and Haiku for heuristic risk debators.
"""

from __future__ import annotations

import os

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic

# The tiers follow the SAME environment variables the pipeline reads, so this
# map can never silently disagree with the models the box is configured to run.
# They were hard-coded to claude-opus-4-7 / claude-sonnet-4-6 — two releases
# behind what the box actually runs (claude-opus-5 / claude-sonnet-5). Nothing
# imports this module yet, so the staleness was invisible; the moment anyone
# wired it, it would have silently DOWNGRADED every deliberately-chosen model.
# A routing table is the wrong place to re-decide which model a tier is.
OPUS = os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "claude-opus-5")
SONNET = os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "claude-sonnet-5")

# The cheap tier is the point of this module and has no upstream equivalent, so
# it gets its own variable rather than borrowing one of the two above.
HAIKU = os.environ.get("TRADINGAGENTS_CHEAP_LLM", "claude-haiku-4-5-20251001")

AGENT_MODEL_MAP: dict[str, str] = {
    "market_analyst": HAIKU,
    "fundamentals_analyst": SONNET,
    "news_analyst": SONNET,
    "social_media_analyst": HAIKU,
    "sentiment_analyst": HAIKU,
    "bull_researcher": SONNET,
    "bear_researcher": SONNET,
    "research_manager": OPUS,
    "trader": SONNET,
    "aggressive_debator": HAIKU,
    "neutral_debator": HAIKU,
    "conservative_debator": HAIKU,
    "portfolio_manager": OPUS,
}


def build_agent_llms(temperature: float = 0.1) -> dict[str, ChatAnthropic]:
    """Construct LangChain Anthropic clients per agent role."""
    from langchain_anthropic import ChatAnthropic

    cache: dict[str, ChatAnthropic] = {}
    out: dict[str, ChatAnthropic] = {}
    for agent, model in AGENT_MODEL_MAP.items():
        if model not in cache:
            cache[model] = ChatAnthropic(model=model, temperature=temperature)
        out[agent] = cache[model]
    return out
