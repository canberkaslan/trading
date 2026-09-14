"""Translate the finished report, rather than making every agent write twice.

Setting output_language to two languages would have doubled the output of all
eighteen council calls — and output is 53% of the bill, on models priced for
reasoning. Measured: ~$0.61 a ticker versus ~$0.012 for one cheap pass over the
final report.
"""

from __future__ import annotations

import pytest

from tradingagents_us.llm import translate as tr


class TestOffByDefault:
    def test_disabled_unless_asked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A deploy must not start spending on its own.
        monkeypatch.delenv("TRANSLATE_REPORTS", raising=False)
        assert tr.is_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    def test_the_accepted_spellings(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("TRANSLATE_REPORTS", value)
        assert tr.is_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "", "maybe"])
    def test_anything_else_is_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("TRANSLATE_REPORTS", value)
        assert tr.is_enabled() is False


class TestItUsesTheCheapTier:
    def test_the_default_model_is_haiku(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The whole argument for this design is the price difference. A default
        # that drifted to a reasoning model would silently undo it.
        monkeypatch.delenv("TRANSLATION_MODEL", raising=False)
        assert "haiku" in tr._model()

    def test_it_is_overridable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRANSLATION_MODEL", "claude-sonnet-5")
        assert tr._model() == "claude-sonnet-5"


class TestTheInstruction:
    def test_it_pins_the_things_that_must_not_move(self) -> None:
        # Numbers, tickers and dates carried through a translation are where a
        # plausible-sounding error would do real damage.
        for needle in ("EXACTLY", "Do not convert currencies", "markdown"):
            assert needle in tr._SYSTEM

    def test_the_disclaimer_must_survive_in_full(self) -> None:
        # The compliance sentence is the part that must not be summarised.
        assert "shortened" in tr._SYSTEM or "in full" in tr._SYSTEM

    def test_it_forbids_summarising(self) -> None:
        assert "Never summarise" in tr._SYSTEM


class TestFailureIsAbsence:
    def test_empty_input_returns_none(self) -> None:
        assert tr.translate_to_turkish("") is None
        assert tr.translate_to_turkish("   ") is None

    def test_a_failing_model_returns_none_rather_than_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A missing translation costs a convenience. A decision that failed to
        # be recorded costs something else.
        import sys
        import types

        fake = types.ModuleType("langchain_anthropic")

        class Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("no credentials")

        fake.ChatAnthropic = Boom  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "langchain_anthropic", fake)
        assert tr.translate_to_turkish("Hold MSFT at existing weight.") is None

    def test_an_empty_completion_is_none_not_an_empty_report(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys
        import types

        fake = types.ModuleType("langchain_anthropic")

        class Blank:
            def __init__(self, *a, **k):
                pass

            def invoke(self, messages):
                return types.SimpleNamespace(content="   ")

        fake.ChatAnthropic = Blank  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "langchain_anthropic", fake)
        assert tr.translate_to_turkish("Hold.") is None

    def test_typed_content_blocks_are_joined(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys
        import types

        fake = types.ModuleType("langchain_anthropic")

        class Blocks:
            def __init__(self, *a, **k):
                pass

            def invoke(self, messages):
                return types.SimpleNamespace(
                    content=[{"type": "text", "text": "MSFT"}, {"type": "text", "text": " tut."}]
                )

        fake.ChatAnthropic = Blocks  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "langchain_anthropic", fake)
        assert tr.translate_to_turkish("Hold MSFT.") == "MSFT tut."

    def test_the_collector_is_forwarded_so_the_cost_is_recorded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Spending it invisibly is the exact mistake the routed Haiku client
        # made before it was fixed.
        import sys
        import types

        seen: dict = {}
        fake = types.ModuleType("langchain_anthropic")

        class Capture:
            def __init__(self, *a, **k):
                seen.update(k)

            def invoke(self, messages):
                return types.SimpleNamespace(content="çeviri")

        fake.ChatAnthropic = Capture  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "langchain_anthropic", fake)
        sentinel = object()
        tr.translate_to_turkish("Hold.", callbacks=[sentinel])
        assert seen.get("callbacks") == [sentinel]
        # And a translation must not be creative.
        assert seen.get("temperature") == 0
