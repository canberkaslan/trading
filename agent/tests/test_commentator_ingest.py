"""Fetch -> extract once -> store -> purge, end to end on fakes (ADR-009)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

from tests.commentator_fakes import X_USER, FakeXAPI, FakeYouTubeAPI, post, video
from tradingagents_us.dataflows.commentator import config, ingest
from tradingagents_us.schemas import AgentDecision
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage import commentator as store
from tradingagents_us.storage.commentator import Extraction
from tradingagents_us.storage.models import CommentatorItemRow, DecisionCommentatorRefRow

NOW = datetime(2026, 9, 28, 22, 30, tzinfo=UTC)


def _videos() -> list[dict]:
    return [
        video(f"v{i}", (NOW - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              f"Video {i}", [f"İçerik {i}"])
        for i in range(1, 7)
    ]


class Extractor:
    """Counts calls; can be told to fail for some texts."""

    def __init__(self, fail_on: tuple[str, ...] = ()) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on

    def __call__(self, text: str) -> Extraction | None:
        self.calls.append(text)
        if any(f in text for f in self.fail_on):
            return None
        return Extraction(
            tickers=("META",), macro_topics=("Fed rates",), stance={"META": "unstated"},
            claim_en="A paraphrase.", is_promo=False, is_market_content=True,
        )


@pytest.fixture
def repo() -> TradeLogRepository:
    return TradeLogRepository(engine=create_engine("sqlite://", future=True))


def _run(repo, *, yt=None, x=None, x_user=X_USER, extractor=None, now=NOW,
         retention_pass=True, **kw):
    if retention_pass:  # the daily timer ran; without it X stores nothing new
        with repo.session() as s:
            store.record_status(s, store.RETENTION_PASS, now - timedelta(hours=1))
    return ingest.run(
        repo.session, youtube=yt, x=x, x_user_id=x_user,
        extractor=extractor or Extractor(), model="haiku-test", now=now, **kw,
    )


def _items(repo: TradeLogRepository) -> dict[str, CommentatorItemRow]:
    with repo.session() as s:
        return {r.item_id: r for r in s.scalars(select(CommentatorItemRow))}


class TestExtractOncePerItem:
    def test_a_second_run_extracts_nothing_already_extracted(
        self, repo: TradeLogRepository
    ) -> None:
        api, ext = FakeYouTubeAPI(_videos()), Extractor()
        first = _run(repo, yt=api.client(), extractor=ext)
        assert first.extracted == 6 and len(ext.calls) == 6
        second = _run(repo, yt=api.client(), extractor=ext, now=NOW + timedelta(hours=1))
        assert second.extracted == 0 and second.cached == 6
        assert len(ext.calls) == 6  # not one more paid call

    def test_raw_text_is_never_stored(self, repo: TradeLogRepository) -> None:
        _run(repo, yt=FakeYouTubeAPI(_videos()).client())
        for row in _items(repo).values():
            values = [str(getattr(row, c.name)) for c in CommentatorItemRow.__table__.columns]
            assert not any("İçerik" in v or "Video " in v for v in values)

    def test_a_failed_extraction_is_stored_empty_and_retried(
        self, repo: TradeLogRepository
    ) -> None:
        api = FakeYouTubeAPI(_videos())
        report = _run(repo, yt=api.client(), extractor=Extractor(fail_on=("İçerik 2",)))
        assert report.extraction_failed == 1
        assert _items(repo)["youtube:v2"].extracted_at_utc is None
        with repo.session() as s:  # an unextracted item is invisible to the analyst
            visible = store.extracted_items_between(s, NOW - timedelta(days=10), NOW, now=NOW)
        assert "v2" not in {i.source_id for i in visible}

        ext = Extractor()
        retry = _run(repo, yt=api.client(), extractor=ext, now=NOW + timedelta(hours=1))
        assert retry.extracted == 1 and retry.new_items == 0
        assert len(ext.calls) == 1 and "İçerik 2" in ext.calls[0]

    def test_an_edited_description_keeps_the_first_extraction(
        self, repo: TradeLogRepository
    ) -> None:
        vids = _videos()
        api = FakeYouTubeAPI(vids)
        _run(repo, yt=api.client())
        vids[0]["snippet"]["title"] = "Edited later"
        ext = Extractor()
        _run(repo, yt=api.client(), extractor=ext, now=NOW + timedelta(hours=1))
        assert ext.calls == []  # a later edit is not re-dated to the original publish time


class TestRetention:
    def test_youtube_items_expire_a_pass_interval_inside_thirty_days(
        self, repo: TradeLogRepository
    ) -> None:
        # The daily pass may run up to a day after the deadline; the deadline
        # is set a day early so the item is gone by day thirty either way.
        _run(repo, yt=FakeYouTubeAPI(_videos()).client())
        row = _items(repo)["youtube:v1"]
        assert store.aware(row.expires_at_utc) == NOW + timedelta(days=29)
        assert (
            store.aware(row.expires_at_utc) + config.RETENTION_PASS_INTERVAL
            <= NOW + config.YOUTUBE_MAX_RETENTION
        )
        report = _run(repo, now=NOW + timedelta(days=29, seconds=1), x=None)
        assert report.purged_expired == 6
        assert _items(repo) == {}

    def test_the_retention_pass_alone_purges_and_calls_no_source(
        self, repo: TradeLogRepository
    ) -> None:
        # What the daily timer runs, flag on or off: no YouTube client, no
        # extractor, and the expired rows still go.
        _run(repo, yt=FakeYouTubeAPI(_videos()).client())
        report = ingest.enforce_retention(
            repo.session, x=None, now=NOW + timedelta(days=29, seconds=1)
        )
        assert report.purged_expired == 6 and _items(repo) == {}
        assert "commentator retention: purged_expired=6" in report.retention_summary()
        with repo.session() as s:
            assert store.status_at(s, store.RETENTION_PASS) == NOW + timedelta(
                days=29, seconds=1
            )

    def test_the_retention_pass_checks_x_deletions(self, repo: TradeLogRepository) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a"),
                        post("1002", "2026-09-27T12:00:00Z", "b")])
        _run(repo, x=api.client())
        api.delete("1001")
        api.requests.clear()
        report = ingest.enforce_retention(
            repo.session, x=api.client(), now=NOW + timedelta(days=5)  # Saturday: no trading run
        )
        assert report.purged_deleted == 1 and set(_items(repo)) == {"x:1002"}
        # Deletion check only: nothing bought from the user timeline.
        assert all("/users/" not in r.url.path for r in api.requests)

    def test_the_retention_pass_with_nothing_stored_calls_nothing(
        self, repo: TradeLogRepository
    ) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")])
        ingest.enforce_retention(repo.session, x=api.client(), now=NOW)
        assert api.requests == []

    def test_an_expired_video_is_not_reingested_the_next_day(
        self, repo: TradeLogRepository
    ) -> None:
        # The routine lookback is shorter than retention, so what retention
        # deleted is already outside what the next fetch ingests.
        _run(repo, yt=FakeYouTubeAPI(_videos()).client())
        later = NOW + timedelta(days=31)
        ext = Extractor()
        report = _run(repo, yt=FakeYouTubeAPI(_videos()).client(), extractor=ext, now=later)
        assert report.new_items == 0 and ext.calls == []


class TestReadsRecorded:
    """What each successful read proves it saw — the basis for any "no commentary"."""

    def _reads(self, repo: TradeLogRepository) -> dict[str, store.SourceRead]:
        with repo.session() as s:
            return {r.source: r for r in store.source_reads(s)}

    def test_a_youtube_read_covers_its_lookback(self, repo: TradeLogRepository) -> None:
        _run(repo, yt=FakeYouTubeAPI(_videos()).client())
        r = self._reads(repo)["youtube"]
        assert r.read_at == NOW and r.covered_since == NOW - config.ingest_lookback()

    def test_a_youtube_read_cut_short_by_the_page_cap_covers_what_it_saw(
        self, repo: TradeLogRepository
    ) -> None:
        vids = [video(f"v{i}", (NOW - timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      f"Video {i}") for i in range(1, 7)]
        _run(repo, yt=FakeYouTubeAPI(vids, page_size=2).client(), youtube_max_pages=1)
        assert self._reads(repo)["youtube"].covered_since == NOW - timedelta(days=2)

    def test_a_failed_or_missing_source_records_no_read(self, repo: TradeLogRepository) -> None:
        yt = FakeYouTubeAPI(_videos())
        yt.fail_with = 403
        _run(repo, yt=yt.client())
        _run(repo, yt=None)
        assert self._reads(repo) == {}

    def test_x_reads_chain_through_since_id(self, repo: TradeLogRepository) -> None:
        api = FakeXAPI([post("1001", "2026-09-24T12:00:00Z", "a"),
                        post("1002", "2026-09-26T12:00:00Z", "b")])
        _run(repo, x=api.client())
        first = self._reads(repo)["x"]
        assert first.covered_since == datetime(2026, 9, 24, 12, tzinfo=UTC)
        api.posts["1003"] = post("1003", "2026-09-28T23:00:00Z", "c")
        _run(repo, x=api.client(), now=NOW + timedelta(hours=1))
        second = self._reads(repo)["x"]
        assert second.read_at == NOW + timedelta(hours=1)
        assert second.covered_since == first.covered_since

    def test_a_failed_deletion_check_forgets_the_x_read(self, repo: TradeLogRepository) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")])
        _run(repo, x=api.client())
        api.fail_lookup_with = 402
        ingest.enforce_retention(repo.session, x=api.client(), now=NOW + timedelta(hours=12))
        assert "x" not in self._reads(repo)


class TestMissingCredentials:
    def test_no_youtube_key_is_a_logged_skip(
        self, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Another test loads agent/.env into os.environ; a real key there must
        # not decide what "missing" means here.
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
        report = _run(repo, yt=None, x=None)
        assert any("YOUTUBE_API_KEY" in n for n in report.notes)
        assert any("X_BEARER_TOKEN" in n for n in report.notes)
        assert report.extracted == 0

    def test_a_youtube_failure_does_not_stop_x(self, repo: TradeLogRepository) -> None:
        yt = FakeYouTubeAPI(_videos())
        yt.fail_with = 403
        x = FakeXAPI([post("1001", "2026-09-27T12:00:00Z", "Nasdaq güçlü")])
        report = _run(repo, yt=yt.client(), x=x.client())
        assert any("YouTube failed" in n for n in report.notes)
        assert "x:1001" in _items(repo)


class TestX:
    def test_since_id_is_the_newest_stored_post(self, repo: TradeLogRepository) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")])
        _run(repo, x=api.client())
        api.posts["1002"] = post("1002", "2026-09-27T12:00:00Z", "b")
        api.requests.clear()
        _run(repo, x=api.client(), now=NOW + timedelta(hours=1))
        fetches = [r for r in api.requests if r.url.path.endswith("/tweets")
                   and "users" in r.url.path]
        assert fetches[0].url.params["since_id"] == "1001"
        assert set(_items(repo)) == {"x:1001", "x:1002"}

    def test_a_deleted_post_is_purged_on_the_next_run(self, repo: TradeLogRepository) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a"),
                        post("1002", "2026-09-27T12:00:00Z", "b")])
        _run(repo, x=api.client())
        store.stash_decision_refs("dec-x", [("x", "x:1001")])
        repo.save_decision(AgentDecision(
            ticker="META", market="US", quote_currency="USD", rating="Hold",
            reasoning=[], timestamp_utc=NOW, decision_id="dec-x",
        ))
        api.delete("1001")
        report = _run(repo, x=api.client(), now=NOW + timedelta(hours=1))
        assert report.purged_deleted == 1
        assert set(_items(repo)) == {"x:1002"}
        with repo.session() as s:
            (ref,) = s.scalars(select(DecisionCommentatorRefRow)).all()
            assert ref.item_id is None

    def test_without_a_token_stored_posts_are_purged(self, repo: TradeLogRepository) -> None:
        # Deletions cannot be seen without the API, so nothing from X may stay.
        _run(repo, x=FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")]).client())
        report = _run(repo, x=None, now=NOW + timedelta(hours=1))
        assert report.purged_deleted == 1 and _items(repo) == {}

    def test_a_failed_deletion_check_purges_every_stored_post(
        self, repo: TradeLogRepository
    ) -> None:
        # A revoked token, the spend cap or an outage: the check that would see
        # a deletion cannot run, so what it would have protected must go —
        # the same rule as no token at all. Nothing stays readable for days.
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a"),
                        post("1002", "2026-09-27T12:00:00Z", "b")])
        _run(repo, x=api.client())
        api.delete("1001")
        api.fail_lookup_with = 402
        first = _run(repo, x=api.client(), x_user=None, now=NOW + timedelta(hours=1))
        assert any("X reconcile failed: HTTPStatusError" in n for n in first.notes)
        assert first.purged_deleted == 2
        for day in range(1, 8):
            at = NOW + timedelta(days=day)
            _run(repo, x=api.client(), x_user=None, now=at)
            with repo.session() as s:
                visible = store.extracted_items_between(s, NOW - timedelta(days=7), at, now=at)
            assert _items(repo) == {} and visible == []

    def test_a_failed_deletion_check_in_the_daily_pass_purges_too(
        self, repo: TradeLogRepository
    ) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")])
        _run(repo, x=api.client())
        api.fail_lookup_with = 429
        at = NOW + timedelta(hours=12)
        report = ingest.enforce_retention(repo.session, x=api.client(), now=at)
        assert report.purged_deleted == 1 and _items(repo) == {}

    def test_a_post_unchecked_for_a_day_is_not_read_even_before_a_pass(
        self, repo: TradeLogRepository
    ) -> None:
        # If no pass runs at all (timer down), the read path still refuses a
        # post whose deletion has not been checked in the last day.
        _run(repo, x=FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")]).client())

        def visible(at: datetime) -> list[str]:
            with repo.session() as s:
                return [i.item_id for i in store.extracted_items_between(
                    s, NOW - timedelta(days=7), at, now=at)]

        assert visible(NOW + timedelta(hours=23)) == ["x:1001"]
        assert visible(NOW + timedelta(hours=25)) == []

    def test_a_deletion_check_restamps_the_posts_it_still_sees(
        self, repo: TradeLogRepository
    ) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")])
        _run(repo, x=api.client())
        later = NOW + timedelta(hours=20)
        ingest.enforce_retention(repo.session, x=api.client(), now=later)
        assert store.aware(_items(repo)["x:1001"].verified_at_utc) == later

    def test_without_a_pinned_id_nothing_is_fetched_but_deletions_are_still_checked(
        self, repo: TradeLogRepository
    ) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")])
        _run(repo, x=api.client())
        api.delete("1001")
        api.requests.clear()
        report = _run(repo, x=api.client(), x_user=None, now=NOW + timedelta(hours=1))
        assert any("no numeric user id" in n for n in report.notes)
        assert all("/users/" not in r.url.path for r in api.requests)
        assert report.purged_deleted == 1

    def test_a_failed_post_extraction_is_retried_from_the_deletion_check(
        self, repo: TradeLogRepository
    ) -> None:
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "zor metin")])
        _run(repo, x=api.client(), extractor=Extractor(fail_on=("zor",)))
        assert _items(repo)["x:1001"].extracted_at_utc is None
        ext = Extractor()
        report = _run(repo, x=api.client(), extractor=ext, now=NOW + timedelta(hours=1))
        assert report.extracted == 1 and ext.calls == ["zor metin"]

    def test_no_new_post_is_stored_without_a_recent_daily_retention_pass(
        self, repo: TradeLogRepository
    ) -> None:
        # The trading run checks deletions Mon-Fri only; the daily pass covers
        # the rest. Until it has run, X buys nothing.
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")])
        report = _run(repo, x=api.client(), retention_pass=False)
        assert any("retention pass has not run" in n for n in report.notes)
        assert _items(repo) == {}
        assert all("/users/" not in r.url.path for r in api.requests)

        with repo.session() as s:  # a pass 27h ago is too old
            store.record_status(s, store.RETENTION_PASS, NOW - timedelta(hours=27))
        report = _run(repo, x=api.client(), retention_pass=False)
        assert _items(repo) == {}

        ingest.enforce_retention(repo.session, x=api.client(), now=NOW - timedelta(hours=2))
        _run(repo, x=api.client(), retention_pass=False)
        assert set(_items(repo)) == {"x:1001"}

    def test_x_items_expire_on_the_short_window(
        self, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("COMMENTATOR_X_RETENTION_DAYS", raising=False)
        _run(repo, x=FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")]).client())
        row = _items(repo)["x:1001"]
        assert store.aware(row.expires_at_utc) == NOW + timedelta(days=8)


class TestNoTransactionAcrossPaidCalls:
    """The SQLite write lock is never held while the extractor (a paid LLM call) runs."""

    def _file_repo(self, tmp_path) -> tuple[TradeLogRepository, str]:
        path = tmp_path / "commentator.db"
        return TradeLogRepository(engine=create_engine(f"sqlite:///{path}", future=True)), str(path)

    def test_another_writer_is_never_locked_out_during_an_extraction(self, tmp_path) -> None:
        import sqlite3

        repo, path = self._file_repo(tmp_path)
        locked: list[str] = []

        class LockProbe(Extractor):
            def __call__(self, text: str) -> Extraction | None:
                # timeout=0: fail at once if any transaction holds the write lock.
                con = sqlite3.connect(path, timeout=0)
                try:
                    con.execute("BEGIN IMMEDIATE")
                    con.rollback()
                except sqlite3.OperationalError as exc:
                    locked.append(str(exc))
                finally:
                    con.close()
                return super().__call__(text)

        ext = LockProbe()
        x_api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a"),
                          post("1002", "2026-09-27T12:00:00Z", "b")])
        report = _run(repo, yt=FakeYouTubeAPI(_videos()).client(), x=x_api.client(),
                      extractor=ext)
        assert len(ext.calls) == 8 and report.extracted == 8
        assert locked == []

    def test_no_session_is_open_while_the_extractor_or_a_source_runs(self) -> None:
        from contextlib import contextmanager

        repo = TradeLogRepository(engine=create_engine("sqlite://", future=True))
        open_sessions = [0]

        @contextmanager
        def sessions():
            open_sessions[0] += 1
            try:
                with repo.session() as s:
                    yield s
            finally:
                open_sessions[0] -= 1

        seen: list[int] = []

        class Probe(Extractor):
            def __call__(self, text: str) -> Extraction | None:
                seen.append(open_sessions[0])
                return super().__call__(text)

        x_api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "zor"),
                          post("1002", "2026-09-27T12:00:00Z", "b")])
        with repo.session() as s:
            store.record_status(s, store.RETENTION_PASS, NOW - timedelta(hours=1))
        # First run: X post 1001 fails; second run retries it from the deletion check.
        ingest.run(sessions, youtube=FakeYouTubeAPI(_videos()).client(), x=x_api.client(),
                   x_user_id=X_USER, extractor=Probe(fail_on=("zor",)), model="m", now=NOW)
        ingest.run(sessions, youtube=None, x=x_api.client(), x_user_id=X_USER,
                   extractor=Probe(), model="m", now=NOW + timedelta(hours=1))
        assert seen and set(seen) == {0}

    def test_the_writes_still_land_together_with_the_read(self, tmp_path) -> None:
        repo, _ = self._file_repo(tmp_path)
        _run(repo, yt=FakeYouTubeAPI(_videos()).client())
        assert len(_items(repo)) == 6
        with repo.session() as s:
            assert {r.source for r in store.source_reads(s)} == {"youtube"}

    def test_an_item_extracted_meanwhile_keeps_that_extraction(self) -> None:
        repo = TradeLogRepository(engine=create_engine("sqlite://", future=True))
        vids = _videos()[:1]

        class Racing(Extractor):
            """Another pass stores and extracts the same video while this call runs."""

            def __call__(self, text: str) -> Extraction | None:
                with repo.session() as s:
                    store.upsert_item(
                        s, source="youtube", source_id="v1", channel_id="c", url="u",
                        published_at=NOW - timedelta(days=1), content_sha256="h", now=NOW,
                        expires_at=NOW + timedelta(days=29),
                        extraction=Extraction(("AAPL",), (), {"AAPL": "bullish"}, "Theirs.",
                                              False, True),
                        extraction_model="other",
                    )
                return super().__call__(text)

        report = _run(repo, yt=FakeYouTubeAPI(vids).client(), extractor=Racing())
        row = _items(repo)["youtube:v1"]
        assert row.claim_en == "Theirs." and row.extraction_model == "other"
        assert report.cached == 1 and report.extracted == 0

    def test_a_retry_never_reinserts_a_post_purged_while_the_model_ran(self) -> None:
        repo = TradeLogRepository(engine=create_engine("sqlite://", future=True))
        api = FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "zor metin")])
        _run(repo, x=api.client(), extractor=Extractor(fail_on=("zor",)))
        assert "x:1001" in _items(repo)

        class PurgedMeanwhile(Extractor):
            def __call__(self, text: str) -> Extraction | None:
                with repo.session() as s:
                    store.purge(s, ["x:1001"])  # the retention timer, concurrently
                return super().__call__(text)

        ext = PurgedMeanwhile()
        report = _run(repo, x=api.client(), x_user=None, extractor=ext,
                      now=NOW + timedelta(hours=1))
        assert ext.calls == ["zor metin"]
        assert "x:1001" not in _items(repo) and report.extracted == 0


class TestExtractorFailureHandling:
    def test_repeated_failures_stop_calling_for_the_rest_of_the_step(
        self, repo: TradeLogRepository
    ) -> None:
        ext = Extractor(fail_on=("",))  # every call fails, as in an outage
        report = _run(repo, yt=FakeYouTubeAPI(_videos()).client(), extractor=ext)
        assert len(ext.calls) == ingest.MAX_CONSECUTIVE_EXTRACTION_FAILURES
        assert report.extraction_failed == 6 and report.new_items == 6
        assert any("extraction stopped" in n for n in report.notes)
        assert all(r.extracted_at_utc is None for r in _items(repo).values())

        ok = Extractor()  # the next run retries every one of them
        retry = _run(repo, yt=FakeYouTubeAPI(_videos()).client(), extractor=ok,
                     now=NOW + timedelta(hours=1))
        assert len(ok.calls) == 6 and retry.extracted == 6

    def test_a_success_resets_the_failure_count(self, repo: TradeLogRepository) -> None:
        # v1, v2 fail, v3 succeeds, v4, v5 fail, v6 succeeds: never three in a row.
        ext = Extractor(fail_on=("İçerik 1", "İçerik 2", "İçerik 4", "İçerik 5"))
        report = _run(repo, yt=FakeYouTubeAPI(_videos()).client(), extractor=ext)
        assert len(ext.calls) == 6
        assert report.extracted == 2 and report.extraction_failed == 4
        assert not any("extraction stopped" in n for n in report.notes)

    def test_an_extractor_that_raises_is_a_failed_extraction_not_a_failed_run(
        self, repo: TradeLogRepository
    ) -> None:
        def boom(text: str) -> Extraction | None:
            raise TimeoutError("hung")

        report = ingest.run(repo.session, youtube=FakeYouTubeAPI(_videos()).client(), x=None,
                            x_user_id=None, extractor=boom, model="m", now=NOW)
        assert report.extraction_failed == 6 and report.extracted == 0
        assert not any(n.startswith("YouTube failed") for n in report.notes)
        with repo.session() as s:  # the read landed; the unread items keep the window open
            assert {r.source for r in store.source_reads(s)} == {"youtube"}


class TestLongPosts:
    def test_the_extractor_reads_a_long_posts_full_text(self, repo: TradeLogRepository) -> None:
        p = post("1001", "2026-09-26T12:00:00Z", "Kesik başlangıç…")
        p["note_tweet"] = {"text": "Kesik başlangıç ve devamı: NVDA için görüşüm olumlu."}
        ext = Extractor()
        _run(repo, x=FakeXAPI([p]).client(), extractor=ext)
        assert ext.calls == ["Kesik başlangıç ve devamı: NVDA için görüşüm olumlu."]
