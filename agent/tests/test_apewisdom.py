"""ApeWisdom retail-attention dataflow — parsing, framing, degradation (no network)."""

from __future__ import annotations

import pytest

from tradingagents_us.dataflows.apewisdom import (
    Mention,
    _to_mention,
    apewisdom_block,
    find,
)


def _m(**kw) -> Mention:
    base = dict(rank=3, ticker="NVDA", name="NVIDIA", mentions=17, upvotes=81,
                rank_24h_ago=8, mentions_24h_ago=31)
    base.update(kw)
    return Mention(**base)  # type: ignore[arg-type]


class TestParsing:
    def test_maps_the_observed_payload(self) -> None:
        # Field names taken from a real response, not the docs.
        row = {"rank": 1, "ticker": "mu", "name": "Micron Technology",
               "mentions": 32, "upvotes": 79, "rank_24h_ago": 3, "mentions_24h_ago": 79}
        m = _to_mention(row)
        assert m.ticker == "MU"  # normalised
        assert (m.mentions, m.upvotes, m.rank_24h_ago) == (32, 79, 3)

    def test_string_numbers_parse(self) -> None:
        # Some rows return these as strings; a silent 0 would be a lie.
        assert _to_mention({"mentions": "32"}).mentions == 32

    def test_unparseable_prior_is_none_not_zero(self) -> None:
        m = _to_mention({"ticker": "X", "mentions": 5, "mentions_24h_ago": "n/a"})
        assert m.mentions_24h_ago is None


class TestMentionChange:
    def test_computes_the_change(self) -> None:
        assert _m(mentions=17, mentions_24h_ago=31).mention_change_pct == pytest.approx(-45.16, abs=0.1)

    def test_no_prior_figure_is_none_not_zero_percent(self) -> None:
        # A new entrant showing 0% would read as the quietest name on the list
        # when it is the opposite: nothing to compare against.
        assert _m(mentions_24h_ago=None).mention_change_pct is None

    def test_a_zero_prior_does_not_divide(self) -> None:
        assert _m(mentions_24h_ago=0).mention_change_pct is None


class TestBlockFraming:
    def test_always_says_this_is_not_sentiment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The whole risk of this feed: an LLM handed a bare count reads "many
        # mentions" as "bullish". Both the hit and the miss path must caveat.
        import tradingagents_us.dataflows.apewisdom as mod

        class FakeClient:
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def top_mentions(self, pages=1): return [_m()]

        monkeypatch.setattr(mod, "ApeWisdomClient", FakeClient)
        assert "not sentiment" in apewisdom_block("NVDA")
        assert "not sentiment" in apewisdom_block("ZZZZ")

    def test_absence_is_reported_as_a_finding(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tradingagents_us.dataflows.apewisdom as mod

        class FakeClient:
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def top_mentions(self, pages=1): return [_m()]

        monkeypatch.setattr(mod, "ApeWisdomClient", FakeClient)
        out = apewisdom_block("ZZZZ")
        assert "no meaningful retail chatter" in out

    def test_a_missing_prior_does_not_print_a_fake_percentage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tradingagents_us.dataflows.apewisdom as mod

        class FakeClient:
            def __enter__(self): return self
            def __exit__(self, *a): return None
            def top_mentions(self, pages=1): return [_m(mentions_24h_ago=None)]

        monkeypatch.setattr(mod, "ApeWisdomClient", FakeClient)
        out = apewisdom_block("NVDA")
        assert "no prior figure" in out
        assert "+0%" not in out


class TestLookup:
    def test_is_case_insensitive(self) -> None:
        assert find([_m()], "nvda") is not None

    def test_returns_none_when_absent(self) -> None:
        assert find([_m()], "AAPL") is None


class TestDegradation:
    def test_a_transport_failure_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tradingagents_us.dataflows.apewisdom as mod

        class Boom:
            def __init__(self, *a, **k): raise RuntimeError("connection reset")

        monkeypatch.setattr(mod, "ApeWisdomClient", Boom)
        out = apewisdom_block("NVDA")
        assert "fetch failed" in out
        assert "NVDA" in out
