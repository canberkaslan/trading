"""Count what a council actually costs, so cost decisions stop being guesses.

Nothing in this system measures model spend. The only figure anywhere is the
string "~5-10 min, ~$0.50-1.50" hand-written into a log line in
`scripts/trade.py`, and every budget argument since has been derived from it —
including the arithmetic that says a wider universe is unaffordable. That may
well be right, but it is a guess quoting a guess, and the invoice is the only
place it has ever been checked.

It also blocks the next optimisation. Anthropic prompt caching charges 1.25x to
write a cache entry and 0.10x to read one, so it is a large saving when the
cached prefix is genuinely stable and a straight *loss* when it is not. Without
`cache_read` in hand there is no way to tell those two outcomes apart, and
shipping an optimisation whose effect cannot be observed is how a regression
gets mistaken for a win.

This hooks the `callbacks` parameter `TradingAgentsGraph` already accepts and
`AnthropicClient` already forwards — a supported seam, not a patch — and
accumulates usage over every call in one ticker's run.

Pricing is deliberately explicit rather than fetched. A rate that silently
changes underneath a stored figure makes historical rows incomparable, so the
table below is versioned in the repository and a model it does not know reports
zero cost with its tokens still counted. A missing price must look like a
missing price, never like a cheap model.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger(__name__)

# USD per million tokens, (input, output). Cache reads bill at 0.10x input and
# cache writes at 1.25x input, applied below rather than duplicated here.
#
# THESE RATES ARE ASSUMPTIONS, not measurements, and they are the one number
# here that cannot be checked from inside the process. Tokens are counted from
# the API's own response; cost is those counts multiplied by this table. A
# stale entry silently rescales every comparison — and worst of all it does so
# ASYMMETRICALLY when only some models are stale, which flatters or damns a
# routing change for reasons that have nothing to do with the change.
#
# So: override them rather than editing this file, with
# TRADINGAGENTS_PRICES='{"claude-sonnet-5": [2.0, 10.0]}', and treat the
# per-decision `tokens_*` columns as the durable record. Cost is derived and
# can be recomputed; tokens cannot be recovered after the fact.
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _prices() -> dict[str, tuple[float, float]]:
    """Rate table, with env overrides merged over the defaults."""
    table = dict(_DEFAULT_PRICES)
    raw = os.environ.get("TRADINGAGENTS_PRICES")
    if not raw:
        return table
    try:
        for name, pair in json.loads(raw).items():
            table[str(name)] = (float(pair[0]), float(pair[1]))
    except Exception:  # noqa: BLE001
        # A malformed override must not silently zero the cost of everything.
        log.warning("TRADINGAGENTS_PRICES is not usable — falling back to defaults")
    return table

_CACHE_READ_MULTIPLIER = 0.10
_CACHE_WRITE_MULTIPLIER = 1.25


def _price_for(model: str) -> tuple[float, float] | None:
    """Longest-prefix match, so dated snapshots resolve to their family.

    Model ids arrive as either a family name ("claude-sonnet-5") or a dated
    snapshot ("claude-haiku-4-5-20251001"); both must price the same.
    """
    best: tuple[str, tuple[float, float]] | None = None
    for name, price in _prices().items():
        if model.startswith(name) and (best is None or len(name) > len(best[0])):
            best = (name, price)
    return best[1] if best else None


@dataclass
class NodeUsage:
    """One graph node's share of the bill."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class Usage:
    """Token totals for one council run, and what they cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    # Models seen but not priced. Surfaced so an unpriced model reads as a gap
    # rather than as a free one.
    unpriced_models: set[str] = field(default_factory=set)

    # Per graph node. The aggregate says what a council costs; this says which
    # of its eighteen calls that money went to — which is the only basis on
    # which any of them could be cut.
    by_node: dict[str, NodeUsage] = field(default_factory=dict)

    def breakdown(self) -> list[tuple[str, NodeUsage]]:
        """Nodes ordered by spend, dearest first."""
        return sorted(self.by_node.items(), key=lambda kv: kv[1].cost_usd, reverse=True)

    @property
    def cache_hit_rate(self) -> float | None:
        """Share of billed input that came from cache. None when nothing ran.

        This is the number that says whether prompt caching is working. Near
        zero after caching is enabled means the cached prefix is not actually
        stable, and the 1.25x writes are pure loss.
        """
        billed = self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
        return (self.cache_read_tokens / billed) if billed else None


class UsageCollector(BaseCallbackHandler):
    """Accumulate token usage across every LLM call in a run.

    Never raises: a malformed or unexpected payload costs a measurement, and a
    measurement must never cost a trading decision.
    """

    # LangGraph puts the node name in the callback metadata, and on_llm_start
    # and on_llm_end share a run_id — so the two can be joined without the
    # agents knowing they are being measured.
    _NODE_KEY = "langgraph_node"

    def __init__(self, on_node: Callable[[str], None] | None = None) -> None:
        self.usage = Usage()
        self._node_of: dict[Any, str] = {}
        # Optional progress sink. A council takes about ten minutes, and a
        # caller that can only report "running" for that long is
        # indistinguishable from one that has hung.
        self._on_node = on_node

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        with contextlib.suppress(Exception):
            run_id = kwargs.get("run_id")
            meta = kwargs.get("metadata") or {}
            node = meta.get(self._NODE_KEY)
            if run_id is not None and node:
                self._node_of[run_id] = str(node)
                if self._on_node is not None:
                    # Never let a progress listener break the run it reports on.
                    with contextlib.suppress(Exception):
                        self._on_node(str(node))

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        # Telemetry must never break the run: a malformed payload costs a
        # measurement, not a trading decision.
        with contextlib.suppress(Exception):
            run_id = kwargs.get("run_id")
            # Unattributed rather than guessed. A call whose node is unknown
            # must not be silently folded into a neighbour's total, or the
            # breakdown would quietly misattribute the very thing it exists to
            # show.
            node = self._node_of.pop(run_id, None) if run_id is not None else None
            self._collect(response, node or "(unattributed)")

    def _collect(self, response: Any, node: str = "(unattributed)") -> None:
        for generation_list in getattr(response, "generations", []) or []:
            for generation in generation_list or []:
                message = getattr(generation, "message", None)
                meta = getattr(message, "usage_metadata", None)
                if not meta:
                    continue

                details = meta.get("input_token_details") or {}
                cache_read = int(details.get("cache_read") or 0)
                # langchain moves the write count into per-TTL keys and ZEROES
                # the generic `cache_creation` whenever it does, to avoid double
                # counting. Reading only the generic key therefore reported
                # cache_write=0 on a run that demonstrably wrote entries — and
                # since writes bill at 1.25x while plain input bills at 1.0x,
                # those tokens were being priced as ordinary input and the cost
                # came out low. Take whichever the response actually used.
                cache_write = int(details.get("cache_creation") or 0) or sum(
                    int(details.get(k) or 0)
                    for k in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
                )
                # langchain reports `input_tokens` as the TOTAL including cached
                # portions. Billing them again at full rate would overstate cost
                # exactly where caching is meant to help, so the cached parts are
                # subtracted out and priced at their own multipliers.
                total_in = int(meta.get("input_tokens") or 0)
                fresh_in = max(0, total_in - cache_read - cache_write)
                out = int(meta.get("output_tokens") or 0)

                u = self.usage
                u.calls += 1
                u.input_tokens += fresh_in
                u.output_tokens += out
                u.cache_read_tokens += cache_read
                u.cache_write_tokens += cache_write

                n = u.by_node.setdefault(node, NodeUsage())
                n.calls += 1
                n.input_tokens += fresh_in
                n.output_tokens += out
                n.cache_read_tokens += cache_read
                n.cache_write_tokens += cache_write

                model = str(
                    (getattr(message, "response_metadata", None) or {}).get("model_name")
                    or (getattr(message, "response_metadata", None) or {}).get("model")
                    or ""
                )
                price = _price_for(model)
                if price is None:
                    if model:
                        u.unpriced_models.add(model)
                    continue
                in_rate, out_rate = price
                call_cost = (
                    fresh_in * in_rate
                    + cache_read * in_rate * _CACHE_READ_MULTIPLIER
                    + cache_write * in_rate * _CACHE_WRITE_MULTIPLIER
                    + out * out_rate
                ) / 1_000_000
                u.cost_usd += call_cost
                n.cost_usd += call_cost
