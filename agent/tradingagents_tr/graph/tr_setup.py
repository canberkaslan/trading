"""TR composition entry point.

Builds the LangGraph workflow with TR data sources and Turkish output, runs
a single-ticker decision, returns AgentDecision after risk-layer approval.

Phase 1 stub — wires once we vendor the upstream tradingagents package.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from ..schemas import AgentDecision


def propagate_tr(
    ticker: str,
    trade_date: str,
    market: str = "BIST",
    output_language: str = "Turkish",
) -> AgentDecision:
    """Run the full 7-agent pipeline for one TR ticker.

    TODO(phase-2):
        - Import TradingAgentsGraph from vendored upstream
        - Override deep_think_llm/quick_think_llm with per-agent dict from llm.routing
        - Replace get_news/get_insider_transactions with KAP-backed implementations
        - Wrap final decision in AgentDecision schema
        - Run risk-layer check before returning
    """
    raise NotImplementedError("tr_setup.propagate_tr — phase 2")


def propagate_us(
    ticker: str,
    trade_date: str,
    output_language: str = "English",
) -> AgentDecision:
    """Run pipeline for one US ticker (standard TradingAgents path, with our LLM routing)."""
    raise NotImplementedError("tr_setup.propagate_us — phase 2")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run TradingAgents-TR for a single ticker")
    parser.add_argument("--ticker", required=True, help="Ticker (e.g. AAPL or ASELS for BIST)")
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--market", choices=["US", "BIST"], default="US")
    parser.add_argument("--paper", action="store_true", help="Paper trade only (no live execution)")
    args = parser.parse_args()

    fn = propagate_tr if args.market == "BIST" else propagate_us
    decision = fn(args.ticker, args.date)
    print(decision.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
