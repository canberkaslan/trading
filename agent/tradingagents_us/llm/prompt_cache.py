"""Anthropic prompt caching for the council, applied at one point.

A measured NVDA run: 18 calls, 190,953 input tokens, 37,916 output,
$1.71 — and `cache_read=0`. Every one of those 18 calls re-sent the same tool
schemas and the same system prompt at full price.

Caching is not free and not symmetric: a cache write costs 1.25x input, a read
costs 0.10x. So it is a large saving when the cached prefix is genuinely stable
and a straight loss when it is not, and the two are indistinguishable without
measurement. That is why the accounting landed first — `cache_hit_rate` is the
number that says which of the two happened.

**What marking tools + system bought: a 7-9% hit rate.** 200 councils
(2026-09-28): 40.3M uncached input, 3.35M read, 1.07M written. A SPY council
captured request by request (2026-09-29) shows why:

- 73% of council input (125k of 171k tokens) is in calls made ONCE with a
  prompt nobody re-sends. The debaters, research manager, trader and
  portfolio manager each build one fresh prompt that opens with the agent's
  own role text and only then embeds the four analyst reports. None of those
  requests matches any earlier one beyond its first few words, and a cache
  is a prefix match, so there is nothing to read. The five debaters do carry
  the same ~13k-token block of reports — at character 1,025 in one, 2,637 in
  another, never at the start. No breakpoint placement changes that.
- The analysts' tool loops DO re-send: each call repeats 53-84% of the one
  before it. But only tools + system (1.7-2.5k tokens) were marked, so the
  market analyst's third call read 2,494 tokens and paid full price for the
  other 9,748 — the tool results of its own previous two turns.
- The sentiment analyst is a single-shot structured call whose 2.4k-token
  system prompt was marked anyway: written at 1.25x every council, never read.
  At this size that is ~0.48M of the 200-run sample's 1.07M writes.

**The rule now: mark only a prefix that will be sent again.** That means a
tool-loop call — tools bound with the model free to use them. It keeps the
tools and system breakpoints and gains a rolling one on the last block of the
conversation, so each turn reads everything up to the previous turn's end and
writes only what that turn added: re-run on this code, the market analyst's
second and third calls read 2,816 and 6,327 tokens instead of 2,494 each.
Every other call is left unmarked.
Three breakpoints at most, under Anthropic's four.

It is a small saving, not a large one, and the reason is structural: the
loops are 27% of the input and ~3 calls long, so the last and largest turn is
written at 1.25x and never read. The lever on the other 73% is prompt layout
— the shared reports first, as one byte-identical block, in the five debater
prompts: one write and four reads of ~13k tokens, about $0.13 of a $1.09
council — which changes what each agent reads first and so needs a ratings
comparison, not a cost PR.

**Minimum cacheable prefix** (Anthropic docs, checked 2026-09-29): Sonnet 4.6
1,024 tokens, Opus 4.7 2,048, Haiku 4.5 4,096. Shorter prefixes are silently
not cached — no error, no charge. The analysts' tools + system prefixes
measure 1.7-2.5k, above Sonnet's floor but under Haiku's: on routed Haiku
only the rolling breakpoint caches, once the conversation passes 4,096.

**TTL: five minutes, 1h opt-in and not recommended.** Inside a council the
calls that share a prefix are seconds apart. Across tickers in a daily run the
only shared prefix is an analyst's tool schema block (~1-1.6k tokens) because
the system prompt names the ticker before its instructions — about a cent per
ticker at best, while `ttl: "1h"` bills every write, the rolling ones
included, at 2x instead of 1.25x. TRADINGAGENTS_CACHE_TTL=1h still opts in (no
beta header needed); one TTL for every marker keeps Anthropic's
longer-before-shorter ordering rule satisfied.

**Why not the vendor's prompt files.** The prompts are built inside thirteen
upstream agent modules that a subtree pull rewrites. Overriding the one method
that assembles the API payload puts the change in a single place we own, and
a breakpoint is metadata: the characters the model reads are unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_VALID_TTLS = ("5m", "1h")

# Anthropic rejects a request carrying more than four breakpoints with a 400.
# Counted over the whole payload, so a marker upstream placed itself is never
# the one that tips a call over.
_MAX_BREAKPOINTS = 4

# Content blocks the API accepts a breakpoint on. Thinking blocks are cached
# with their turn but cannot carry the marker, and an empty text block is
# rejected outright — so the marker walks back to the nearest block that can.
_MARKABLE_BLOCKS = frozenset({"text", "image", "document", "tool_use", "tool_result"})


def _ttl() -> str | None:
    """Configured TTL, or None for Anthropic's default five minutes.

    Returning None rather than "5m" keeps the payload identical to the
    default request; omitting the key IS the five-minute cache.
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


def _has_marker(block: Any) -> bool:
    return isinstance(block, dict) and block.get("cache_control") is not None


def count_breakpoints(payload: dict[str, Any]) -> int:
    """Every cache_control marker in the request, wherever it sits.

    Top-level `cache_control` (automatic caching) takes a slot too.
    """
    n = 1 if payload.get("cache_control") is not None else 0
    tools = payload.get("tools")
    if isinstance(tools, list):
        n += sum(_has_marker(t) for t in tools)
    system = payload.get("system")
    if isinstance(system, list):
        n += sum(_has_marker(b) for b in system)
    messages = payload.get("messages")
    if isinstance(messages, list):
        for m in messages:
            content = m.get("content") if isinstance(m, dict) else None
            if isinstance(content, list):
                n += sum(_has_marker(b) for b in content)
    return n


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


def _is_tool_loop(payload: dict[str, Any]) -> bool:
    """Whether this call's conversation will be re-sent, one turn longer.

    That is the only case in which a breakpoint on the messages is ever read
    back. An analyst binds its tools with the model free to call them (no
    tool_choice, or "auto"), and every tool call it makes comes straight back
    as the next request. A structured-output call forces its single schema
    tool and is answered in one shot; a debater has no tools at all. Marking
    either would write their whole prompt at 1.25x and read none of it.
    """
    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        return False
    choice = payload.get("tool_choice")
    if choice is None:
        return True
    if isinstance(choice, dict):
        return choice.get("type") == "auto"
    return choice == "auto"


def _markable(block: Any) -> bool:
    if not isinstance(block, dict) or block.get("type") not in _MARKABLE_BLOCKS:
        return False
    if block.get("type") == "text":
        text = block.get("text")
        return isinstance(text, str) and bool(text.strip())
    return True


def _cache_last_message(messages: Any) -> Any:
    """Breakpoint on the last markable block of the last message.

    Copies what it touches: the list, the one message, its content list and
    the one block — the caller's conversation is never mutated.
    """
    if not isinstance(messages, list) or not messages:
        return messages
    last = messages[-1]
    if not isinstance(last, dict):
        return messages
    content = last.get("content")
    if isinstance(content, str):
        if not content.strip():
            return messages
        new_content: list[Any] = [
            {"type": "text", "text": content, "cache_control": _marker()}
        ]
    elif isinstance(content, list):
        new_content = list(content)
        for i in range(len(new_content) - 1, -1, -1):
            if _markable(new_content[i]):
                new_content[i] = {**new_content[i], "cache_control": _marker()}
                break
        else:
            return messages
    else:
        return messages
    return [*messages[:-1], {**last, "content": new_content}]


def apply_cache_control(payload: dict[str, Any]) -> dict[str, Any]:
    """Add cache breakpoints to a request payload. Pure; safe on any shape.

    Only a tool-loop call is marked, because only its prefix is ever sent
    again: tools, then system, then the end of the conversation, each while
    the four-breakpoint budget allows. A single-shot call is returned as it
    came. Re-applying to an already-marked payload changes nothing.
    """
    if not _is_tool_loop(payload):
        return payload
    tools = payload["tools"]
    if _has_marker(tools[-1]) or count_breakpoints(payload) < _MAX_BREAKPOINTS:
        payload["tools"] = _cache_tools(tools)
    system = payload.get("system")
    if system is not None:
        already = isinstance(system, list) and bool(system) and _has_marker(system[-1])
        if already or count_breakpoints(payload) < _MAX_BREAKPOINTS:
            payload["system"] = _cache_system(system)
    if "messages" in payload and count_breakpoints(payload) < _MAX_BREAKPOINTS:
        payload["messages"] = _cache_last_message(payload["messages"])
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
