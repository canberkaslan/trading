"""Give the cheap roles a cheap model, without forking the graph.

Upstream is strictly two-tier: `GraphSetup` receives `quick_thinking_llm` and
`deep_thinking_llm`, and `setup.py` hands the quick one to ten agents — the
four analysts, both researchers, the trader and all three risk debators. On
this box that means claude-sonnet-5 answers every one of them.

Six of those ten are heuristic: they read a prepared block and produce an
opinion, without the long-horizon reasoning the researchers and the trader do.
`routing.py` has said so since it was written, and nothing has ever imported
it, so the cheaper tier it describes has never run.

**Why patch the factories rather than the LLM.** The call sites in
`setup.py:76-91` are what decide which agent gets which client, and they read
`self.quick_thinking_llm` — one object, no idea which agent is asking. There
is no hook between "the graph picks a client" and "the agent is built". The
factories are the seam: each `create_*` takes exactly one argument, the llm,
so replacing the name in `setup.py`'s namespace lets each agent be handed its
own client while everything else stays upstream's.

**What this deliberately does not touch.** The research manager and the
portfolio manager stay on the deep model — they are where the decision is
actually made. The fundamentals and news analysts, both researchers and the
trader stay on the quick model: they read long documents and their output is
parsed downstream, and `pipeline.py` now RAISES on an unparseable portfolio
manager rather than inventing a Hold, so a model that formats differently
fails the run loudly rather than cheaply.

Saving something on the analysis is worth it. Saving something on the
judgement is not the same trade, and this module does not make it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from tradingagents_us.llm.routing import AGENT_MODEL_MAP, HAIKU

log = logging.getLogger(__name__)

# The factory in `setup.py`'s namespace for each role named in AGENT_MODEL_MAP.
# Only roles that appear here can be routed; the rest keep upstream's client.
_FACTORY_FOR_ROLE: dict[str, str] = {
    "market_analyst": "create_market_analyst",
    "sentiment_analyst": "create_sentiment_analyst",
    "social_media_analyst": "create_sentiment_analyst",
    "aggressive_debator": "create_aggressive_debator",
    "neutral_debator": "create_neutral_debator",
    "conservative_debator": "create_conservative_debator",
}


def is_enabled() -> bool:
    """Off unless asked for.

    A model change alters what the agents say, not just what they cost, so it
    does not arrive as a silent side effect of deploying. Set
    TRADINGAGENTS_AGENT_ROUTING=1 to turn it on, and compare a run against the
    recorded cost and ratings before leaving it on.
    """
    return (os.environ.get("TRADINGAGENTS_AGENT_ROUTING") or "").strip() in ("1", "true", "yes")


def _cheap_llm(temperature: float = 0.1, callbacks: list[Any] | None = None) -> Any:
    """Build the cheap client through the SAME factory upstream uses.

    Constructing `ChatAnthropic` directly here was a quiet, serious bug. Two
    things in this system attach to the client the factory produces, and both
    would have missed every routed call:

    - `UsageCollector` reaches an LLM only as a constructor `callbacks` kwarg
      that `TradingAgentsGraph` forwards through `create_llm_client`. A client
      built outside it is invisible, so the routed roles would have reported
      ZERO tokens and ZERO cost — and the before/after comparison this module's
      own docstring prescribes would have read that as a saving of their entire
      share rather than the real fraction. The measurement would have flattered
      the change, which is the worst direction for an error to run.
    - `prompt_cache.install()` patches `NormalizedChatAnthropic`, which only
      this factory instantiates. Bypassing it also silently dropped caching
      from the routed calls.

    Going through the factory fixes both in one move, and carries base_url,
    max_tokens and max_retries along with them.
    """
    from tradingagents.llm_clients import create_llm_client

    kwargs: dict[str, Any] = {"temperature": temperature}
    if callbacks:
        kwargs["callbacks"] = callbacks
    return create_llm_client(provider="anthropic", model=HAIKU, **kwargs).get_llm()


def _rebind(module: Any, factory_name: str, llm: Any) -> bool:
    """Replace `factory_name` in `module` so it builds its agent with `llm`."""
    original: Callable[..., Any] | None = getattr(module, factory_name, None)
    if original is None:
        return False
    if getattr(original, "_agent_routing_applied", False):
        return True

    def routed(_upstream_llm: Any = None, *args: Any, **kwargs: Any) -> Any:
        # The client the graph offered is discarded on purpose — choosing it is
        # the entire point. Everything else upstream passes is forwarded, so a
        # factory that grows a second parameter does not silently lose it.
        return original(llm, *args, **kwargs)

    routed._agent_routing_applied = True  # type: ignore[attr-defined]
    routed._wraps = original  # type: ignore[attr-defined]
    setattr(module, factory_name, routed)
    return True


def install(callbacks: list[Any] | None = None) -> list[str]:
    """Point the cheap roles at the cheap model. Returns the roles routed.

    `callbacks` must carry the same UsageCollector the graph is given, or the
    routed calls are absent from the cost record — see `_cheap_llm`.

    Idempotent, and a no-op when disabled or when upstream has moved — an empty
    list means nothing changed, which is the safe outcome.
    """
    if not is_enabled():
        return []

    try:
        from tradingagents.graph import setup as setup_mod
    except ImportError:
        log.warning("agent routing: upstream graph.setup not importable — skipped")
        return []

    try:
        llm = _cheap_llm(callbacks=callbacks)
    except Exception:  # noqa: BLE001
        log.warning("agent routing: could not build the cheap client — skipped", exc_info=True)
        return []

    routed: list[str] = []
    seen_factories: set[str] = set()
    for role, model in AGENT_MODEL_MAP.items():
        if model != HAIKU:
            continue
        factory = _FACTORY_FOR_ROLE.get(role)
        # Two roles map to one factory (sentiment/social share create_sentiment_analyst),
        # so rebinding once is correct and the second pass must not double-wrap.
        if factory is None or factory in seen_factories:
            continue
        if _rebind(setup_mod, factory, llm):
            seen_factories.add(factory)
            routed.append(role)

    if routed:
        log.info("agent routing: %s -> %s", ", ".join(sorted(routed)), HAIKU)
    return routed
