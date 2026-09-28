"""ADR-009 gate step A: the sentiment-node-only diff script. No model calls."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from scripts import commentator_sentiment_diff as diff  # noqa: E402
from tradingagents_us.storage.commentator import StoredItem  # noqa: E402


def _item(sid: str, when: datetime, stance: str = "unstated") -> StoredItem:
    return StoredItem(
        item_id=f"youtube:{sid}", source="youtube", source_id=sid, published_at=when,
        tickers=("META",), macro_topics=(), stance={"META": stance}, claim_en="p",
        is_promo=False, is_market_content=True,
    )


class TestSentimentDiff:
    def test_the_header_is_parsed(self) -> None:
        report = "**Overall Sentiment:** **Mildly Bullish** (Score: 6.0/10)\n**Confidence:** Low"
        assert diff.parse_header(report) == ("Mildly Bullish", 6.0)
        assert diff.parse_header("free text, no header") is None

    def test_points_are_dates_whose_prompt_would_show_an_item(self) -> None:
        items = [_item("a", datetime(2026, 9, 22, 9, tzinfo=UTC))]
        points = diff.candidate_points(items, ["META", "NVDA"], max_dates=3,
                                       today=date(2026, 9, 28))
        # 09-22 09:00 is after 09-22 00:00, so the first admissible trade date is 09-23.
        assert points == [("META", "2026-09-25"), ("META", "2026-09-24"),
                          ("META", "2026-09-23")]

    def test_unstated_points_that_move_are_flagged_as_a_prompt_bug(self) -> None:
        diffs = [diff.PointDiff("META", "d", 1, False, 5.0, 6.5)]
        assert "PROMPT BUG" in diff.summarize(diffs)["verdict"]

    def test_no_movement_says_stop(self) -> None:
        diffs = [diff.PointDiff("META", "d", 1, True, 5.0, 5.2),
                 diff.PointDiff("SPY", "d", 1, False, 6.0, 6.0)]
        assert "stop" in diff.summarize(diffs)["verdict"]

    def test_stated_movement_justifies_step_b(self) -> None:
        diffs = [diff.PointDiff("META", "d", 2, True, 5.0, 6.5),
                 diff.PointDiff("SPY", "d", 1, False, 6.0, 6.1)]
        s = diff.summarize(diffs)
        assert "step B" in s["verdict"]
        assert s["stance_stated"]["moved"] == 1 and s["stance_unstated"]["moved"] == 0

    def test_both_arms_read_the_same_fetched_inputs(self) -> None:
        # Two live fetches minutes apart can differ; that difference must not
        # be read as the feed's effect.
        import types

        calls: list[str] = []

        def fetch_reddit_posts(ticker, **kw):
            calls.append(ticker)
            return f"reddit #{len(calls)}"

        tool = types.SimpleNamespace(func=lambda *a: f"news #{len(calls)}")
        mod = types.SimpleNamespace(fetch_reddit_posts=fetch_reddit_posts, get_news=tool)
        with diff.frozen_inputs(mod):
            first = mod.fetch_reddit_posts("META", start_date="a")
            second = mod.fetch_reddit_posts("META", start_date="a")
            assert mod.get_news.func("META") == mod.get_news.func("META")
        assert first == second and calls == ["META"]
        assert mod.fetch_reddit_posts is fetch_reddit_posts  # restored
        assert mod.get_news is tool

    def test_dry_run_spends_nothing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setitem(sys.modules, "langchain_anthropic", None)  # any import fails
        items = [_item("a", datetime(2026, 9, 22, 9, tzinfo=UTC), stance="bullish")]
        monkeypatch.setattr(diff.cs, "_load_items", lambda *a: items)
        assert diff.main(["--points", "META:2026-09-24", "--dry-run"]) == 0
        out = capsys.readouterr().out
        assert "META @ 2026-09-24: 1 item(s)" in out and "stance stated: True" in out
