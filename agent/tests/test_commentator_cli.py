"""The once-per-run commentator fetch entry, and its daily_run.sh guard (ADR-009)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from scripts import commentator_fetch as cli  # noqa: E402
from tests.test_daily_run_alerting import run_daily  # noqa: E402
from tradingagents_us.dataflows.commentator import ingest  # noqa: E402
from tradingagents_us.dataflows.commentator import youtube_source as yt_source  # noqa: E402
from tradingagents_us.storage import TradeLogRepository  # noqa: E402
from tradingagents_us.storage import commentator as store  # noqa: E402

_DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "hetzner"


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("YOUTUBE_API_KEY", "X_BEARER_TOKEN", "COMMENTATOR_X_USER_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TRADE_LOG_DB_URL", f"sqlite:///{tmp_path / 'feed.db'}")


def _repo(tmp_path: Path) -> TradeLogRepository:
    return TradeLogRepository(engine=create_engine(f"sqlite:///{tmp_path / 'feed.db'}"))


def _stored_expired_video(tmp_path: Path) -> TradeLogRepository:
    """A row as an `--ignore-flag` backfill 45 days ago would have left it."""
    repo = _repo(tmp_path)
    fetched = datetime.now(UTC) - timedelta(days=45)
    with repo.session() as s:
        store.upsert_item(
            s, source="youtube", source_id="old", channel_id="c", url="u",
            published_at=fetched - timedelta(days=1), content_sha256="h", now=fetched,
            expires_at=fetched + timedelta(days=29), extraction=None, extraction_model=None,
        )
    return repo


def _item_ids(repo: TradeLogRepository) -> list[str]:
    with repo.session() as s:
        return store.source_ids(s, "youtube")


class TestFetchCli:
    def test_flag_off_fetches_nothing_but_retention_still_runs(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        monkeypatch.delenv("COMMENTATOR_FEED", raising=False)
        monkeypatch.setattr(ingest, "run", lambda *a, **k: pytest.fail("ran with the flag off"))
        repo = _stored_expired_video(tmp_path)
        assert cli.main([]) == 0
        out = capsys.readouterr().out
        assert "commentator feed off" in out
        assert "commentator retention: purged_expired=1" in out
        assert _item_ids(repo) == []

    def test_a_flag_off_dry_run_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("COMMENTATOR_FEED", raising=False)
        repo = _stored_expired_video(tmp_path)
        assert cli.main(["--dry-run"]) == 0
        assert _item_ids(repo) == ["old"]

    @pytest.mark.parametrize("flag", ["1", None])
    def test_retention_only_purges_whatever_the_flag_and_reads_no_source(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        tmp_path: Path, flag: str | None,
    ) -> None:
        if flag is None:
            monkeypatch.delenv("COMMENTATOR_FEED", raising=False)
        else:
            monkeypatch.setenv("COMMENTATOR_FEED", flag)
        monkeypatch.setenv("YOUTUBE_API_KEY", "yt-key-not-real-0001")
        monkeypatch.setattr(ingest, "run", lambda *a, **k: pytest.fail("fetched"))
        monkeypatch.setattr(
            yt_source.YouTubeClient, "__init__", lambda *a, **k: pytest.fail("YouTube client")
        )
        repo = _stored_expired_video(tmp_path)
        assert cli.main(["--retention-only"]) == 0
        assert "purged_expired=1" in capsys.readouterr().out
        assert _item_ids(repo) == []
        with repo.session() as s:
            assert store.status_at(s, store.RETENTION_PASS) is not None

    def test_a_failed_retention_pass_exits_nonzero_so_the_timer_pages(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def boom(*a, **k):
            raise RuntimeError("disk full")

        monkeypatch.setattr(ingest, "enforce_retention", boom)
        assert cli.main(["--retention-only"]) == 1
        assert "retention pass FAILED" in capsys.readouterr().out

    def test_no_keys_is_a_clean_logged_noop(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("COMMENTATOR_FEED", "1")
        assert cli.main([]) == 0
        out = capsys.readouterr().out
        assert "YouTube skipped: YOUTUBE_API_KEY is not set" in out
        assert "X skipped: X_BEARER_TOKEN is not set" in out
        assert "extracted=0" in out

    def test_a_crash_never_fails_the_run(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("COMMENTATOR_FEED", "1")

        def boom(*a, **k):
            raise RuntimeError("disk full")

        monkeypatch.setattr(ingest, "run", boom)
        assert cli.main([]) == 0
        assert "non-fatal" in capsys.readouterr().out

    def test_resolving_an_id_without_a_token_calls_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main(["--resolve-x-id", "BoraOzkentNSDQ"]) == 0
        assert "X_BEARER_TOKEN is not set" in capsys.readouterr().err


class TestDailyRunGuard:
    def test_flag_off_the_step_does_not_run(self, tmp_path: Path) -> None:
        run = run_daily(tmp_path)
        assert run.rc == 0, run.output
        assert "scripts.commentator_fetch" not in run.modules()
        assert "commentator" not in run.output

    def test_flag_on_it_runs_once_before_the_first_ticker(self, tmp_path: Path) -> None:
        run = run_daily(tmp_path, COMMENTATOR_FEED="1")
        assert run.rc == 0, run.output
        mods = run.modules()
        assert mods.count("scripts.commentator_fetch") == 1  # once per run, not per ticker
        assert mods.index("scripts.commentator_fetch") < mods.index("scripts.trade")

    def test_a_failed_fetch_does_not_cost_the_trading_day(self, tmp_path: Path) -> None:
        run = run_daily(tmp_path, COMMENTATOR_FEED="1", FAKE_RC_scripts_commentator_fetch="1")
        assert run.rc == 0, run.output
        assert run.modules().count("scripts.trade") == 2
        assert "commentator fetch failed (non-fatal)" in run.output


class TestRetentionTimer:
    """The daily pass has its own unit: not Mon-Fri, not behind the flag or the kill switch."""

    def test_the_timer_fires_every_day_at_most_a_day_apart(self) -> None:
        text = (_DEPLOY / "ai-trader-commentator-retention.timer").read_text()
        settings = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
        assert any(ln.startswith("OnCalendar=*-*-* ") for ln in settings)
        assert "Persistent=true" in settings
        # The 29-day deadline assumes passes at most a day apart.
        assert not any(ln.startswith("RandomizedDelaySec") for ln in settings)

    def test_the_service_runs_the_retention_pass_and_pages_on_failure(self) -> None:
        service = (_DEPLOY / "ai-trader-commentator-retention.service").read_text()
        assert "-m scripts.commentator_fetch --retention-only" in service
        assert "OnFailure=ai-trader-alert.service" in service

    def test_install_ships_and_names_both_units(self) -> None:
        install = (_DEPLOY / "install.sh").read_text()
        assert "ai-trader-commentator-retention.service" in install
        assert install.count("ai-trader-commentator-retention.timer") >= 2  # copied + enabled
