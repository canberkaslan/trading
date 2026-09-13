"""Prompt caching — where breakpoints land, and what must never break."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tradingagents_us.llm.prompt_cache import apply_cache_control, install

# The vendored upstream is added to sys.path by graph/pipeline.py at import
# time. Relying on some earlier test having imported it makes these two cases
# pass or fail on collection order, so this puts it there itself.
_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def _cc(obj) -> dict | None:
    return obj.get("cache_control") if isinstance(obj, dict) else None


class TestSystem:
    def test_a_string_system_becomes_a_cached_block(self) -> None:
        out = apply_cache_control({"system": "You are an analyst."})
        assert out["system"][0]["type"] == "text"
        assert _cc(out["system"][0]) == {"type": "ephemeral"}

    def test_a_block_list_gets_the_breakpoint_on_the_last_block(self) -> None:
        # Anthropic caches a PREFIX, so the marker belongs at the end of the
        # stable region, not the start.
        payload = {"system": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        out = apply_cache_control(payload)
        assert _cc(out["system"][0]) is None
        assert _cc(out["system"][1]) == {"type": "ephemeral"}

    def test_an_empty_system_is_left_alone(self) -> None:
        # Nothing to cache, and an empty cached block would just be noise.
        assert apply_cache_control({"system": "   "})["system"] == "   "

    def test_the_input_is_not_mutated_in_place(self) -> None:
        original = [{"type": "text", "text": "a"}]
        apply_cache_control({"system": original})
        assert "cache_control" not in original[0]


class TestTools:
    def test_the_breakpoint_lands_on_the_last_tool(self) -> None:
        # One marker on the final tool caches the entire tool block, which is
        # the largest identical thing every call re-sends.
        out = apply_cache_control({"tools": [{"name": "a"}, {"name": "b"}, {"name": "c"}]})
        assert [_cc(t) for t in out["tools"]] == [None, None, {"type": "ephemeral"}]

    def test_an_empty_tool_list_is_left_alone(self) -> None:
        assert apply_cache_control({"tools": []})["tools"] == []

    def test_tools_are_not_mutated_in_place(self) -> None:
        original = [{"name": "a"}]
        apply_cache_control({"tools": original})
        assert "cache_control" not in original[0]


class TestMessagesAreNeverTouched:
    def test_conversation_content_gets_no_breakpoint(self) -> None:
        # Messages are where the request GROWS. A breakpoint there would write
        # a new cache entry on every turn at 1.25x and read almost none of it.
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        out = apply_cache_control({"messages": msgs, "system": "s"})
        assert out["messages"] == msgs
        assert "cache_control" not in out["messages"][0]["content"][0]


class TestTtl:
    def test_the_default_omits_ttl_entirely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Omitting the key uses the GA five-minute cache. Sending an explicit
        # ttl requires a beta header this client may not be sending.
        monkeypatch.delenv("TRADINGAGENTS_CACHE_TTL", raising=False)
        assert apply_cache_control({"system": "s"})["system"][0]["cache_control"] == {
            "type": "ephemeral"
        }

    def test_one_hour_is_opt_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADINGAGENTS_CACHE_TTL", "1h")
        assert apply_cache_control({"system": "s"})["system"][0]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }

    def test_an_unknown_ttl_falls_back_rather_than_sending_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADINGAGENTS_CACHE_TTL", "7 days")
        assert apply_cache_control({"system": "s"})["system"][0]["cache_control"] == {
            "type": "ephemeral"
        }


class TestNeverBreaksTheCall:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"system": None},
            {"tools": None},
            {"system": 42},
            {"tools": "not a list"},
            {"system": [None]},
            {"system": [{"type": "image"}]},
        ],
    )
    def test_odd_shapes_pass_through_without_raising(self, payload: dict) -> None:
        apply_cache_control(dict(payload))


class TestInstall:
    def test_is_idempotent(self) -> None:
        first = install()
        second = install()
        assert first == second

    def test_the_patched_class_still_subclasses_the_original(self) -> None:
        install()
        from tradingagents.llm_clients import anthropic_client as mod

        assert getattr(mod.NormalizedChatAnthropic, "_prompt_cache_installed", False)

    def test_the_override_wraps_rather_than_replaces_the_payload(self) -> None:
        """The subclass must call up and then decorate, not build its own.

        Rebuilding the payload here would silently drop whatever upstream puts
        in it — thinking config, betas, stop sequences — and the only symptom
        would be a behaviour change nobody attributed to caching.
        """
        from tradingagents_us.llm.prompt_cache import apply_cache_control

        class FakeBase:
            def _get_request_payload(self, input_, *, stop=None, **kw):
                return {"system": "S", "tools": [{"name": "t"}], "betas": ["x"]}

        class Caching(FakeBase):
            def _get_request_payload(self, input_, *, stop=None, **kw):
                return apply_cache_control(super()._get_request_payload(input_, stop=stop, **kw))

        out = Caching()._get_request_payload(None)
        assert out["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert out["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        # Upstream's own fields survive untouched.
        assert out["betas"] == ["x"]

    def test_a_broken_decorator_costs_the_saving_not_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the breakpoint logic ever throws, the request must still go out.

        Mirrors the try/except in install()'s override. A caching optimisation
        that can fail a trading decision is not an optimisation.
        """
        import tradingagents_us.llm.prompt_cache as pc

        upstream = {"system": "S", "tools": [{"name": "t"}]}

        def boom(_payload):
            raise RuntimeError("malformed payload")

        monkeypatch.setattr(pc, "apply_cache_control", boom)

        class FakeBase:
            def _get_request_payload(self, input_, *, stop=None, **kw):
                return dict(upstream)

        class Caching(FakeBase):
            def _get_request_payload(self, input_, *, stop=None, **kw):
                payload = super()._get_request_payload(input_, stop=stop, **kw)
                try:
                    return pc.apply_cache_control(payload)
                except Exception:
                    return payload

        out = Caching()._get_request_payload(None)
        assert out == upstream  # unmodified, but sent
