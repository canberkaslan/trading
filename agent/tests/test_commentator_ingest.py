"""Fetch -> extract once -> store -> purge, end to end on fakes (ADR-009)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select

from tests.commentator_fakes import X_USER, FakeXAPI, FakeYouTubeAPI, post, video
from tradingagents_us.dataflows.commentator import ingest
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


def _run(repo, *, yt=None, x=None, x_user=X_USER, extractor=None, now=NOW, **kw):
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
            visible = store.extracted_items_between(s, NOW - timedelta(days=10), NOW)
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
    def test_youtube_items_expire_thirty_days_after_the_fetch(
        self, repo: TradeLogRepository
    ) -> None:
        _run(repo, yt=FakeYouTubeAPI(_videos()).client())
        row = _items(repo)["youtube:v1"]
        assert store.aware(row.expires_at_utc) == NOW + timedelta(days=30)
        report = _run(repo, now=NOW + timedelta(days=30, seconds=1), x=None)
        assert report.purged_expired == 6
        assert _items(repo) == {}

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


class TestMissingCredentials:
    def test_no_youtube_key_is_a_logged_skip(self, repo: TradeLogRepository) -> None:
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

    def test_x_items_expire_on_the_short_window(
        self, repo: TradeLogRepository, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("COMMENTATOR_X_RETENTION_DAYS", raising=False)
        _run(repo, x=FakeXAPI([post("1001", "2026-09-26T12:00:00Z", "a")]).client())
        row = _items(repo)["x:1001"]
        assert store.aware(row.expires_at_utc) == NOW + timedelta(days=8)
