"""YouTube and X sources for the commentator feed (ADR-009). No network: fakes only."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from tests.commentator_fakes import (
    BOILERPLATE,
    CHANNEL,
    X_TOKEN,
    X_USER,
    YT_KEY,
    FakeXAPI,
    FakeYouTubeAPI,
    post,
    video,
)
from tradingagents_us.dataflows.commentator import config
from tradingagents_us.dataflows.commentator.x_source import fetch_new, post_text
from tradingagents_us.dataflows.commentator.youtube_source import (
    boilerplate_lines,
    fetch_recent,
    fetch_window,
    split_description,
)

SINCE = datetime(2026, 9, 10, tzinfo=UTC)


def _channel() -> list[dict]:
    return [
        video("v6", "2026-09-27T09:00:00Z", "Faizler Piyasaları Korkutuyor",
              ["10 yıllık tahvil faizi yükseliyor."],
              chapters=["00:00 Giriş", "03:15 Fed kararı", "1:02:03 - NVDA"]),
        video("v5", "2026-09-25T09:00:00Z", "META Muse", ["META yeni model."]),
        video("v4", "2026-09-22T09:30:00Z", "Nasdaq Coşkusu", ["Nasdaq rekor."]),
        video("v3", "2026-09-20T09:00:00Z", "Para Kazanmanın 4 Yolu", ["Kariyer."]),
        video("v2", "2026-09-18T09:00:00Z", "AMZN bulut", ["AWS büyüyor."]),
        video("v1", "2026-09-01T09:00:00Z", "Eski video", ["Eski."]),
    ]


class TestYouTubeFetch:
    def test_the_key_travels_in_a_header_never_the_url(self) -> None:
        # httpx logs every request URL at INFO; a key in the query string would
        # be written into the run log on every call.
        api = FakeYouTubeAPI(_channel())
        fetch_recent(api.client(), CHANNEL, since=SINCE)
        assert api.requests
        for r in api.requests:
            assert r.headers["X-Goog-Api-Key"] == YT_KEY
            assert YT_KEY not in str(r.url)

    def test_the_chain_is_channel_playlist_videos_and_nothing_else(self) -> None:
        api = FakeYouTubeAPI(_channel())
        fetch_recent(api.client(), CHANNEL, since=SINCE)
        assert [r.url.path.rsplit("/", 1)[-1] for r in api.requests] == [
            "channels", "playlistItems", "videos",
        ]
        assert all(r.url.host == "www.googleapis.com" for r in api.requests)

    def test_returns_items_since_the_horizon_newest_first(self) -> None:
        items = fetch_recent(FakeYouTubeAPI(_channel()).client(), CHANNEL, since=SINCE)
        assert [i.source_id for i in items] == ["v6", "v5", "v4", "v3", "v2"]
        assert all(i.published_at.tzinfo is not None for i in items)
        assert items[0].url == "https://www.youtube.com/watch?v=v6"

    def test_the_repeated_footer_is_stripped_before_extraction(self) -> None:
        items = fetch_recent(FakeYouTubeAPI(_channel()).client(), CHANNEL, since=SINCE)
        for item in items:
            for line in BOILERPLATE:
                assert line not in item.text
        assert "Nasdaq rekor." in next(i.text for i in items if i.source_id == "v4")

    def test_chapters_are_parsed_out_of_the_description(self) -> None:
        items = fetch_recent(FakeYouTubeAPI(_channel()).client(), CHANNEL, since=SINCE)
        text = items[0].text
        assert "Chapters:\n- 00:00 Giriş\n- 03:15 Fed kararı\n- 1:02:03 NVDA" in text
        assert text.startswith("Title: Faizler Piyasaları Korkutuyor")

    def test_an_undated_video_is_refused_not_guessed(self) -> None:
        # Undated cannot be kept out of a backtest that predates it.
        vids = [*_channel(), video("vx", None, "No date", ["?"])]
        items = fetch_recent(FakeYouTubeAPI(vids).client(), CHANNEL, since=SINCE)
        assert "vx" not in {i.source_id for i in items}

    def test_an_unfinished_live_stream_is_skipped(self) -> None:
        vids = [
            video("up", "2026-09-26T09:00:00Z", "Canlı: FOMC", broadcast="upcoming"),
            video("on", "2026-09-26T10:00:00Z", "Canlı: CPI", broadcast="live"),
            *_channel(),
        ]
        items = fetch_recent(FakeYouTubeAPI(vids).client(), CHANNEL, since=SINCE)
        assert {"up", "on"}.isdisjoint({i.source_id for i in items})

    def test_a_finished_live_stream_is_dated_by_its_latest_stamp(self) -> None:
        # publishedAt can predate the broadcast; a later stamp can only keep an
        # item out of a backtest, never let it in early.
        vids = [
            video("lv", "2026-09-15T08:00:00Z", "Canlı: NVDA bilanço",
                  live={"actualStartTime": "2026-09-16T20:00:00Z",
                        "actualEndTime": "2026-09-16T22:10:00Z"}),
            *_channel(),
        ]
        items = fetch_recent(FakeYouTubeAPI(vids).client(), CHANNEL, since=SINCE)
        lv = next(i for i in items if i.source_id == "lv")
        assert lv.published_at == datetime(2026, 9, 16, 22, 10, tzinfo=UTC)

    def test_an_offset_timestamp_is_normalised_to_utc(self) -> None:
        # SQLite stores no offset: a +03:00 stamp kept as wall time would sit
        # three hours late and slip past the look-ahead cutoff.
        vids = [video("tr", "2026-09-22T12:30:00+03:00", "İstanbul saati"), *_channel()]
        items = fetch_recent(FakeYouTubeAPI(vids).client(), CHANNEL, since=SINCE)
        tr = next(i for i in items if i.source_id == "tr")
        assert tr.published_at == datetime(2026, 9, 22, 9, 30, tzinfo=UTC)
        assert tr.published_at.utcoffset().total_seconds() == 0

    def test_a_video_from_another_channel_is_refused(self) -> None:
        vids = [video("other", "2026-09-26T09:00:00Z", "Squatter", channel="UCsomeoneelse"),
                *_channel()]
        items = fetch_recent(FakeYouTubeAPI(vids).client(), CHANNEL, since=SINCE)
        assert "other" not in {i.source_id for i in items}

    def test_it_stops_paging_once_it_passes_the_horizon(self) -> None:
        api = FakeYouTubeAPI(_channel(), page_size=2)
        fetch_recent(api.client(), CHANNEL, since=datetime(2026, 9, 24, tzinfo=UTC), max_pages=5)
        playlist_calls = [r for r in api.requests if r.url.path.endswith("/playlistItems")]
        assert len(playlist_calls) == 2  # page 2 reaches 09-22, before the horizon

    def test_a_read_that_reaches_the_horizon_covers_it(self) -> None:
        since = datetime(2026, 9, 24, tzinfo=UTC)
        _, covered = fetch_window(FakeYouTubeAPI(_channel(), page_size=2).client(), CHANNEL,
                                  since=since, max_pages=5)
        assert covered == since

    def test_a_read_cut_short_covers_only_back_to_the_oldest_upload_seen(self) -> None:
        _, covered = fetch_window(FakeYouTubeAPI(_channel(), page_size=2).client(), CHANNEL,
                                  since=SINCE, max_pages=1)
        assert covered == datetime(2026, 9, 25, 9, tzinfo=UTC)

    def test_the_end_of_the_playlist_covers_the_horizon(self) -> None:
        _, covered = fetch_window(FakeYouTubeAPI(_channel()[:3]).client(), CHANNEL,
                                  since=SINCE, max_pages=1)
        assert covered == SINCE

    def test_a_quota_refusal_raises_for_the_ingest_step_to_report(self) -> None:
        api = FakeYouTubeAPI(_channel())
        api.fail_with = 403
        with pytest.raises(Exception):  # noqa: B017 — httpx.HTTPStatusError
            fetch_recent(api.client(), CHANNEL, since=SINCE)


class TestBoilerplate:
    def test_five_repeats_is_boilerplate(self) -> None:
        descs = ["footer\nunique a"] * 5
        assert "footer" in boilerplate_lines(descs)

    def test_four_repeats_is_not(self) -> None:
        assert "footer" not in boilerplate_lines(["footer\nx"] * 4)

    def test_repeats_within_one_description_count_once(self) -> None:
        assert "dup" not in boilerplate_lines(["dup\ndup\ndup\ndup\ndup"])

    def test_whitespace_differences_do_not_hide_a_repeat(self) -> None:
        descs = ["Abone  ol:  link", " Abone ol: link ", "Abone ol: link",
                 "Abone ol:\tlink", "Abone ol: link"]
        assert "Abone ol: link" in boilerplate_lines(descs)

    def test_split_keeps_body_and_drops_boilerplate(self) -> None:
        chapters, body = split_description("Satır 1\nfooter\n00:00 Giriş", frozenset({"footer"}))
        assert chapters == [("00:00", "Giriş")]
        assert body == "Satır 1"


class TestXFetch:
    def _api(self) -> FakeXAPI:
        return FakeXAPI([
            post("1003", "2026-09-27T12:00:00.000Z", "$TSLA pozisyon büyüklüğü önemli"),
            post("1002", "2026-09-26T12:00:00.000Z", "Nasdaq yükselişi sürüyor"),
            post("1001", "2026-09-25T12:00:00.000Z", "Yeni video yayında"),
        ])

    def test_by_numeric_id_with_the_documented_parameters(self) -> None:
        api = self._api()
        fetch_new(api.client(), X_USER, since_id="1001", first_run_max=20,
                  expected_username="BoraOzkentNSDQ")
        (req,) = api.requests
        assert req.url.host == "api.x.com"
        assert req.url.path == f"/2/users/{X_USER}/tweets"
        p = req.url.params
        assert p["since_id"] == "1001"
        assert p["exclude"] == "replies,retweets"
        assert p["tweet.fields"] == "created_at,entities,lang,note_tweet"
        assert req.headers["Authorization"] == f"Bearer {X_TOKEN}"
        assert X_TOKEN not in str(req.url)

    def test_a_long_post_is_read_from_note_tweet_not_the_truncated_text(self) -> None:
        head = "Nasdaq " * 40
        full = head + "ve NVDA için hedefim yukarı yönlü."
        long_post = post("1004", "2026-09-28T12:00:00.000Z", head[:280])
        long_post["note_tweet"] = {"text": full, "entities": {}}
        api = FakeXAPI([long_post])
        (item,) = fetch_new(api.client(), X_USER, since_id=None, first_run_max=20,
                            expected_username="BoraOzkentNSDQ")
        assert item.text == full.strip()

    def test_the_deletion_check_also_asks_for_note_tweet(self) -> None:
        # The retry of a failed extraction reads its text from this answer.
        api = self._api()
        api.client().lookup_alive(["1001"])
        (req,) = api.requests
        assert "note_tweet" in req.url.params["tweet.fields"].split(",")

    @pytest.mark.parametrize("note", [None, {}, {"text": ""}, {"text": "   "}, "junk"])
    def test_without_a_usable_note_tweet_the_text_is_used(self, note: object) -> None:
        p = post("1005", "2026-09-28T12:00:00.000Z", "Kısa bir gönderi")
        if note is not None:
            p["note_tweet"] = note
        assert post_text(p) == "Kısa bir gönderi"

    def test_only_posts_after_since_id_newest_first(self) -> None:
        items = fetch_new(self._api().client(), X_USER, since_id="1001", first_run_max=20,
                          expected_username="BoraOzkentNSDQ")
        assert [i.source_id for i in items] == ["1003", "1002"]
        # Permalink by post id, not by handle: a handle can change hands.
        assert items[0].url == "https://x.com/i/web/status/1003"

    def test_the_first_run_buys_at_most_first_run_max(self) -> None:
        api = self._api()
        fetch_new(api.client(), X_USER, since_id=None, first_run_max=5,
                  expected_username="BoraOzkentNSDQ")
        assert api.requests[0].url.params["max_results"] == "5"
        assert "since_id" not in api.requests[0].url.params

    def test_a_rename_is_logged_and_the_id_still_followed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        api = FakeXAPI([post("1", "2026-09-27T12:00:00Z", "x")], username="SomethingElse")
        with caplog.at_level(logging.WARNING):
            items = fetch_new(api.client(), X_USER, since_id=None, first_run_max=5,
                              expected_username="BoraOzkentNSDQ")
        assert [i.source_id for i in items] == ["1"]
        assert "SomethingElse" in caplog.text
        # And no lookup by handle was made to "fix" it.
        assert not any("/users/by/" in r.url.path for r in api.requests)

    def test_an_undated_post_is_refused(self) -> None:
        api = FakeXAPI([post("1", None, "tarihsiz"), post("2", "2026-09-27T12:00:00Z", "ok")])
        items = fetch_new(api.client(), X_USER, since_id=None, first_run_max=5,
                          expected_username="BoraOzkentNSDQ")
        assert [i.source_id for i in items] == ["2"]

    def test_lookup_alive_omits_deleted_posts(self) -> None:
        api = self._api()
        api.delete("1002")
        alive = api.client().lookup_alive(["1001", "1002", "1003"])
        assert set(alive) == {"1001", "1003"}


class TestPinnedIdentity:
    def test_the_youtube_channel_is_pinned_by_id(self) -> None:
        assert config.YOUTUBE_CHANNEL_ID == "UCrXj09uA0Nqv65st774NEKw"

    def test_no_x_id_means_x_is_inert(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("COMMENTATOR_X_USER_ID", raising=False)
        monkeypatch.setattr(config, "X_USER_ID", None)
        assert config.x_user_id() is None

    def test_a_handle_is_never_accepted_as_the_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The old @BoraOzkent now belongs to someone else.
        for handle in ("BoraOzkent", "@BoraOzkentNSDQ", "12ab"):
            monkeypatch.setenv("COMMENTATOR_X_USER_ID", handle)
            assert config.x_user_id() is None

    def test_a_numeric_id_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COMMENTATOR_X_USER_ID", " 1234567890 ")
        assert config.x_user_id() == "1234567890"

    @pytest.mark.parametrize("value", ["", "...", "   "])
    def test_placeholder_secrets_count_as_absent(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        monkeypatch.setenv("X_BEARER_TOKEN", value)
        monkeypatch.setenv("YOUTUBE_API_KEY", value)
        monkeypatch.setenv(config.YOUTUBE_CLEARED_ENV, "1")  # so the key is what is judged
        assert config.x_bearer_token() is None
        assert config.youtube_api_key() is None

    @pytest.mark.parametrize("cleared", [None, "", "0", "true", "yes"])
    def test_a_youtube_key_is_refused_until_the_source_is_cleared(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        cleared: str | None,
    ) -> None:
        monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key-not-real-0001")
        if cleared is None:
            monkeypatch.delenv(config.YOUTUBE_CLEARED_ENV, raising=False)
        else:
            monkeypatch.setenv(config.YOUTUBE_CLEARED_ENV, cleared)
        with caplog.at_level(logging.WARNING):
            assert config.youtube_api_key() is None
        assert config.YOUTUBE_CLEARED_ENV in caplog.text
        assert "yt-key-not-real-0001" not in caplog.text
        assert config.YOUTUBE_CLEARED_ENV in config.youtube_skip_reason()

    def test_a_cleared_source_uses_its_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key-not-real-0001")
        monkeypatch.setenv(config.YOUTUBE_CLEARED_ENV, "1")
        assert config.youtube_api_key() == "yt-key-not-real-0001"

    def test_clearance_without_a_key_is_still_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        monkeypatch.setenv(config.YOUTUBE_CLEARED_ENV, "1")
        assert config.youtube_api_key() is None
        assert config.youtube_skip_reason() == "YOUTUBE_API_KEY is not set"

    def test_resolve_is_a_separate_one_off_call(self) -> None:
        api = FakeXAPI([])
        assert api.client().resolve_user_id("@BoraOzkentNSDQ") == X_USER
        assert api.requests[0].url.path == "/2/users/by/username/BoraOzkentNSDQ"
