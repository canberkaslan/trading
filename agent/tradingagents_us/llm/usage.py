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

from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

# USD per million tokens, (input, output). Cache reads bill at 0.10x input and
# cache writes at 1.25x input, applied below rather than duplicated here.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_CACHE_READ_MULTIPLIER = 0.10
_CACHE_WRITE_MULTIPLIER = 1.25


def _price_for(model: str) -> tuple[float, float] | None:
    """Longest-prefix match, so dated snapshots resolve to their family.

    Model ids arrive as either a family name ("claude-sonnet-5") or a dated
    snapshot ("claude-haiku-4-5-20251001"); both must price the same.
    """
    best: tuple[str, tuple[float, float]] | None = None
    for name, price in _PRICES.items():
        if model.startswith(name) and (best is None or len(name) > len(best[0])):
            best = (name, price)
    return best[1] if best else None


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

    def __init__(self) -> None:
        self.usage = Usage()

    def on_llm_end(self, response: Any, **_: Any) -> None:
        try:
            self._collect(response)
        except Exception:  # noqa: BLE001 — telemetry must not break the run
            pass

    def _collect(self, response: Any) -> None:
        for generation_list in getattr(response, "generations", []) or []:
            for generation in generation_list or []:
                message = getattr(generation, "message", None)
                meta = getattr(message, "usage_metadata", None)
                if not meta:
                    continue

                details = meta.get("input_token_details") or {}
                cache_read = int(details.get("cache_read") or 0)
                cache_write = int(details.get("cache_creation") or 0)
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
                u.cost_usd += (
                    fresh_in * in_rate
                    + cache_read * in_rate * _CACHE_READ_MULTIPLIER
                    + cache_write * in_rate * _CACHE_WRITE_MULTIPLIER
                    + out * out_rate
                ) / 1_000_000
