"""The sentiment analyst must not form an opinion out of nothing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from tradingagents_us.dataflows import sentiment_supplement as ss  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_apewisdom(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ss, "apewisdom_block", lambda t: f"[ApeWisdom — {t}] 28 mentions")


class TestSupplement:
    def test_the_aggregate_is_appended_to_reddits_own_output(self) -> None:
        out = ss.supplement("<3 posts found>", "NVDA")
        assert "<3 posts found>" in out
        assert "28 mentions" in out

    def test_it_is_appended_even_when_reddit_succeeded(self) -> None:
        # The two are different measurements — a handful of posts from two
        # subreddits versus counts across many. An analyst that saw the
        # aggregate only when the posts broke would be reading a different
        # instrument on different days.
        assert "28 mentions" in ss.supplement("r/stocks: 5 posts...", "NVDA")

    def test_a_failing_aggregate_never_costs_the_reddit_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ss, "apewisdom_block", lambda t: (_ for _ in ()).throw(RuntimeError("down"))
        )
        assert ss.supplement("<3 posts found>", "NVDA") == "<3 posts found>"


class TestInstall:
    def test_rebinds_inside_the_analyst_and_is_idempotent(self) -> None:
        from tradingagents.agents.analysts import sentiment_analyst as mod

        original = mod.fetch_reddit_posts
        try:
            assert ss.install() is True
            wrapped = mod.fetch_reddit_posts
            assert getattr(wrapped, "_supplemented", False)
            assert ss.install() is True
            assert mod.fetch_reddit_posts is wrapped  # not re-wrapped
        finally:
            mod.fetch_reddit_posts = original

    def test_reddit_raising_still_yields_the_aggregate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reddit failing is the case this exists for; the analyst must not be
        # left with nothing precisely when nothing is what it had before.
        from tradingagents.agents.analysts import sentiment_analyst as mod

        original = mod.fetch_reddit_posts
        try:
            monkeypatch.setattr(
                mod, "fetch_reddit_posts",
                lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")),
            )
            ss.install()
            out = mod.fetch_reddit_posts("NVDA")
            assert "28 mentions" in out
            assert "not an absence of discussion" in out
        finally:
            mod.fetch_reddit_posts = original

    def test_arguments_are_forwarded(self) -> None:
        from tradingagents.agents.analysts import sentiment_analyst as mod

        original = mod.fetch_reddit_posts
        seen: dict = {}
        try:
            monkeypatch_target = lambda ticker, **kw: (seen.update(kw) or "<posts>")  # noqa: E731
            mod.fetch_reddit_posts = monkeypatch_target
            ss.install()
            mod.fetch_reddit_posts("NVDA", start_date="2026-09-08", end_date="2026-09-12")
            assert seen["start_date"] == "2026-09-08"
        finally:
            mod.fetch_reddit_posts = original
