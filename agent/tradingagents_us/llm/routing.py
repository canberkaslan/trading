"""Per-agent LLM routing — see ADR-006.

TradingAgents upstream only exposes deep_think_llm / quick_think_llm. We bind
a dict keyed by agent role to enable Opus for managers, Sonnet for analysts
that benefit from reasoning, and Haiku for heuristic risk debators.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic

OPUS = "claude-opus-4-7"
SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5"

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
