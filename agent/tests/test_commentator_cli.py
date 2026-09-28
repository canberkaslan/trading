"""The once-per-run commentator fetch entry, and its daily_run.sh guard (ADR-009)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from scripts import commentator_fetch as cli  # noqa: E402
from tests.test_daily_run_alerting import run_daily  # noqa: E402
from tradingagents_us.dataflows.commentator import ingest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("YOUTUBE_API_KEY", "X_BEARER_TOKEN", "COMMENTATOR_X_USER_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TRADE_LOG_DB_URL", f"sqlite:///{tmp_path / 'feed.db'}")


class TestFetchCli:
    def test_flag_off_does_nothing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("COMMENTATOR_FEED", raising=False)
        monkeypatch.setattr(ingest, "run", lambda *a, **k: pytest.fail("ran with the flag off"))
        assert cli.main([]) == 0
        assert "commentator feed off" in capsys.readouterr().out

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
