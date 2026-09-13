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
from datetime import UTC, datetime
from pathlib import Path

# Make the vendored upstream importable
_VENDOR = Path(__file__).resolve().parent.parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from tradingagents_us.llm.agent_routing import install as install_agent_routing  # noqa: E402
from tradingagents_us.llm.prompt_cache import install as install_prompt_cache  # noqa: E402
from tradingagents_us.llm.usage import UsageCollector  # noqa: E402

from ..schemas import AgentDecision, AgentReasoning  # noqa: E402

log = logging.getLogger(__name__)


def _load_env() -> None:
    """Auto-load .env so CLI runs work without pre-sourcing."""
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
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

    from tradingagents.graph.trading_graph import (
        TradingAgentsGraph,  # type: ignore[import-not-found]
    )

    log.info("initializing TradingAgentsGraph for %s @ %s", ticker, trade_date)
    # `callbacks` is upstream's own seam (trading_graph.py forwards it into the
    # LLM constructor), so the accounting rides along without a patch. Until
    # this existed the only cost figure in the system was a hand-written string
    # in a log line, and every budget argument was derived from it.
    # Must run BEFORE the graph is built: it swaps the class the vendor's
    # get_llm() instantiates, and the graph constructs its LLMs in __init__.
    if not install_prompt_cache():
        log.warning("prompt cache not installed — upstream client moved; running uncached")

    # Also before construction: this rebinds the agent factories setup.py calls.
    # Off unless TRADINGAGENTS_AGENT_ROUTING is set, because it changes what the
    # agents say and not only what they cost.
    routed = install_agent_routing()
    if routed:
        log.info("cheap-tier routing active for: %s", ", ".join(sorted(routed)))

    usage = UsageCollector()
    ta = TradingAgentsGraph(
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        callbacks=[usage],
    )

    log.info("propagating decision pipeline (this will make ~12 LLM calls)…")
    final_state, processed_signal = ta.propagate(ticker, trade_date)

    u = usage.usage
    hit = u.cache_hit_rate
    log.info(
        "council usage for %s: %d calls, in=%d out=%d cache_read=%d cache_write=%d "
        "cost=$%.4f hit_rate=%s",
        ticker, u.calls, u.input_tokens, u.output_tokens,
        u.cache_read_tokens, u.cache_write_tokens, u.cost_usd,
        "n/a" if hit is None else f"{hit:.1%}",
    )
    if u.unpriced_models:
        # Cost is understated by whatever these models consumed. Say so rather
        # than letting a low figure read as a cheap run.
        log.warning(
            "cost EXCLUDES unpriced models: %s — figure is a floor, not a total",
            ", ".join(sorted(u.unpriced_models)),
        )

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

    # LLM council (opt-in): cross-family voters (DeepSeek + GLM) re-rate the
    # house view, Opus 4.8 chair issues the final call. Fail-safe — any error
    # leaves the house `rating` untouched.
    if os.environ.get("LLM_COUNCIL", "0") in ("1", "true", "True"):
        rating = _apply_council(ticker, final_state, rating, str(final or ""), reasoning)

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
        timestamp_utc=datetime.now(UTC),
        decision_id=str(uuid.uuid4()),
        tokens_in=u.input_tokens,
        tokens_out=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
        cache_write_tokens=u.cache_write_tokens,
        # A floor rather than a total when a model was not priced; the warning
        # above names which, so a low figure cannot pass as a cheap run.
        cost_usd=u.cost_usd,
    )


def _apply_council(ticker, final_state, house_rating, house_final, reasoning):
    """Run the LLM council and return the chair's final rating.

    Returns the unchanged house rating on any failure. Appends the chair
    synthesis + each voter's opinion to `reasoning` so the app can show them.
    """
    try:
        from tradingagents_us.llm.council import CHAIR_MODEL, council_review
    except Exception:  # noqa: BLE001
        return house_rating

    parts = []
    for label, key in [
        ("MARKET", "market_report"),
        ("FUNDAMENTALS", "fundamentals_report"),
        ("NEWS", "news_report"),
        ("SENTIMENT", "sentiment_report"),
        ("RESEARCH PLAN", "investment_plan"),
    ]:
        body = (final_state.get(key) or "").strip()
        if body:
            parts.append(f"[{label}]\n{body[:1200]}")
    digest = f"Research digest for {ticker}:\n\n" + "\n\n".join(parts)

    result = council_review(ticker, digest, house_rating, house_final)
    if result is None:
        log.info("council unavailable for %s — keeping house rating %s", ticker, house_rating)
        return house_rating

    log.info(
        "council: %s house=%s -> final=%s (conf %d)",
        ticker, house_rating, result.final_rating, result.confidence,
    )
    reasoning.append(
        AgentReasoning(
            agent="council_chair",
            model=CHAIR_MODEL,
            summary=f"[confidence {result.confidence}] {result.chair_summary}",
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
        )
    )
    for v in result.votes:
        reasoning.append(
            AgentReasoning(
                agent=f"council:{v.member}",
                model=v.member,
                summary=f"{v.rating or '?'} — {v.rationale}",
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
            )
        )
    return result.final_rating


def _parse_pm_output(text: str) -> tuple[str, float | None, str | None]:
    """Parse Portfolio Manager output.

    Upstream PM emits markdown with **Rating**: ..., **Price Target**: ...,
    **Time Horizon**: ... headers. Regex-based extraction for Phase 2; Phase
    3 will switch to structured output (with_structured_output).
    """
    import re

    rating_match = re.search(
        r"\*\*Rating\*\*\s*:?\s*(Buy|Overweight|Hold|Underweight|Sell)", text, re.I
    )
    if rating_match is None:
        # An unparseable PM decision used to become "Hold" here, which is a
        # TRADEABLE rating: the sizer maps it to no order, the log records a
        # deliberate decision to stand pat, and a parse failure becomes
        # indistinguishable from the model actually saying hold. Upstream fixed
        # the same bug in its own parser (#1170, returning REVIEW) but that fix
        # never reaches this code — pipeline.py re-parses the PM markdown itself
        # rather than using the vendored SignalProcessor's answer.
        #
        # Raising is the honest branch. Every caller of this pipeline is the
        # daily run, which already treats a failed ticker as a failed ticker,
        # logs it, and carries on with the rest of the universe. A visible gap
        # in one name beats a fabricated Hold in the decision record.
        raise ValueError(
            "portfolio manager output carries no parseable **Rating** — refusing "
            "to default to Hold, which would be indistinguishable from a real one"
        )
    # Normalize: pydantic Literal is case-sensitive
    rating = rating_match.group(1).capitalize()

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
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
    )
    parser = argparse.ArgumentParser(description="Run TradingAgents-US for a single ticker")
    parser.add_argument("--ticker", required=True, help="US ticker (e.g. AAPL)")
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    args = parser.parse_args()

    decision = propagate(args.ticker, args.date)
    print("\n=== DECISION ===")
    print(decision.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
