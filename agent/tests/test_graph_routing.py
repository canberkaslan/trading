"""GraphSetup per-agent routing hook (_pick) — back-compat + override.

Verifies the vendored fork patch without running the full LLM graph.
"""

from __future__ import annotations

import pytest

setup_mod = pytest.importorskip("tradingagents.graph.setup")
GraphSetup = setup_mod.GraphSetup


def _make(agent_llms=None) -> "GraphSetup":
    # _pick only touches agent_llms; the other args are stored unused for it.
    return GraphSetup(
        quick_thinking_llm="QUICK",
        deep_thinking_llm="DEEP",
        tool_nodes={},
        conditional_logic=object(),
        agent_llms=agent_llms,
    )


def test_pick_falls_back_to_default_without_map() -> None:
    gs = _make()
    assert gs.agent_llms == {}
    assert gs._pick("market_analyst", "QUICK") == "QUICK"
    assert gs._pick("research_manager", "DEEP") == "DEEP"


def test_pick_uses_routed_llm_when_present() -> None:
    gs = _make({"market_analyst": "HAIKU", "research_manager": "OPUS"})
    assert gs._pick("market_analyst", "QUICK") == "HAIKU"
    assert gs._pick("research_manager", "DEEP") == "OPUS"
    # unrouted role still falls back
    assert gs._pick("trader", "QUICK") == "QUICK"
