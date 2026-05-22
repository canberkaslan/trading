"""tradingagents_tr — TR/BIST adaptation layer for TradingAgents.

This package contains everything TR/BIST-specific:
- dataflows: KAP scraper, TR news, Matriks IQ wrapper
- prompts: Turkish-language prompt overrides
- graph: TR-specific LangGraph composition
- risk: deterministic risk layer (position sizing, stops, circuit breakers, kill switch)
- llm: per-agent model routing + prompt caching helpers
- memory: pgvector semantic memory (upgrade from upstream markdown log)
"""

__version__ = "0.1.0"
