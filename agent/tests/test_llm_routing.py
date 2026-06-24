"""Per-agent LLM routing + prompt-cache helpers (ADR-006). No network/$ spend."""

from __future__ import annotations

import pytest

from tradingagents_us.llm.cache import cacheable_messages, with_cache
from tradingagents_us.llm.routing import (
    AGENT_MODEL_MAP,
    HAIKU,
    OPUS,
    SONNET,
    build_agent_llms,
)

ALL_ROLES = {
    "market_analyst",
    "fundamentals_analyst",
    "news_analyst",
    "social_media_analyst",
    "sentiment_analyst",
    "bull_researcher",
    "bear_researcher",
    "research_manager",
    "trader",
    "aggressive_debator",
    "neutral_debator",
    "conservative_debator",
    "portfolio_manager",
}


class TestRoutingMap:
    def test_covers_every_role(self) -> None:
        assert set(AGENT_MODEL_MAP) == ALL_ROLES

    def test_managers_use_opus(self) -> None:
        assert AGENT_MODEL_MAP["research_manager"] == OPUS
        assert AGENT_MODEL_MAP["portfolio_manager"] == OPUS

    def test_risk_debators_use_haiku(self) -> None:
        for role in ("aggressive_debator", "neutral_debator", "conservative_debator"):
            assert AGENT_MODEL_MAP[role] == HAIKU

    def test_reasoning_analysts_use_sonnet(self) -> None:
        assert AGENT_MODEL_MAP["fundamentals_analyst"] == SONNET
        assert AGENT_MODEL_MAP["news_analyst"] == SONNET


class TestBuildAgentLlms:
    def test_reuses_one_client_per_unique_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        built: list[str] = []

        class _FakeChat:
            def __init__(self, model: str, temperature: float = 0.0) -> None:
                self.model = model
                built.append(model)

        monkeypatch.setattr("langchain_anthropic.ChatAnthropic", _FakeChat)
        llms = build_agent_llms()
        assert set(llms) == ALL_ROLES
        # 3 distinct models -> only 3 client constructions despite 13 roles
        assert sorted(set(built)) == sorted({OPUS, SONNET, HAIKU})
        assert len(built) == 3
        # same model -> same client instance
        assert llms["aggressive_debator"] is llms["neutral_debator"]


class TestCache:
    def test_with_cache_marker_shape(self) -> None:
        block = with_cache("prefix", ttl="1h")
        assert block["type"] == "text"
        assert block["text"] == "prefix"
        assert block["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_cacheable_messages_splits_static_and_dynamic(self) -> None:
        msgs = cacheable_messages("sys", "static", "dynamic")
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"][0]["cache_control"]["ttl"] == "1h"
        user = msgs[1]["content"]
        assert user[0]["cache_control"]["ttl"] == "5m"  # static prefix cached
        assert "cache_control" not in user[1]  # dynamic suffix not cached
        assert user[1]["text"] == "dynamic"
