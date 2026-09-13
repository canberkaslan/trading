"""Per-agent model routing — what it rebinds, and what it must never touch."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from tradingagents_us.llm.agent_routing import _FACTORY_FOR_ROLE, _rebind, install, is_enabled
from tradingagents_us.llm.routing import AGENT_MODEL_MAP, HAIKU

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


class TestOffByDefault:
    def test_disabled_unless_asked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A model change alters what the agents SAY, not just what they cost,
        # so it must not arrive as a side effect of deploying.
        monkeypatch.delenv("TRADINGAGENTS_AGENT_ROUTING", raising=False)
        assert is_enabled() is False
        assert install() == []

    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    def test_the_accepted_spellings(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("TRADINGAGENTS_AGENT_ROUTING", value)
        assert is_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "", "maybe"])
    def test_anything_else_is_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("TRADINGAGENTS_AGENT_ROUTING", value)
        assert is_enabled() is False


class TestRebind:
    def test_the_agent_is_built_with_our_client_not_the_offered_one(self) -> None:
        mod = types.SimpleNamespace(create_x=lambda llm: f"built-with:{llm}")
        _rebind(mod, "create_x", "CHEAP")
        # The graph offers its own client; discarding it is the whole point.
        assert mod.create_x("EXPENSIVE") == "built-with:CHEAP"

    def test_extra_arguments_are_forwarded(self) -> None:
        # A factory that grows a second parameter must not silently lose it.
        mod = types.SimpleNamespace(create_x=lambda llm, memory=None: (llm, memory))
        _rebind(mod, "create_x", "CHEAP")
        assert mod.create_x("EXPENSIVE", memory="M") == ("CHEAP", "M")

    def test_is_idempotent_and_does_not_nest_wrappers(self) -> None:
        calls: list[str] = []
        mod = types.SimpleNamespace(create_x=calls.append)
        _rebind(mod, "create_x", "A")
        first = mod.create_x
        _rebind(mod, "create_x", "B")
        assert mod.create_x is first  # not re-wrapped
        mod.create_x("ignored")
        assert calls == ["A"]

    def test_a_missing_factory_is_reported_not_raised(self) -> None:
        assert _rebind(types.SimpleNamespace(), "create_nope", "CHEAP") is False


class TestWhatIsRouted:
    def test_every_cheap_role_has_a_factory_mapping(self) -> None:
        # A role marked cheap with no factory entry would silently stay on the
        # expensive model while the config claimed otherwise.
        cheap = {r for r, m in AGENT_MODEL_MAP.items() if m == HAIKU}
        assert cheap <= set(_FACTORY_FOR_ROLE)

    def test_the_judgement_roles_are_not_in_the_cheap_map(self) -> None:
        # These are where the decision is actually made. Saving on the
        # analysis is a different trade from saving on the judgement.
        for role in ("research_manager", "portfolio_manager", "trader"):
            assert AGENT_MODEL_MAP[role] != HAIKU
            assert role not in _FACTORY_FOR_ROLE

    def test_the_long_document_readers_are_not_downgraded(self) -> None:
        # Their output is parsed downstream and pipeline.py now RAISES on an
        # unparseable portfolio manager rather than inventing a Hold.
        for role in ("fundamentals_analyst", "news_analyst", "bull_researcher", "bear_researcher"):
            assert AGENT_MODEL_MAP[role] != HAIKU


class TestInstallAgainstTheRealGraph:
    def test_rebinds_the_expected_factories_once_each(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADINGAGENTS_AGENT_ROUTING", "1")
        import tradingagents_us.llm.agent_routing as ar

        monkeypatch.setattr(ar, "_cheap_llm", lambda temperature=0.1, callbacks=None: "CHEAP")
        from tradingagents.graph import setup as setup_mod

        originals = {n: getattr(setup_mod, n) for n in set(_FACTORY_FOR_ROLE.values())}
        try:
            routed = install()
            # sentiment_analyst and social_media_analyst share create_sentiment_analyst,
            # so install() reports one role per DISTINCT factory — six cheap roles
            # become five routed entries, not six.
            assert len(routed) == len(set(_FACTORY_FOR_ROLE.values()))
            assert len({_FACTORY_FOR_ROLE[r] for r in routed}) == len(routed)
            for name in set(_FACTORY_FOR_ROLE.values()):
                assert getattr(getattr(setup_mod, name), "_agent_routing_applied", False)
        finally:
            for n, f in originals.items():
                setattr(setup_mod, n, f)

    def test_the_shared_factory_is_not_wrapped_twice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # sentiment_analyst and social_media_analyst both point at
        # create_sentiment_analyst; wrapping it twice would nest the wrappers.
        monkeypatch.setenv("TRADINGAGENTS_AGENT_ROUTING", "1")
        import tradingagents_us.llm.agent_routing as ar

        monkeypatch.setattr(ar, "_cheap_llm", lambda temperature=0.1, callbacks=None: "CHEAP")
        from tradingagents.graph import setup as setup_mod

        original = setup_mod.create_sentiment_analyst
        try:
            install()
            wrapped = setup_mod.create_sentiment_analyst
            assert getattr(wrapped, "_wraps", None) is original
        finally:
            setup_mod.create_sentiment_analyst = original


class TestTheCollectorReachesTheRoutedClient:
    """The routed calls must land in the SAME cost record as the rest.

    Building the client outside `create_llm_client` was a quiet, serious bug:
    UsageCollector attaches only as a constructor callbacks kwarg that the
    factory forwards, so routed calls reported zero tokens and zero cost — and
    the before/after this module prescribes would have read that as a saving of
    their entire share. The error ran in the flattering direction.
    """

    def test_install_forwards_callbacks_to_the_client_builder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADINGAGENTS_AGENT_ROUTING", "1")
        import tradingagents_us.llm.agent_routing as ar

        seen: dict = {}

        def fake(temperature=0.1, callbacks=None):
            seen["callbacks"] = callbacks
            return "CHEAP"

        monkeypatch.setattr(ar, "_cheap_llm", fake)
        from tradingagents.graph import setup as setup_mod

        originals = {n: getattr(setup_mod, n) for n in set(_FACTORY_FOR_ROLE.values())}
        try:
            sentinel = object()
            install(callbacks=[sentinel])
            assert seen["callbacks"] == [sentinel]
        finally:
            for n, f in originals.items():
                setattr(setup_mod, n, f)

    def test_the_builder_goes_through_the_upstream_factory(self) -> None:
        # Not langchain_anthropic.ChatAnthropic directly: the factory is also
        # what instantiates the prompt-cache-patched class, so bypassing it
        # dropped caching from the routed calls too.
        import inspect

        import tradingagents_us.llm.agent_routing as ar

        src = inspect.getsource(ar._cheap_llm)
        assert "create_llm_client" in src
        assert "from langchain_anthropic import ChatAnthropic" not in src
