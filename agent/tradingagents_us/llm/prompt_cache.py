"""Anthropic prompt caching for the council, applied at one point.

A measured NVDA run: 18 calls, 190,953 input tokens, 37,916 output,
$1.71 — and `cache_read=0`. Every one of those 18 calls re-sent the same tool
schemas and the same system prompt at full price.

Caching is not free and not symmetric: a cache write costs 1.25x input, a read
costs 0.10x. So it is a large saving when the cached prefix is genuinely stable
and a straight loss when it is not, and the two are indistinguishable without
measurement. That is why the accounting landed first — `cache_hit_rate` is now
the number that says which of the two happened, and a run that shows near zero
after this is switched on is telling us to switch it back off.

**Where the breakpoints go.** Anthropic caches a PREFIX of the request, in the
order tools -> system -> messages, and allows at most four breakpoints. The
messages are where the conversation grows, so they are deliberately left alone;
everything before them is identical on every call an agent makes. One
breakpoint on the last tool covers the whole tool schema block, and one on the
system prompt covers tools plus system together.

**Why not the vendor's prompt files.** The prompts are built inside thirteen
upstream agent modules that a subtree pull rewrites. Overriding the one method
that assembles the API payload puts the change in a single place we own.

**Default TTL is the plain five-minute cache**, which is generally available
and needs no beta header. The one-hour variant costs 2x to write instead of
1.25x and has to be requested explicitly — worth it across a full daily run,
wrong for a single ticker. TRADINGAGENTS_CACHE_TTL=1h opts in.

A prefix under Anthropic's minimum (1024 tokens, 2048 for Haiku) is silently
not cached — no error, no charge. So a short system prompt costs nothing here;
it simply does not participate.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_VALID_TTLS = ("5m", "1h")


def _ttl() -> str | None:
    """Configured TTL, or None for Anthropic's default five minutes.

    Returning None rather than "5m" matters: omitting the key uses the GA
    behaviour, while sending an explicit ttl requires a beta header that this
    client may not be sending.
    """
    raw = (os.environ.get("TRADINGAGENTS_CACHE_TTL") or "").strip().lower()
    if raw in ("", "5m"):
        return None
    if raw == "1h":
        return "1h"
    log.warning(
        "TRADINGAGENTS_CACHE_TTL=%r is not one of %s — using the default",
        raw, _VALID_TTLS,
    )
    return None


def _marker() -> dict[str, Any]:
    ttl = _ttl()
    return {"type": "ephemeral", "ttl": ttl} if ttl else {"type": "ephemeral"}


def _cache_system(system: Any) -> Any:
    """Put a breakpoint at the end of the system prompt.

    The system field is either a plain string or a list of content blocks
    depending on how the prompt was built, so both shapes are handled rather
    than assumed.
    """
    if isinstance(system, str):
        if not system.strip():
            return system
        return [{"type": "text", "text": system, "cache_control": _marker()}]
    if isinstance(system, list) and system:
        blocks = [dict(b) if isinstance(b, dict) else b for b in system]
        last = blocks[-1]
        if isinstance(last, dict) and last.get("type") == "text":
            last["cache_control"] = _marker()
        return blocks
    return system


def _cache_tools(tools: Any) -> Any:
    """Put a breakpoint on the LAST tool, which caches the whole tool block."""
    if not isinstance(tools, list) or not tools:
        return tools
    out = [dict(t) if isinstance(t, dict) else t for t in tools]
    if isinstance(out[-1], dict):
        out[-1]["cache_control"] = _marker()
    return out


def apply_cache_control(payload: dict[str, Any]) -> dict[str, Any]:
    """Add cache breakpoints to a request payload. Pure; safe on any shape."""
    if "tools" in payload:
        payload["tools"] = _cache_tools(payload["tools"])
    if payload.get("system") is not None:
        payload["system"] = _cache_system(payload["system"])
    return payload


def install() -> bool:
    """Make the upstream Anthropic client emit cached prefixes. Idempotent.

    Replaces the class the vendor's `get_llm()` instantiates, which is the only
    handle we have on an object the graph builds internally. Returns False
    rather than raising if upstream moves it — losing the saving is acceptable,
    losing the run is not.
    """
    try:
        from tradingagents.llm_clients import anthropic_client as mod
    except ImportError:
        return False

    base = getattr(mod, "NormalizedChatAnthropic", None)
    if base is None:
        return False
    if getattr(base, "_prompt_cache_installed", False):
        return True

    class CachingChatAnthropic(base):  # type: ignore[misc, valid-type]
        """Upstream's client, with a cache breakpoint on the stable prefix."""

        _prompt_cache_installed = True

        def _get_request_payload(self, input_, *, stop=None, **kwargs):  # type: ignore[no-untyped-def]
            payload = super()._get_request_payload(input_, stop=stop, **kwargs)
            try:
                return apply_cache_control(payload)
            except Exception:  # noqa: BLE001
                # A malformed payload must cost the saving, never the call.
                log.warning("prompt cache: leaving payload unmodified", exc_info=True)
                return payload

    mod.NormalizedChatAnthropic = CachingChatAnthropic  # type: ignore[attr-defined]
    return True
