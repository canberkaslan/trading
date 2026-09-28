"""The commentator block in the sentiment analyst's prompt (ADR-009).

The contract that matters most is the first class: with COMMENTATOR_FEED off
the sentiment prompt is the vendor's, byte for byte.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from tradingagents_us.dataflows import commentator_supplement as cs  # noqa: E402
from tradingagents_us.graph import pipeline  # noqa: E402
from tradingagents_us.schemas import AgentDecision  # noqa: E402
from tradingagents_us.storage import TradeLogRepository  # noqa: E402
from tradingagents_us.storage import commentator as store  # noqa: E402
from tradingagents_us.storage.commentator import SourceRead, StoredItem  # noqa: E402
from tradingagents_us.storage.models import DecisionCommentatorRefRow  # noqa: E402

KW = {
    "ticker": "META",
    "start_date": "2026-09-15",
    "end_date": "2026-09-22",
    "news_block": "NEWS",
    "stocktwits_block": "TWITS",
    "reddit_block": "REDDIT",
}
TODAY = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def item(sid: str, when: datetime | None, *, tickers=("META",), topics=(), stance=None,
         promo=False, market=True, source="youtube", claim="A paraphrase.",
         extracted=True) -> StoredItem:
    return StoredItem(
        item_id=f"{source}:{sid}", source=source, source_id=sid,
        published_at=when,  # type: ignore[arg-type] — None exercises the refusal
        tickers=tuple(tickers), macro_topics=tuple(topics),
        stance=stance or {t: "unstated" for t in tickers}, claim_en=claim,
        is_promo=promo, is_market_content=market, extracted=extracted,
    )


def unread_item(sid: str, when: datetime, *, source="youtube") -> StoredItem:
    """As `store.items_between` returns a row whose extraction never succeeded."""
    return StoredItem(
        item_id=f"{source}:{sid}", source=source, source_id=sid, published_at=when,
        tickers=(), macro_topics=(), extracted=False,
    )


def at(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


@pytest.fixture
def analyst() -> Iterator[object]:
    from tradingagents.agents.analysts import sentiment_analyst as mod

    original = mod._build_system_message
    yield mod
    mod._build_system_message = original
    with cs._state_lock:
        cs._consumed.clear()
        cs._run_starts.clear()


@pytest.fixture
def feed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMMENTATOR_FEED", "1")


@pytest.fixture
def feed_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMMENTATOR_FEED", raising=False)


class TestFlagOffIsByteIdentical:
    def test_the_pipeline_does_not_install_it(
        self, analyst, feed_off, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = analyst._build_system_message
        monkeypatch.setattr(cs, "install", lambda: pytest.fail("installed with the flag off"))
        assert pipeline._install_commentator_feed("META", "2026-09-22") is False
        assert analyst._build_system_message is before

    @pytest.mark.parametrize("value", ["0", "true", "yes", ""])
    def test_only_exactly_1_turns_it_on(
        self, analyst, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("COMMENTATOR_FEED", value)
        before = analyst._build_system_message
        assert pipeline._install_commentator_feed("META", "2026-09-22") is False
        assert analyst._build_system_message is before

    def test_an_installed_wrapper_passes_the_prompt_through_untouched(
        self, analyst, feed_off, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A long-lived process that installed it earlier must still produce the
        # vendor's prompt exactly when the flag is off — and must not read the DB.
        baseline = analyst._build_system_message(**KW)
        monkeypatch.setattr(cs, "_load_items", lambda *a: pytest.fail("read with the flag off"))
        assert cs.install() is True
        assert analyst._build_system_message(**KW) == baseline

    def test_flag_on_installs_and_marks_the_run(self, analyst, feed_on) -> None:
        assert pipeline._install_commentator_feed("META", "2026-09-22") is True
        assert getattr(analyst._build_system_message, "_supplemented", False)
        assert cs._run_start("META", "2026-09-22") is not None


class TestInstall:
    def test_idempotent(self, analyst) -> None:
        assert cs.install() is True
        wrapped = analyst._build_system_message
        assert cs.install() is True
        assert analyst._build_system_message is wrapped

    def test_the_block_goes_before_the_marker(
        self, analyst, feed_on, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cs, "_load_items", lambda *a: [item("v1", at(20))])
        cs.install()
        out = analyst._build_system_message(**KW)
        block_at = out.index("### Commentator feed")
        assert out.index("<end_of_reddit>") < block_at < out.index(cs.MARKER)
        assert out.count(cs.MARKER) == 1
        assert "[C1, YouTube]" in out

    def test_it_is_not_in_the_reddit_or_stocktwits_blocks(
        self, analyst, feed_on, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cs, "_load_items", lambda *a: [item("v1", at(20))])
        cs.install()
        out = analyst._build_system_message(**KW)
        reddit = out[out.index("<start_of_reddit>"): out.index("<end_of_reddit>")]
        twits = out[out.index("<start_of_stocktwits>"): out.index("<end_of_stocktwits>")]
        assert "[C1, YouTube]" in out  # rendered, so its absence below says where it is not
        # Items carry labels, never ids, so look for the block itself.
        for slot in (reddit, twits):
            assert "### Commentator feed" not in slot
            assert "<start_of_commentator>" not in slot and "[C1" not in slot

    def test_without_the_marker_it_is_appended(self) -> None:
        out = cs.insert("vendor prompt with no marker\n", "### Commentator feed — x")
        assert out == "vendor prompt with no marker\n\n### Commentator feed — x\n"

    def test_the_items_used_are_recorded_for_the_decision(
        self, analyst, feed_on, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cs, "_load_items", lambda *a: [item("v1", at(20))])
        cs.install()
        analyst._build_system_message(**KW)
        assert cs.consumed("META", "2026-09-22") == [("youtube", "youtube:v1")]

    def test_a_storage_failure_says_unavailable_and_keeps_the_report(
        self, analyst, feed_on, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a):
            raise RuntimeError("db locked")

        monkeypatch.setattr(cs, "_load_items", boom)
        cs.install()
        out = analyst._build_system_message(**KW)
        assert "Commentator feed unavailable" in out
        assert "not an absence of commentary" in out
        assert "<start_of_news>" in out  # the vendor prompt is all still there


class TestNoLookAhead:
    def test_backtest_excludes_anything_from_the_trade_date_on(self) -> None:
        # in_window alone admits the whole trade date; a video posted that
        # morning would be read by a decision "made" before it existed.
        items = [
            item("before", at(21, 23, 59)),
            item("midnight", at(22, 0, 0)),
            item("morning", at(22, 9, 30)),
        ]
        own, _ = cs.select(items, "META", "2026-09-15", "2026-09-22", run_start=None, now=TODAY)
        assert [i.source_id for i in own] == ["before"]

    def test_live_admits_up_to_the_run_start_and_nothing_after(self) -> None:
        start = at(28, 22, 30)
        items = [item("at", start), item("after", at(28, 22, 31)), item("earlier", at(28, 9))]
        own, _ = cs.select(items, "META", "2026-09-21", "2026-09-28", run_start=start,
                           now=at(28, 22, 40))
        assert [i.source_id for i in own] == ["at", "earlier"]

    def test_a_run_that_crosses_midnight_keeps_the_trade_dates_items(self) -> None:
        # daily_run fixes DATE at 22:30 UTC; later tickers start after 00:00.
        # They must see what the earlier tickers saw, not a backtest of DATE.
        items = [item("today_video", at(28, 14))]
        anchor = at(28, 22, 35)
        for started in (at(28, 22, 40), at(29, 0, 5), at(29, 1, 30)):
            own, _ = cs.select(items, "META", "2026-09-21", "2026-09-28", run_start=started,
                               now=started + timedelta(minutes=3), live_anchor=anchor)
            assert [i.source_id for i in own] == ["today_video"], started

    def test_one_run_has_one_live_cutoff(self) -> None:
        anchor = at(28, 22, 35)
        items = [item("before", at(28, 22, 30)), item("after", at(28, 22, 50))]
        seen = {
            tuple(i.source_id for i in cs.select(
                items, "META", "2026-09-21", "2026-09-28", run_start=started,
                now=started, live_anchor=anchor)[0])
            for started in (at(28, 22, 40), at(28, 23, 55), at(29, 0, 20))
        }
        assert seen == {("before",)}

    def test_without_an_anchor_the_start_not_the_clock_decides(self) -> None:
        # A manual run that began at 23:50 and builds this prompt at 00:05.
        items = [item("today_video", at(28, 14))]
        own, _ = cs.select(items, "META", "2026-09-21", "2026-09-28", run_start=at(28, 23, 50),
                           now=at(29, 0, 5))
        assert [i.source_id for i in own] == ["today_video"]

    def test_a_backtest_of_yesterday_stays_strict(self) -> None:
        # llm_backtest the next morning: begin_run records 29 10:00 for 09-28.
        items = [item("today_video", at(28, 14)), item("prior", at(27, 18))]
        own, _ = cs.select(items, "META", "2026-09-21", "2026-09-28", run_start=at(29, 10),
                           now=at(29, 10, 1))
        assert [i.source_id for i in own] == ["prior"]

    @pytest.mark.parametrize(("raw", "expected"), [
        (None, None),
        ("", None),
        ("2026-09-28T22:35:00Z", datetime(2026, 9, 28, 22, 35, tzinfo=UTC)),
        ("2026-09-29T00:10:00Z", datetime(2026, 9, 29, 0, 10, tzinfo=UTC)),  # slipped past 00:00
        ("2026-09-30T22:35:00Z", None),  # another run's anchor
        ("2026-09-27T22:35:00Z", None),  # before the trade date
        ("2026-09-28T22:35:00", None),   # no offset
        ("yesterday", None),
    ])
    def test_the_live_anchor_is_read_only_for_its_own_trade_date(
        self, monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: datetime | None
    ) -> None:
        if raw is None:
            monkeypatch.delenv(cs.LIVE_AS_OF_ENV, raising=False)
        else:
            monkeypatch.setenv(cs.LIVE_AS_OF_ENV, raw)
        assert cs.live_as_of("2026-09-28") == expected

    def test_the_wrapper_uses_the_daily_runs_anchor(
        self, analyst, feed_on, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(cs.LIVE_AS_OF_ENV, "2026-09-28T22:35:00Z")
        monkeypatch.setattr(cs, "_load_items", lambda *a: [item("today_video", at(28, 14))])
        cs.install()
        cs.begin_run("META", "2026-09-28", now=at(29, 0, 5))  # this ticker began after 00:00
        out = analyst._build_system_message(**{**KW, "start_date": "2026-09-21",
                                               "end_date": "2026-09-28"})
        assert "- 2026-09-28T14:00Z [C1, YouTube]" in out  # the trade date's own item

    def test_the_window_start_still_applies(self) -> None:
        own, _ = cs.select([item("old", at(14))], "META", "2026-09-15", "2026-09-22",
                           run_start=None, now=TODAY)
        assert own == []

    def test_an_undated_item_is_refused(self) -> None:
        # Undated cannot be kept out of a backtest, so it is refused on live
        # runs too — unlike the vendor's in_window, which keeps it live.
        # in_window decides "live" by the wall clock, not by `now`, so the
        # window is built from the real date: a fixed one would stop being
        # live for in_window a day later, and the test would pass with the
        # refusal deleted.
        from tradingagents.dataflows.date_window import in_window

        started = datetime.now(UTC)
        end = started.date()
        start, end_date = (end - timedelta(days=7)).isoformat(), end.isoformat()
        assert in_window(None, cs._day(start), cs._day(end_date))  # the vendor keeps it
        own, macro = cs.select([item("nodate", None)], "META", start, end_date,
                               run_start=started, now=started + timedelta(minutes=5))
        assert own == [] and macro == []


class TestSelection:
    def test_promo_and_off_topic_items_are_dropped(self) -> None:
        items = [item("promo", at(20), promo=True), item("mindset", at(20), market=False),
                 item("ok", at(19))]
        own, _ = cs.select(items, "META", "2026-09-15", "2026-09-22", run_start=None, now=TODAY)
        assert [i.source_id for i in own] == ["ok"]

    def test_at_most_five_about_the_ticker_newest_first(self) -> None:
        items = [item(f"v{d}", at(d)) for d in range(15, 22)]
        own, _ = cs.select(items, "META", "2026-09-15", "2026-09-22", run_start=None, now=TODAY)
        assert [i.source_id for i in own] == ["v21", "v20", "v19", "v18", "v17"]

    def test_other_tickers_get_one_market_wide_line_at_most(self) -> None:
        items = [item("m1", at(21), tickers=("SPY",), topics=("Fed rates",)),
                 item("m2", at(20), tickers=(), topics=("Oil",)),
                 item("nvda", at(19), tickers=("NVDA",))]
        own, macro = cs.select(items, "META", "2026-09-15", "2026-09-22", run_start=None,
                               now=TODAY)
        assert own == [] and [i.source_id for i in macro] == ["m1"]

    def test_a_single_name_item_with_a_macro_topic_is_not_market_wide(self) -> None:
        # The extractor tags themes such as "AI capex" on any item. An NVDA-only
        # claim must not reach other tickers as the broad-market line, nor push
        # out the real market item.
        items = [
            item("nvda1", at(21), tickers=("NVDA",), topics=("AI capex",),
                 stance={"NVDA": "bullish"}, claim="NVDA will break out on China chip sales."),
            item("spy1", at(20), tickers=("SPY",), topics=("Fed rates",),
                 stance={"SPY": "bearish"}),
        ]
        own, macro = cs.select(items, "AAPL", "2026-09-15", "2026-09-22", run_start=None,
                               now=TODAY)
        assert own == [] and [i.source_id for i in macro] == ["spy1"]
        out = cs.render("AAPL", own, macro, start_date="s", end_date="e")
        assert "NVDA" not in out and "[C2" not in out  # the NVDA item is not shown
        assert "broad-market stance: bearish" in out

        spy_own, _ = cs.select(items, "SPY", "2026-09-15", "2026-09-22", run_start=None,
                               now=TODAY)
        assert [i.source_id for i in spy_own] == ["spy1"]

        nvda_own, nvda_macro = cs.select(items, "NVDA", "2026-09-15", "2026-09-22",
                                         run_start=None, now=TODAY)
        assert [i.source_id for i in nvda_own] == ["nvda1"]
        assert [i.source_id for i in nvda_macro] == ["spy1"]

    def test_spy_reads_market_wide_items_as_its_own(self) -> None:
        items = [item("m1", at(21), tickers=("SPY",), topics=("Fed rates",)),
                 item("m2", at(20), tickers=(), topics=("Oil",))]
        own, macro = cs.select(items, "SPY", "2026-09-15", "2026-09-22", run_start=None,
                               now=TODAY)
        assert [i.source_id for i in own] == ["m1", "m2"] and macro == []


def read(source: str, at_: datetime, since: datetime) -> SourceRead:
    return SourceRead(source=source, read_at=at_, covered_since=since)


class TestAbsenceNeedsARead:
    """ "No commentary" is a claim; it is made only when a read saw the window."""

    def test_empty_with_a_covering_read_says_so_and_names_it(self) -> None:
        r = read("youtube", at(28, 22, 35), at(14))
        out = cs.render("META", [], [], start_date="2026-09-21", end_date="2026-09-28",
                        observed=[r])
        assert "No commentary in window (2026-09-21 to 2026-09-28) for META" in out
        assert "read from YouTube at 2026-09-28T22:35Z" in out

    def test_empty_without_a_read_says_unavailable(self) -> None:
        out = cs.render("META", [], [], start_date="2026-09-15", end_date="2026-09-22")
        assert "No commentary" not in out
        assert "Commentator feed unavailable" in out and "not an absence of commentary" in out

    def test_flag_on_with_no_keys_claims_no_absence(self) -> None:
        # The fetch no-op'd: nothing stored, nothing read. The block must not
        # tell the analyst there was no commentary.
        block, used = cs.build_block("NVDA", "2026-09-21", "2026-09-28",
                                     run_start=at(28, 22, 40), now=at(28, 22, 41),
                                     load=lambda *a: [], load_reads=lambda: [])
        assert used == [] and "No commentary" not in block
        assert "not an absence of commentary" in block

    def test_a_fresh_live_read_proves_an_empty_window(self) -> None:
        reads = [read("youtube", at(28, 22, 35), at(14, 22, 35))]
        block, _ = cs.build_block("NVDA", "2026-09-21", "2026-09-28",
                                  live_anchor=at(28, 22, 35), now=at(28, 23),
                                  load=lambda *a: [], load_reads=lambda: reads)
        assert "No commentary in window" in block

    def test_a_failed_fetch_today_leaves_only_yesterdays_read(self) -> None:
        # Today's fetch hit the quota or the 600s timeout; the last good read
        # is yesterday's, which saw nothing of today.
        reads = [read("youtube", at(25, 22, 35), at(11, 22, 35))]
        observed = cs.covering_reads(reads, start=at(21, 0), limit=at(28, 22, 35), live=True,
                                     now=at(28, 23))
        assert observed == []

    def test_a_read_that_does_not_reach_the_window_start(self) -> None:
        reads = [read("youtube", at(28, 22, 35), at(23))]
        assert cs.covering_reads(reads, start=at(21, 0), limit=at(28, 22, 35), live=True,
                                 now=at(28, 23)) == []

    def test_a_backtest_window_the_reads_saw(self) -> None:
        reads = [read("youtube", at(28, 22, 35), at(1))]
        got = cs.covering_reads(reads, start=at(15, 0), limit=at(22, 0), live=False,
                                now=at(28, 23))
        assert got == reads

    def test_a_window_older_than_retention_is_not_proven(self) -> None:
        # Items published that early may already have been purged.
        reads = [read("youtube", datetime(2026, 10, 30, tzinfo=UTC), at(1))]
        assert cs.covering_reads(reads, start=at(15, 0), limit=at(22, 0), live=False,
                                 now=datetime(2026, 10, 30, 1, tzinfo=UTC)) == []

    def test_an_x_read_older_than_a_day_proves_nothing(self) -> None:
        # Its posts would already be hidden by the deletion-check rule.
        reads = [read("x", at(26, 22, 35), at(18))]
        assert cs.covering_reads(reads, start=at(21, 0), limit=at(22, 0), live=False,
                                 now=at(28, 23)) == []
        assert cs.covering_reads(reads, start=at(21, 0), limit=at(22, 0), live=False,
                                 now=at(27, 12)) == reads

    def test_the_wrapper_says_unavailable_when_nothing_was_read(
        self, analyst, feed_on, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cs, "_load_items", lambda *a: [])
        monkeypatch.setattr(cs, "_load_reads", lambda: [])
        cs.install()
        out = analyst._build_system_message(**KW)
        assert "No commentary" not in out and "not an absence of commentary" in out


class TestAnUnreadItemIsNotAnAbsence:
    """A fetch that landed but whose extraction failed saw an item, not silence."""

    READS = [read("youtube", at(28, 22, 35), at(14, 22, 35))]

    def _block(self, items: list[StoredItem], ticker: str = "NVDA") -> tuple[str, list]:
        return cs.build_block(ticker, "2026-09-21", "2026-09-28",
                              live_anchor=at(28, 22, 35), now=at(28, 22, 45),
                              load=lambda *a: items, load_reads=lambda: self.READS)

    def test_with_nothing_else_in_the_window_the_feed_is_unavailable(self) -> None:
        block, used = self._block([unread_item("nvda1", at(28, 14))])
        assert used == [] and "No commentary" not in block
        assert "1 item(s) published between 2026-09-21 and 2026-09-28 could not be read" in block
        assert "not an absence of commentary" in block

    def test_beside_shown_items_the_list_says_it_may_be_incomplete(self) -> None:
        shown = item("nvda0", at(27), tickers=("NVDA",))
        block, used = self._block([unread_item("nvda1", at(28, 14)), shown])
        assert used == [shown]
        assert "(Not shown: 1 item(s) from this window that could not be read yet" in block

    def test_it_is_never_shown_whatever_its_fields_say(self) -> None:
        half = item("nvda1", at(28, 14), tickers=("NVDA",), extracted=False)
        own, macro = cs.select([half], "NVDA", "2026-09-21", "2026-09-28",
                               run_start=None, now=at(28, 22, 45), live_anchor=at(28, 22, 35))
        assert own == [] and macro == []

    @pytest.mark.parametrize("when", [at(20, 23), at(28, 23)], ids=["before", "after-cutoff"])
    def test_one_outside_the_window_leaves_the_absence_claim_alone(
        self, when: datetime
    ) -> None:
        block, _ = self._block([unread_item("elsewhere", when)])
        assert "No commentary in window (2026-09-21 to 2026-09-28) for NVDA" in block

    def test_end_to_end_a_failed_extraction_then_a_retry(self) -> None:
        # The fetch lands and records its read; the extractor is down all night.
        from tests.commentator_fakes import FakeYouTubeAPI, video
        from tradingagents_us.dataflows.commentator import ingest

        repo = TradeLogRepository(engine=create_engine("sqlite://", future=True))
        now = at(28, 22, 35)
        api = FakeYouTubeAPI([video("nvda1", "2026-09-28T14:00:00Z", "NVDA hedefim 250")])

        def block_at(when: datetime) -> str:
            def load(start: datetime, end: datetime) -> list[StoredItem]:
                with repo.session() as s:
                    return store.items_between(s, start, end, now=when)

            def reads() -> list[SourceRead]:
                with repo.session() as s:
                    return store.source_reads(s)

            block, _ = cs.build_block("NVDA", "2026-09-21", "2026-09-28", live_anchor=when,
                                      now=when, load=load, load_reads=reads)
            return block

        report = ingest.run(repo.session, youtube=api.client(), x=None, x_user_id=None,
                            extractor=lambda text: None, model="haiku", now=now)
        assert report.extraction_failed == 1
        with repo.session() as s:
            assert store.source_reads(s)  # the read was recorded: the fetch did land
        down = block_at(now)
        assert "No commentary" not in down and "not an absence of commentary" in down

        from tradingagents_us.storage.commentator import Extraction

        later = now + timedelta(minutes=30)
        ingest.run(repo.session, youtube=api.client(), x=None, x_user_id=None,
                   extractor=lambda text: Extraction(
                       tickers=("NVDA",), macro_topics=(), stance={"NVDA": "bullish"},
                       claim_en="Targets 250 for NVDA.", is_promo=False,
                       is_market_content=True),
                   model="haiku", now=later)
        up = block_at(later)
        assert "stance on NVDA: bullish" in up and "could not be read" not in up


class TestRender:

    def test_it_is_labelled_as_one_commentators_opinion(self) -> None:
        out = cs.render("META", [item("v1", at(20))], [], start_date="s", end_date="e")
        assert out.startswith("### Commentator feed — Bora Özkent (")
        assert "not investment advice" in out and "opinion" in out
        assert "not consensus" in out
        assert "must not move overall_score" in out
        assert "Commentator view:" in out
        assert "skews bullish" in out

    def test_an_item_line_carries_a_label_time_and_stance_only(self) -> None:
        i = item("iIVDlDLd9yk", at(22, 9, 30), tickers=("META", "NVDA"),
                 topics=("Nasdaq rally breadth",), stance={"META": "unstated", "NVDA": "bullish"})
        out = cs.render("META", [i], [], start_date="s", end_date="e")
        assert ("- 2026-09-22T09:30Z [C1, YouTube] also on: NVDA; "
                "topics: Nasdaq rally breadth; stance on META: unstated") in out

    def test_a_market_wide_line_reports_the_broad_market_stance(self) -> None:
        m = item("m1", at(21), tickers=("SPY",), topics=("Fed rates",), stance={"SPY": "bearish"},
                 source="x")
        out = cs.render("META", [], [m], start_date="s", end_date="e")
        assert "[C1, X] market-wide; topics: Fed rates; broad-market stance: bearish" in out

    def test_no_platform_id_reaches_the_prompt(self) -> None:
        # The report the analyst writes from this block is stored whole, shown
        # in the app and backed up for good; an id here would outlive a deleted
        # post there. Only the refs table, which the purge scrubs, holds ids.
        own = [item("iIVDlDLd9yk", at(22)), item("1790000000000000001", at(21), source="x")]
        macro = [item("M7qtACFd9g4", at(20), tickers=("SPY",), stance={"SPY": "bullish"})]
        out = cs.render("META", own, macro, start_date="s", end_date="e")
        for sid in ("iIVDlDLd9yk", "1790000000000000001", "M7qtACFd9g4"):
            assert sid not in out
        assert [ln.split("] ")[0].split(" [")[1] for ln in out.splitlines()
                if ln.startswith("- 2026-")] == ["C1, YouTube", "C2, X", "C3, YouTube"]

    def test_the_guidance_asks_for_labels(self) -> None:
        out = cs.render("META", [item("v1", at(20))], [], start_date="s", end_date="e")
        assert "`Commentator view:` and citing items by label ([C1], ...)" in out


class TestDecisionLink:
    def test_bind_then_save_writes_the_refs(self, analyst, feed_on,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
        repo = TradeLogRepository(engine=create_engine("sqlite://", future=True))
        monkeypatch.setattr(cs, "_load_items", lambda *a: [item("v1", at(20))])
        cs.install()
        cs.begin_run("META", "2026-09-22")
        analyst._build_system_message(**KW)
        cs.bind_decision("META", "2026-09-22", "dec-link")
        repo.save_decision(AgentDecision(
            ticker="META", market="US", quote_currency="USD", rating="Hold",
            reasoning=[], timestamp_utc=TODAY, decision_id="dec-link",
        ))
        with repo.session() as s:
            refs = s.scalars(select(DecisionCommentatorRefRow.item_id)).all()
        assert refs == ["youtube:v1"]
        assert cs.consumed("META", "2026-09-22") == []
        assert store.pop_decision_refs("dec-link") == []

    def test_the_nth_ref_is_the_item_labelled_cn(self, analyst, feed_on,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
        # The report cites [C2]; the refs table is where that resolves.
        repo = TradeLogRepository(engine=create_engine("sqlite://", future=True))
        items = [item("v1", at(21)), item("v2", at(20)), item("x9", at(19), source="x"),
                 item("m1", at(18), tickers=(), topics=("Fed rates",))]
        monkeypatch.setattr(cs, "_load_items", lambda *a: items)
        cs.install()
        prompt = analyst._build_system_message(**KW)
        labels = [ln.split("] ")[0].split(" [")[1].split(",")[0]
                  for ln in prompt.splitlines() if ln.startswith("- 2026-")]
        cs.bind_decision("META", "2026-09-22", "dec-order")
        repo.save_decision(AgentDecision(
            ticker="META", market="US", quote_currency="USD", rating="Hold",
            reasoning=[], timestamp_utc=TODAY, decision_id="dec-order",
        ))
        with repo.session() as s:
            refs = s.scalars(select(DecisionCommentatorRefRow.item_id)
                             .order_by(DecisionCommentatorRefRow.id)).all()
        assert labels == ["C1", "C2", "C3", "C4"]
        assert refs == ["youtube:v1", "youtube:v2", "x:x9", "youtube:m1"]


class TestTheRealNode:
    """The whole sentiment node, fetchers and model faked, prompt captured."""

    class _LLM:
        def __init__(self) -> None:
            self.prompts: list[list] = []

        def with_structured_output(self, schema):
            raise NotImplementedError  # take the plain path; this fake has no tools

        def invoke(self, messages):
            import types

            self.prompts.append(messages)
            header = "**Overall Sentiment:** **Neutral** (Score: 5.0/10)"
            return types.SimpleNamespace(content=header)

    def _system(self, analyst, monkeypatch: pytest.MonkeyPatch) -> str:
        import types

        monkeypatch.setattr(analyst, "get_news", types.SimpleNamespace(func=lambda *a: "NEWS"))
        monkeypatch.setattr(analyst, "finnhub_block", lambda t: "FINNHUB")
        monkeypatch.setattr(analyst, "fetch_stocktwits_messages", lambda *a, **k: "TWITS")
        monkeypatch.setattr(analyst, "fetch_reddit_posts", lambda *a, **k: "REDDIT")
        llm = self._LLM()
        node = analyst.create_sentiment_analyst(llm)
        node({"messages": [("human", "META")], "company_of_interest": "META",
              "trade_date": "2026-09-22", "instrument_context": "META (stock)"})
        (messages,) = llm.prompts
        return messages[0].content

    def test_flag_off_the_node_sends_mains_system_message(
        self, analyst, feed_off, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        baseline = self._system(analyst, monkeypatch)
        assert pipeline._install_commentator_feed("META", "2026-09-22") is False
        cs.install()  # even installed, the flag decides
        assert self._system(analyst, monkeypatch) == baseline
        assert "Commentator feed" not in baseline

    def test_flag_on_the_node_sends_the_block(
        self, analyst, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("COMMENTATOR_FEED", raising=False)
        baseline = self._system(analyst, monkeypatch)
        monkeypatch.setenv("COMMENTATOR_FEED", "1")
        monkeypatch.setattr(cs, "_load_items", lambda *a: [item("v1", at(20))])
        assert pipeline._install_commentator_feed("META", "2026-09-22") is True
        with_feed = self._system(analyst, monkeypatch)
        assert "### Commentator feed" in with_feed and "[C1, YouTube]" in with_feed
        # Everything else is the vendor's prompt, untouched.
        start = with_feed.index("### Commentator feed")
        end = with_feed.index(cs.MARKER)
        assert with_feed[:start] + with_feed[end:] == baseline
