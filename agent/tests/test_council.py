"""LLM council — parsing + synthesis + fail-safe (no network)."""

from __future__ import annotations

import pytest

from tradingagents_us.llm import council
from tradingagents_us.llm.council import _parse_confidence, _parse_rating, council_review


class TestParse:
    def test_rating_variants(self) -> None:
        assert _parse_rating("blah\nRATING: Overweight") == "Overweight"
        assert _parse_rating("RATING:Hold") == "Hold"
        assert _parse_rating("my rating: sell now") == "Sell"
        assert _parse_rating("no verdict here") is None

    def test_confidence(self) -> None:
        assert _parse_confidence("CONFIDENCE: 72") == 72
        assert _parse_confidence("confidence 150") == 100  # clamped
        assert _parse_confidence("none") == 50  # default


class TestCouncilReview:
    def test_chair_synthesizes_votes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(council, "_call_openrouter", lambda m, s, u: "Cautious.\nRATING: Hold")
        monkeypatch.setattr(
            council, "_call_anthropic",
            lambda m, s, u: "Council split lower.\nRATING: Hold\nCONFIDENCE: 65",
        )
        r = council_review("NVDA", "digest", "Overweight", "house says buy")
        assert r is not None
        assert r.final_rating == "Hold"
        assert r.confidence == 65
        assert len(r.votes) == 2
        assert all(v.rating == "Hold" for v in r.votes)

    def test_voter_failure_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}

        def flaky(model, s, u):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("openrouter 500")
            return "RATING: Buy"

        monkeypatch.setattr(council, "_call_openrouter", flaky)
        monkeypatch.setattr(council, "_call_anthropic", lambda m, s, u: "RATING: Buy\nCONFIDENCE: 80")
        r = council_review("AAPL", "digest", "Buy", "house")
        assert r is not None
        assert len(r.votes) == 1  # one voter dropped, one survived

    def test_chair_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(council, "_call_openrouter", lambda m, s, u: "RATING: Hold")

        def boom(m, s, u):
            raise RuntimeError("anthropic down")

        monkeypatch.setattr(council, "_call_anthropic", boom)
        assert council_review("AAPL", "d", "Buy", "h") is None  # keep house decision

    def test_unparseable_chair_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(council, "_call_openrouter", lambda m, s, u: "RATING: Hold")
        monkeypatch.setattr(council, "_call_anthropic", lambda m, s, u: "I cannot decide.")
        assert council_review("AAPL", "d", "Buy", "h") is None
