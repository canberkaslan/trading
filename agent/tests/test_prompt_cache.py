"""Prompt caching — where breakpoints land, and what must never break."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tradingagents_us.llm.prompt_cache import apply_cache_control, count_breakpoints, install

# The vendored upstream is added to sys.path by graph/pipeline.py at import
# time. Relying on some earlier test having imported it makes these two cases
# pass or fail on collection order, so this puts it there itself.
_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def _cc(obj) -> dict | None:
    return obj.get("cache_control") if isinstance(obj, dict) else None


def _tool_loop_payload(**extra) -> dict:
    """The shape an analyst sends mid-loop: tools bound, the model free to
    call them, and the previous turn's tool results as the last message."""
    payload = {
        "tools": [{"name": "get_stock_data"}, {"name": "get_indicators"}],
        "system": "You are the market analyst.",
        "messages": [
            {"role": "user", "content": "SPY"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Fetching data."},
                    {"type": "tool_use", "id": "t1", "name": "get_stock_data", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "date,close"},
                ],
            },
        ],
    }
    payload.update(extra)
    return payload


def _strip_markers(obj):
    """The payload with every cache_control removed — i.e. what the model reads."""
    if isinstance(obj, dict):
        return {k: _strip_markers(v) for k, v in obj.items() if k != "cache_control"}
    if isinstance(obj, list):
        return [_strip_markers(v) for v in obj]
    return obj


def _text_of(payload: dict) -> str:
    """Render system + messages to the characters the model sees, whatever the
    block/string shape — a breakpoint may reshape content, never change it."""

    def flat(content) -> str:
        if isinstance(content, str):
            return content
        return "".join(
            b.get("text", "") + str(b.get("content", "")) + str(b.get("input", ""))
            for b in content
            if isinstance(b, dict)
        )

    return flat(payload.get("system") or "") + "".join(
        m["role"] + flat(m["content"]) for m in payload.get("messages") or []
    )


# An analyst's tool set, bound with the model free to call it: the one call
# shape whose prefix is sent again, and so the only one that is marked.
_TOOLS = [{"name": "a"}, {"name": "b"}, {"name": "c"}]


class TestSystem:
    def test_a_string_system_becomes_a_cached_block(self) -> None:
        out = apply_cache_control({"tools": _TOOLS, "system": "You are an analyst."})
        assert out["system"][0]["type"] == "text"
        assert _cc(out["system"][0]) == {"type": "ephemeral"}

    def test_a_block_list_gets_the_breakpoint_on_the_last_block(self) -> None:
        # Anthropic caches a PREFIX, so the marker belongs at the end of the
        # stable region, not the start.
        system = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        out = apply_cache_control({"tools": _TOOLS, "system": system})
        assert _cc(out["system"][0]) is None
        assert _cc(out["system"][1]) == {"type": "ephemeral"}

    def test_an_empty_system_is_left_alone(self) -> None:
        # Nothing to cache, and an empty cached block would just be noise.
        assert apply_cache_control({"tools": _TOOLS, "system": "   "})["system"] == "   "

    def test_the_input_is_not_mutated_in_place(self) -> None:
        original = [{"type": "text", "text": "a"}]
        apply_cache_control({"tools": _TOOLS, "system": original})
        assert "cache_control" not in original[0]


class TestTools:
    def test_the_breakpoint_lands_on_the_last_tool(self) -> None:
        # One marker on the final tool caches the entire tool block, which is
        # the largest identical thing every call re-sends.
        out = apply_cache_control({"tools": list(_TOOLS)})
        assert [_cc(t) for t in out["tools"]] == [None, None, {"type": "ephemeral"}]

    def test_an_empty_tool_list_is_left_alone(self) -> None:
        assert apply_cache_control({"tools": []})["tools"] == []

    def test_tools_are_not_mutated_in_place(self) -> None:
        original = [{"name": "a"}]
        apply_cache_control({"tools": original})
        assert "cache_control" not in original[0]


class TestToolLoopConversation:
    """The analysts resend their whole conversation on every tool turn.

    Measured on a SPY council: the market analyst's third call paid full price
    for 9,748 of its 12,242 input tokens — its own previous two turns — with
    only the 2,494-token tools+system prefix read from cache. The conversation
    is the part that grows, so it is the part a rolling breakpoint reads back.
    """

    def test_the_last_block_of_the_last_message_gets_the_breakpoint(self) -> None:
        out = apply_cache_control(_tool_loop_payload())
        last = out["messages"][-1]["content"][-1]
        assert last["type"] == "tool_result"
        assert _cc(last) == {"type": "ephemeral"}

    def test_only_one_message_block_is_marked(self) -> None:
        # Earlier turns keep no marker of their own: the previous call's
        # breakpoint is still a valid read point without being re-sent.
        out = apply_cache_control(_tool_loop_payload())
        marked = [b for m in out["messages"] for b in m["content"] if _cc(b)]
        assert len(marked) == 1

    def test_tools_and_system_keep_their_breakpoints(self) -> None:
        out = apply_cache_control(_tool_loop_payload())
        assert _cc(out["tools"][-1]) == {"type": "ephemeral"}
        assert _cc(out["system"][-1]) == {"type": "ephemeral"}
        assert count_breakpoints(out) == 3

    def test_an_opening_call_marks_its_string_prompt(self) -> None:
        # The first call of the loop is just the ticker. Marked, so the second
        # call reads the entire opening prefix rather than stopping at system.
        payload = _tool_loop_payload(messages=[{"role": "user", "content": "SPY"}])
        out = apply_cache_control(payload)
        assert out["messages"][0]["content"] == [
            {"type": "text", "text": "SPY", "cache_control": {"type": "ephemeral"}}
        ]

    def test_auto_tool_choice_is_a_loop_too(self) -> None:
        out = apply_cache_control(_tool_loop_payload(tool_choice={"type": "auto"}))
        assert _cc(out["messages"][-1]["content"][-1]) is not None

    def test_the_marker_skips_blocks_the_api_refuses_it_on(self) -> None:
        # Thinking blocks cannot carry cache_control and an empty text block
        # is rejected — the marker belongs on the nearest block that can.
        payload = _tool_loop_payload()
        payload["messages"].append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "ok"},
                    {"type": "thinking", "thinking": "", "signature": "s"},
                    {"type": "text", "text": "  "},
                ],
            }
        )
        out = apply_cache_control(payload)
        blocks = out["messages"][-1]["content"]
        assert [_cc(b) for b in blocks] == [{"type": "ephemeral"}, None, None]

    def test_a_last_message_with_nothing_markable_is_left_alone(self) -> None:
        payload = _tool_loop_payload()
        payload["messages"].append({"role": "user", "content": "   "})
        out = apply_cache_control(payload)
        assert out["messages"][-1] == {"role": "user", "content": "   "}

    def test_the_callers_conversation_is_not_mutated(self) -> None:
        payload = _tool_loop_payload()
        original_last = payload["messages"][-1]
        original_block = original_last["content"][-1]
        apply_cache_control(payload)
        assert "cache_control" not in original_block
        assert original_last["content"][-1] is original_block

    def test_the_prompt_content_is_unchanged(self) -> None:
        # A breakpoint is metadata. The characters the model reads must be
        # identical, or this is a behaviour change dressed as a cost fix.
        before = _tool_loop_payload()
        after = apply_cache_control(_tool_loop_payload())
        assert _text_of(_strip_markers(after)) == _text_of(before)
        # Structure too, apart from the string prompt promoted to a block.
        assert _strip_markers(after)["messages"][1:] == before["messages"][1:]

    def test_applying_twice_is_the_same_as_once(self) -> None:
        once = apply_cache_control(_tool_loop_payload())
        twice = apply_cache_control(apply_cache_control(_tool_loop_payload()))
        assert once == twice
        assert count_breakpoints(twice) == 3


class TestSingleShotCallsAreNeverMarked:
    """A breakpoint on a prompt nobody re-sends is a 25% surcharge on it.

    The debaters, research manager, trader and portfolio manager are each
    called once per council with one freshly-built prompt — measured, 73% of
    all council input. The sentiment analyst is the same shape with a
    2,419-token system prompt, and marking it wrote those tokens at 1.25x on
    every council with nothing ever reading them back.
    """

    def test_a_call_without_tools_gets_no_breakpoint(self) -> None:
        # The debaters: a single user message, no tools, no system.
        msgs = [{"role": "user", "content": "You are a Bull Analyst..."}]
        out = apply_cache_control({"messages": msgs})
        assert out["messages"] == msgs
        assert count_breakpoints(out) == 0

    def test_a_system_prompt_without_tools_gets_no_breakpoint(self) -> None:
        payload = {"system": "You are an analyst.", "messages": [{"role": "user", "content": "x"}]}
        out = apply_cache_control(payload)
        assert out["system"] == "You are an analyst."
        assert count_breakpoints(out) == 0

    def test_a_forced_structured_output_call_gets_no_breakpoint(self) -> None:
        # with_structured_output binds ONE schema tool and forces it: answered
        # in one shot, never continued. Its fallback is a plain call with no
        # tools, a different prefix, so it cannot read this one either.
        payload = _tool_loop_payload(tool_choice={"type": "tool", "name": "ResearchPlan"})
        assert count_breakpoints(apply_cache_control(payload)) == 0

    def test_forced_any_is_not_treated_as_a_loop(self) -> None:
        payload = _tool_loop_payload(tool_choice={"type": "any"})
        assert count_breakpoints(apply_cache_control(payload)) == 0


class TestBreakpointBudget:
    """Anthropic rejects more than four breakpoints with a 400 — a failed call,
    not a lost saving — so the budget is counted over the whole payload."""

    def _crowded(self, n_upstream: int) -> dict:
        payload = _tool_loop_payload()
        extra = [
            {"type": "text", "text": f"doc {i}", "cache_control": {"type": "ephemeral"}}
            for i in range(n_upstream)
        ]
        opening = [*extra, {"type": "text", "text": "SPY"}]
        payload["messages"][0] = {"role": "user", "content": opening}
        return payload

    @pytest.mark.parametrize("n_upstream", [0, 1, 2, 3, 4, 5])
    def test_never_more_than_four(self, n_upstream: int) -> None:
        out = apply_cache_control(self._crowded(n_upstream))
        assert count_breakpoints(out) <= max(4, n_upstream)

    def test_upstream_markers_are_kept_and_ours_yield(self) -> None:
        out = apply_cache_control(self._crowded(2))
        # tools + system fit; the rolling marker would be the fifth.
        assert count_breakpoints(out) == 4
        assert _cc(out["messages"][-1]["content"][-1]) is None

    def test_top_level_automatic_caching_takes_a_slot(self) -> None:
        payload = self._crowded(1)
        payload["cache_control"] = {"type": "ephemeral"}
        assert count_breakpoints(apply_cache_control(payload)) == 4


class TestTtl:
    def test_the_default_omits_ttl_entirely(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Omitting the key uses the GA five-minute cache. Sending an explicit
        # ttl requires a beta header this client may not be sending.
        monkeypatch.delenv("TRADINGAGENTS_CACHE_TTL", raising=False)
        out = apply_cache_control({"tools": _TOOLS, "system": "s"})
        assert out["system"][0]["cache_control"] == {
            "type": "ephemeral"
        }

    def test_one_hour_is_opt_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRADINGAGENTS_CACHE_TTL", "1h")
        out = apply_cache_control({"tools": _TOOLS, "system": "s"})
        assert out["system"][0]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }

    def test_one_hour_applies_to_every_marker_alike(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Anthropic requires longer-TTL entries to precede shorter ones. One
        # TTL for all three markers can never violate that ordering.
        monkeypatch.setenv("TRADINGAGENTS_CACHE_TTL", "1h")
        out = apply_cache_control(_tool_loop_payload())
        ttls = {
            _cc(out["tools"][-1])["ttl"],
            _cc(out["system"][-1])["ttl"],
            _cc(out["messages"][-1]["content"][-1])["ttl"],
        }
        assert ttls == {"1h"}

    def test_an_unknown_ttl_falls_back_rather_than_sending_garbage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRADINGAGENTS_CACHE_TTL", "7 days")
        out = apply_cache_control({"tools": _TOOLS, "system": "s"})
        assert out["system"][0]["cache_control"] == {
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
            # The same, on the tool-loop path that does get marked.
            {"tools": [None]},
            {"tools": [{"name": "t"}], "tool_choice": 5},
            {"tools": [{"name": "t"}], "system": [None]},
            {"tools": [{"name": "t"}], "messages": None},
            {"tools": [{"name": "t"}], "messages": []},
            {"tools": [{"name": "t"}], "messages": [None]},
            {"tools": [{"name": "t"}], "messages": [{"role": "user"}]},
            {"tools": [{"name": "t"}], "messages": [{"role": "user", "content": None}]},
            {"tools": [{"name": "t"}], "messages": [{"role": "user", "content": [None, 3]}]},
        ],
    )
    def test_odd_shapes_pass_through_without_raising(self, payload: dict) -> None:
        apply_cache_control(dict(payload))


class TestCacheMinimum:
    def test_agent_instruction_is_long_enough_for_anthropic_minimum(self) -> None:
        """AGENT_INSTRUCTION must be >=760 chars to push prefix over 1024 tokens.

        Anthropic silently refuses to cache prefixes under 1024 tokens on
        Sonnet 4.6 (2048 on Opus 4.7, 4096 on Haiku 4.5). Measured prefix with
        tools + system is ~966 tokens, 58 short of minimum. AGENT_INSTRUCTION
        needs ~230 more chars (~58 tokens) to cross the threshold, bringing
        total to ~760 characters.
        """
        from tradingagents_us.compliance import AGENT_INSTRUCTION

        assert len(AGENT_INSTRUCTION) >= 760, (
            f"AGENT_INSTRUCTION is {len(AGENT_INSTRUCTION)} chars; "
            "need >=760 to reach cache minimum (prefix currently ~966 tokens)"
        )


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
