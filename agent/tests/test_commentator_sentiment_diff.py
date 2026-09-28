"""ADR-009 gate step A: the sentiment-node-only diff script. No model calls."""

from __future__ import annotations

import itertools
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from scripts import commentator_sentiment_diff as diff  # noqa: E402
from tradingagents_us.storage.commentator import StoredItem  # noqa: E402


def _item(sid: str, when: datetime, stance: str = "unstated", *,
          tickers: tuple[str, ...] = ("META",), stances: dict[str, str] | None = None,
          topics: tuple[str, ...] = ()) -> StoredItem:
    return StoredItem(
        item_id=f"youtube:{sid}", source="youtube", source_id=sid, published_at=when,
        tickers=tickers, macro_topics=topics,
        stance=stances if stances is not None else {t: stance for t in tickers},
        claim_en="p", is_promo=False, is_market_content=True,
    )


NOW = datetime(2026, 9, 28, tzinfo=UTC)
#: A stance the prompt shows as stated, in either line shape.
_SHOWN_STATED = re.compile(r"(?:stance on [A-Z]+|broad-market stance): (?:bullish|bearish|neutral)")


def _lines(ticker: str, items: list[StoredItem]) -> tuple[str, list[tuple[StoredItem, bool]]]:
    return diff.cs.build_block_lines(ticker, "2026-09-15", "2026-09-22", now=NOW,
                                     load=lambda *a: items, load_reads=lambda: [])


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

    def test_a_ticker_line_is_stated_only_by_the_ticker_stance(self) -> None:
        # The extractor adds SPY to any item that covers the broad market, so
        # META-and-SPY items are common. META's line shows only its META stance.
        both = _item("a", datetime(2026, 9, 21, 9, tzinfo=UTC), tickers=("META", "SPY"),
                     stances={"META": "unstated", "SPY": "bullish"}, topics=("Fed rates",))
        block, lines = _lines("META", [both])
        assert "stance on META: unstated" in block and not _SHOWN_STATED.search(block)
        assert diff.stance_stated(lines, "META") is False
        # SPY's own block shows that same item with its SPY stance.
        block, lines = _lines("SPY", [both])
        assert "stance on SPY: bullish" in block
        assert diff.stance_stated(lines, "SPY") is True

    def test_a_market_wide_line_is_stated_by_the_broad_market_stance(self) -> None:
        # Six META items, the oldest also on SPY: five fill META's own lines and
        # the sixth is shown as its one market-wide line, under the SPY stance.
        day = datetime(2026, 9, 21, 9, tzinfo=UTC)
        own = [_item(f"m{n}", day - timedelta(hours=n)) for n in range(5)]
        wide = _item("w", day - timedelta(hours=6), tickers=("META", "SPY"),
                     stances={"META": "unstated", "SPY": "bearish"})
        block, lines = _lines("META", [*own, wide])
        assert [w for _, w in lines] == [False] * 5 + [True]
        assert "broad-market stance: bearish" in block
        assert diff.stance_stated(lines, "META") is True

    def test_stated_means_a_stated_stance_is_in_the_prompt(self) -> None:
        # Whatever the combination, the diff's label agrees with the text sent.
        day = datetime(2026, 9, 21, 9, tzinfo=UTC)
        shapes = [(("META",),), (("META", "SPY"),), (("SPY",),), ((),), (("NVDA", "SPY"),)]
        for (tickers,), meta, spy, ticker in itertools.product(
            shapes, ("unstated", "bullish"), ("unstated", "bearish"), ("META", "SPY"),
        ):
            stances = {t: {"META": meta, "SPY": spy}.get(t, "neutral") for t in tickers}
            it = _item("x", day, tickers=tickers, stances=stances, topics=("Fed rates",))
            block, lines = _lines(ticker, [it])
            assert diff.stance_stated(lines, ticker) == bool(_SHOWN_STATED.search(block)), (
                tickers, stances, ticker, block)

    def test_a_moving_point_whose_lines_are_unstated_is_a_prompt_bug(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # End to end through run_point: the SPY stance the META line does not
        # show must not file this point under stance_stated.
        both = _item("a", datetime(2026, 9, 21, 9, tzinfo=UTC), tickers=("META", "SPY"),
                     stances={"META": "unstated", "SPY": "bullish"})
        monkeypatch.setattr(diff.cs, "_load_items", lambda *a: [both])

        def node(state: dict) -> dict:
            score = "6.5" if diff.os.environ.get("COMMENTATOR_FEED") == "1" else "5.0"
            return {"sentiment_report": f"**Overall Sentiment:** **Neutral** (Score: {score}/10)"}

        point = diff.run_point(node, "META", "2026-09-22", noise=False)
        assert point.items == 1 and point.stance_stated is False and point.delta == 1.5
        assert "PROMPT BUG" in diff.summarize([point])["verdict"]

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
