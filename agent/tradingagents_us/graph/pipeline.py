"""TR composition entry point.

Wraps the upstream `TradingAgentsGraph` with our additions. For Phase 2 we use
the upstream 2-tier (deep_think_llm + quick_think_llm) configuration, which
already aligns with ADR-006's Opus/Sonnet split. Three-tier (with Haiku for
heuristic agents) is deferred to Phase 3 along with prompt-cache markers.

Phase 2 scope:
- Drive upstream graph through TRADINGAGENTS_* env vars (.env already wired)
- Output schema mapped to our `AgentDecision`
- Risk-layer check before returning

Phase 3+ scope (TODO):
- Per-agent LLM routing (Haiku for risk debators + sentiment/market analysts)
- Anthropic prompt-caching markers
- Polygon / Finnhub dataflows substituted via VENDOR_METHODS
- pgvector semantic memory replacing markdown log
- True multi-ticker portfolio aggregation
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Make the vendored upstream importable
_VENDOR = Path(__file__).resolve().parent.parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from ..schemas import AgentDecision, AgentReasoning  # noqa: E402

log = logging.getLogger(__name__)


def _load_env() -> None:
    """Auto-load .env so CLI runs work without pre-sourcing."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"')
        if v:
            os.environ.setdefault(k, v)


def propagate(ticker: str, trade_date: str) -> AgentDecision:
    """Run the upstream 7-agent pipeline for one US ticker.

    Args:
        ticker: e.g. "AAPL"
        trade_date: ISO date string (the "as-of" date for the decision)

    Returns:
        AgentDecision with the final rating and reasoning blobs.
    """
    _load_env()

    from tradingagents.default_config import DEFAULT_CONFIG  # type: ignore[import-not-found]
    from tradingagents.graph.trading_graph import TradingAgentsGraph  # type: ignore[import-not-found]

    # ADR-006: opt-in per-agent LLM routing (Haiku for heuristic agents, Opus
    # for managers). OFF by default so the paper eval measures one fixed system;
    # flip TRADINGAGENTS_PER_AGENT_ROUTING=1 only after the eval window closes.
    cfg = dict(DEFAULT_CONFIG)
    if os.environ.get("TRADINGAGENTS_PER_AGENT_ROUTING", "0") in ("1", "true", "True"):
        from tradingagents_us.llm.routing import AGENT_MODEL_MAP

        cfg["agent_model_map"] = AGENT_MODEL_MAP
        log.info("per-agent LLM routing ENABLED (ADR-006)")

    log.info("initializing TradingAgentsGraph for %s @ %s", ticker, trade_date)
    ta = TradingAgentsGraph(
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config=cfg,
    )

    log.info("propagating decision pipeline (this will make ~12 LLM calls)…")
    final_state, processed_signal = ta.propagate(ticker, trade_date)

    # Upstream returns final_state dict and a processed decision string.
    # Map to our AgentDecision schema. Many fields are placeholders pending
    # Phase 3 wiring (structured output extraction from upstream).
    reasoning: list[AgentReasoning] = []
    for agent_key, report_key in [
        ("market_analyst", "market_report"),
        ("sentiment_analyst", "sentiment_report"),
        ("news_analyst", "news_report"),
        ("fundamentals_analyst", "fundamentals_report"),
    ]:
        body = final_state.get(report_key, "") or ""
        if body:
            reasoning.append(
                AgentReasoning(
                    agent=agent_key,
                    model=os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "claude-sonnet-4-6"),
                    summary=body[:2000],
                    tokens_in=0,
                    tokens_out=0,
                    latency_ms=0,
                )
            )

    if final_state.get("investment_plan"):
        reasoning.append(
            AgentReasoning(
                agent="research_manager",
                model=os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "claude-opus-4-7"),
                summary=final_state["investment_plan"][:2000],
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        )

    if final_state.get("trader_investment_plan"):
        reasoning.append(
            AgentReasoning(
                agent="trader",
                model=os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM", "claude-sonnet-4-6"),
                summary=final_state["trader_investment_plan"][:2000],
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        )

    final = final_state.get("final_trade_decision", processed_signal or "Hold")
    if final:
        reasoning.append(
            AgentReasoning(
                agent="portfolio_manager",
                model=os.environ.get("TRADINGAGENTS_DEEP_THINK_LLM", "claude-opus-4-7"),
                summary=str(final)[:2000],
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        )

    rating, price_target, horizon = _parse_pm_output(str(final or processed_signal or ""))

    # Trader gives the concrete entry / stop / sizing numbers. Upstream may
    # store it under either field name across versions.
    trader_text = (
        final_state.get("trader_investment_plan")
        or final_state.get("trader_investment_decision")
        or ""
    )
    entry_price, stop_loss, suggested_size_pct = _parse_trader_output(str(trader_text))

    return AgentDecision(
        ticker=ticker,
        market="US",
        quote_currency="USD",
        rating=rating,
        entry_price=entry_price,
        stop_loss=stop_loss,
        suggested_size_pct=suggested_size_pct,
        price_target=price_target,
        time_horizon=horizon,
        reasoning=reasoning,
        debate_transcript={},
        final_decision_text=str(final or "")[:8000],
        timestamp_utc=datetime.now(timezone.utc),
        decision_id=str(uuid.uuid4()),
    )


def _parse_pm_output(text: str) -> tuple[str, float | None, str | None]:
    """Parse Portfolio Manager output.

    Upstream PM emits markdown with **Rating**: ..., **Price Target**: ...,
    **Time Horizon**: ... headers. Regex-based extraction for Phase 2; Phase
    3 will switch to structured output (with_structured_output).
    """
    import re

    rating_match = re.search(r"\*\*Rating\*\*\s*:?\s*(Buy|Overweight|Hold|Underweight|Sell)", text, re.I)
    rating_raw = (rating_match.group(1).capitalize() if rating_match else "Hold")
    # Normalize: pydantic Literal is case-sensitive
    rating_map = {
        "Buy": "Buy", "Overweight": "Overweight", "Hold": "Hold",
        "Underweight": "Underweight", "Sell": "Sell",
    }
    rating = rating_map.get(rating_raw, "Hold")

    pt_match = re.search(r"\*\*Price Target\*\*\s*:?\s*\$?([\d,]+(?:\.\d+)?)", text, re.I)
    price_target: float | None = None
    if pt_match:
        try:
            price_target = float(pt_match.group(1).replace(",", ""))
        except ValueError:
            price_target = None

    horizon_match = re.search(r"\*\*Time Horizon\*\*\s*:?\s*([^\n*]+)", text, re.I)
    horizon = horizon_match.group(1).strip() if horizon_match else None

    return rating, price_target, horizon


def _parse_trader_output(text: str) -> tuple[float | None, float | None, float]:
    """Parse upstream Trader markdown into (entry_price, stop_loss, size_pct).

    Trader markdown (render_trader_proposal in vendor schemas.py) emits:

        **Action**: Buy | Hold | Sell
        **Reasoning**: ...
        **Entry Price**: 271.0
        **Stop Loss**: 229.0
        **Position Sizing**: 4–5% of portfolio, ...
        FINAL TRANSACTION PROPOSAL: **BUY**

    `position_sizing` is freeform text — we extract the first percentage
    (e.g. "4-5%" -> 0.045, "5%" -> 0.05). Defaults to 0.0 (risk layer skips
    sizing if the LLM didn't propose one).
    """
    import re

    entry: float | None = None
    stop: float | None = None
    size: float = 0.0

    e_match = re.search(r"\*\*Entry Price\*\*\s*:?\s*\$?([\d,]+(?:\.\d+)?)", text, re.I)
    if e_match:
        try:
            entry = float(e_match.group(1).replace(",", ""))
        except ValueError:
            entry = None

    s_match = re.search(r"\*\*Stop[ \-]?Loss\*\*\s*:?\s*\$?([\d,]+(?:\.\d+)?)", text, re.I)
    if s_match:
        try:
            stop = float(s_match.group(1).replace(",", ""))
        except ValueError:
            stop = None

    # "4–5%" / "4-5%" / "5%" — take midpoint of range, else single value
    sz_match = re.search(
        r"\*\*Position Sizing\*\*\s*:?\s*([\d.]+)\s*(?:[–—\-]\s*([\d.]+))?\s*%",
        text,
        re.I,
    )
    if sz_match:
        try:
            lo = float(sz_match.group(1))
            hi = float(sz_match.group(2)) if sz_match.group(2) else lo
            size = ((lo + hi) / 2.0) / 100.0
            size = max(0.0, min(size, 1.0))
        except ValueError:
            size = 0.0

    return entry, stop, size


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description="Run TradingAgents-US for a single ticker")
    parser.add_argument("--ticker", required=True, help="US ticker (e.g. AAPL)")
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = parser.parse_args()

    decision = propagate(args.ticker, args.date)
    print("\n=== DECISION ===")
    print(decision.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
