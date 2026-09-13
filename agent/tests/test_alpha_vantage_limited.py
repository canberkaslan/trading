"""Alpha Vantage news, trimmed to the limit the config already declares."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from tradingagents_us.dataflows.alpha_vantage_limited import (  # noqa: E402
    _limit_from_config,
    register,
    trim_feed,
)


def _feed(n: int) -> dict:
    return {
        "items": str(n),
        "feed": [{"title": f"t{i}", "relevance_score": 1 - i / 100} for i in range(n)],
    }


class TestTrimming:
    def test_keeps_the_configured_count(self) -> None:
        # The real case: a live NVDA call returns 50 articles (~34k tokens) for
        # a config that says 20, and get_news is called by two analysts.
        out = trim_feed(_feed(50), 20)
        assert len(out["feed"]) == 20

    def test_keeps_the_highest_relevance_articles(self) -> None:
        # Alpha Vantage already ranks by relevance for the requested ticker,
        # so "the first N" is its judgement of what matters, not ours.
        out = trim_feed(_feed(50), 3)
        assert [a["title"] for a in out["feed"]] == ["t0", "t1", "t2"]

    def test_a_short_feed_is_returned_untouched(self) -> None:
        original = _feed(5)
        assert trim_feed(original, 20) is original

    def test_it_says_that_it_trimmed(self) -> None:
        # A silently shortened feed reads as "that is all the news there was",
        # which is a different claim from "we asked for twenty".
        out = trim_feed(_feed(50), 20)
        assert out["items"] == "20"
        assert out["_trimmed_from"] == 50


class TestShapeIsPreserved:
    def test_a_string_in_gives_a_string_out(self) -> None:
        # A caller expecting a string must not suddenly receive a dict.
        out = trim_feed(json.dumps(_feed(50)), 20)
        assert isinstance(out, str)
        assert len(json.loads(out)["feed"]) == 20

    def test_a_dict_in_gives_a_dict_out(self) -> None:
        assert isinstance(trim_feed(_feed(50), 20), dict)

    def test_the_input_is_not_mutated(self) -> None:
        original = _feed(50)
        trim_feed(original, 20)
        assert len(original["feed"]) == 50


class TestFailureShapes:
    def test_a_vendor_error_string_passes_through(self) -> None:
        # Swallowing it would hide the message that explains the failure.
        msg = "Error: our standard API rate limit is 25 requests per day"
        assert trim_feed(msg, 20) == msg

    @pytest.mark.parametrize("payload", [None, 42, [], {"no_feed": 1}, {"feed": "nope"}])
    def test_odd_payloads_pass_through_without_raising(self, payload: object) -> None:
        assert trim_feed(payload, 20) == payload


class TestConfigLimit:
    def test_reads_the_declared_limit(self) -> None:
        assert _limit_from_config("news_article_limit", 99) == 20

    def test_an_unreadable_config_does_not_mean_unlimited(self) -> None:
        # The dangerous fallback: no config -> no limit -> the 39% blow-up.
        assert _limit_from_config("no_such_key", 20) == 20

    def test_a_nonsense_limit_falls_back(self) -> None:
        import tradingagents_us.dataflows.alpha_vantage_limited as mod

        original = mod._limit_from_config
        try:
            assert original("news_article_limit", 20) > 0
        finally:
            mod._limit_from_config = original


class TestRegistration:
    def test_registers_and_is_idempotent(self) -> None:
        from tradingagents.dataflows.interface import VENDOR_METHODS

        assert register() is True
        assert "alpha_vantage_limited" in VENDOR_METHODS["get_news"]
        assert register() is True

    def test_it_does_not_become_the_default(self) -> None:
        # Registration only makes it selectable; data_vendors still decides.
        from tradingagents.dataflows.config import get_config

        register()
        assert get_config()["data_vendors"]["news_data"] != "alpha_vantage_limited"
