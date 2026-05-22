"""Anthropic prompt-caching helpers — see ADR-006.

Cache hits cost 0.10x input; cache writes cost 1.25x input. Within a single
ticker decision (under 5 min) we read the same growing prefix 6-8 times,
so caching the stable parts saves 70-85% of input cost.
"""

from __future__ import annotations

from typing import Any, Literal

CacheTTL = Literal["5m", "1h"]


def with_cache(content: str, ttl: CacheTTL = "5m") -> dict[str, Any]:
    """Wrap a string as an Anthropic content block with cache_control."""
    return {
        "type": "text",
        "text": content,
        "cache_control": {"type": "ephemeral", "ttl": ttl},
    }


def cacheable_messages(
    system: str,
    static_prefix: str,
    dynamic_suffix: str,
) -> list[dict[str, Any]]:
    """Build a messages payload with the right cache markers.

    Pattern:
      - system: cached (changes only on prompt revision)
      - static_prefix: cached (analyst reports, instrument context — stable within a cycle)
      - dynamic_suffix: not cached (the actual question for this node)
    """
    return [
        {"role": "system", "content": [with_cache(system, ttl="1h")]},
        {
            "role": "user",
            "content": [
                with_cache(static_prefix, ttl="5m"),
                {"type": "text", "text": dynamic_suffix},
            ],
        },
    ]
