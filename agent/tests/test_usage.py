"""Token/cost accounting — the measurement that makes caching verifiable."""

from __future__ import annotations

from types import SimpleNamespace

from tradingagents_us.llm.usage import UsageCollector, _price_for


def _resp(model: str, *, inp: int, out: int, cache_read: int = 0, cache_write: int = 0):
    """Mimic a LangChain LLMResult carrying an AIMessage with usage_metadata."""
    msg = SimpleNamespace(
        usage_metadata={
            "input_tokens": inp,
            "output_tokens": out,
            "input_token_details": {"cache_read": cache_read, "cache_creation": cache_write},
        },
        response_metadata={"model_name": model},
    )
    return SimpleNamespace(generations=[[SimpleNamespace(message=msg)]])


class TestPricing:
    def test_a_dated_snapshot_prices_as_its_family(self) -> None:
        assert _price_for("claude-haiku-4-5-20251001") == _price_for("claude-haiku-4-5")

    def test_longest_prefix_wins(self) -> None:
        # "claude-sonnet-5" must not be matched by a shorter, cheaper entry.
        assert _price_for("claude-sonnet-5") == (3.0, 15.0)

    def test_an_unknown_model_has_no_price(self) -> None:
        assert _price_for("some-other-model") is None


class TestAccumulation:
    def test_sums_across_calls(self) -> None:
        c = UsageCollector()
        c.on_llm_end(_resp("claude-sonnet-5", inp=1000, out=500))
        c.on_llm_end(_resp("claude-sonnet-5", inp=2000, out=100))
        assert c.usage.calls == 2
        assert c.usage.input_tokens == 3000
        assert c.usage.output_tokens == 600

    def test_cost_matches_the_published_rate(self) -> None:
        c = UsageCollector()
        c.on_llm_end(_resp("claude-sonnet-5", inp=1_000_000, out=1_000_000))
        # $3 per Mtok in + $15 per Mtok out
        assert round(c.usage.cost_usd, 6) == 18.0


class TestCacheAccounting:
    def test_cached_input_is_not_billed_twice(self) -> None:
        # langchain reports input_tokens as the TOTAL including cached parts.
        # Billing the full figure at 1.0x AND the cache portions again would
        # overstate cost exactly where caching is supposed to help.
        c = UsageCollector()
        c.on_llm_end(_resp("claude-sonnet-5", inp=1000, out=0, cache_read=900))
        assert c.usage.input_tokens == 100
        assert c.usage.cache_read_tokens == 900
        # 100 fresh @ $3/Mtok + 900 cached @ 0.10x
        assert round(c.usage.cost_usd, 8) == round((100 * 3 + 900 * 3 * 0.10) / 1e6, 8)

    def test_a_cache_write_costs_more_than_fresh_input(self) -> None:
        fresh = UsageCollector()
        fresh.on_llm_end(_resp("claude-sonnet-5", inp=1000, out=0))
        written = UsageCollector()
        written.on_llm_end(_resp("claude-sonnet-5", inp=1000, out=0, cache_write=1000))
        assert written.usage.cost_usd > fresh.usage.cost_usd

    def test_hit_rate_is_what_says_whether_caching_works(self) -> None:
        c = UsageCollector()
        c.on_llm_end(_resp("claude-sonnet-5", inp=1000, out=0, cache_read=800))
        assert c.usage.cache_hit_rate == 0.8

    def test_hit_rate_is_none_before_anything_runs(self) -> None:
        assert UsageCollector().usage.cache_hit_rate is None

    def test_a_zero_hit_rate_after_caching_is_a_loss_signal(self) -> None:
        # All writes, no reads: the prefix is not stable and the 1.25x is waste.
        c = UsageCollector()
        c.on_llm_end(_resp("claude-sonnet-5", inp=1000, out=0, cache_write=1000))
        assert c.usage.cache_hit_rate == 0.0


class TestFailuresCostAMeasurementNotADecision:
    def test_an_unpriced_model_still_counts_tokens_and_is_flagged(self) -> None:
        # A missing price must look like a gap, never like a free model.
        c = UsageCollector()
        c.on_llm_end(_resp("claude-unreleased-9", inp=1000, out=500))
        assert c.usage.input_tokens == 1000
        assert c.usage.cost_usd == 0.0
        assert "claude-unreleased-9" in c.usage.unpriced_models

    def test_a_response_with_no_usage_is_skipped_quietly(self) -> None:
        msg = SimpleNamespace(usage_metadata=None, response_metadata={})
        c = UsageCollector()
        c.on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=msg)]]))
        assert c.usage.calls == 0

    def test_a_malformed_payload_never_raises(self) -> None:
        c = UsageCollector()
        c.on_llm_end(object())
        c.on_llm_end(None)
        c.on_llm_end(SimpleNamespace(generations=None))
        assert c.usage.calls == 0

    def test_negative_arithmetic_cannot_produce_negative_input(self) -> None:
        # If a provider ever reports cache tokens exceeding the total.
        c = UsageCollector()
        c.on_llm_end(_resp("claude-sonnet-5", inp=100, out=0, cache_read=500))
        assert c.usage.input_tokens == 0
