"""Fail-fast primitives: bounded retry pauses and the run-wide breaker."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tradingagents_us.dataflows import fail_fast
from tradingagents_us.dataflows.fail_fast import RUN_STATE_DIR_ENV, RunBreaker, retry_delay

AGENT = Path(__file__).resolve().parent.parent


class TestRetryDelay:
    def test_a_short_retry_after_is_honoured(self) -> None:
        assert retry_delay("2") == 2.0
        assert retry_delay("0") == 0.0

    def test_a_long_retry_after_is_capped(self) -> None:
        # Reddit asks for a minute; a council does not wait a minute.
        assert retry_delay("60") == fail_fast.RETRY_AFTER_CAP_S
        assert retry_delay("3600", cap_s=2.0) == 2.0

    @pytest.mark.parametrize("header", [None, "Wed, 21 Oct 2026 07:28:00 GMT", "-4", "soon"])
    def test_no_usable_header_means_a_short_jittered_pause(self, header: str | None) -> None:
        for _ in range(50):
            wait = retry_delay(header)
            assert 0.0 <= wait <= fail_fast.RETRY_BASE_S * (1 + fail_fast.JITTER_FRACTION)


class TestRunBreaker:
    @pytest.fixture(autouse=True)
    def _fresh(self):
        RunBreaker("t-src", 2).reset()
        yield
        RunBreaker("t-src", 2).reset()

    def test_opens_after_the_limit_and_stays_open(self) -> None:
        b = RunBreaker("t-src", limit=2)
        assert b.record(success=False) is False
        assert not b.is_open()
        assert b.record(success=False) is True
        assert b.is_open()
        # A late success (a call already in flight) does not re-close it.
        b.record(success=True)
        assert b.is_open()

    def test_a_success_resets_the_count(self) -> None:
        b = RunBreaker("t-src", limit=2)
        b.record(success=False)
        b.record(success=True)
        b.record(success=False)
        assert not b.is_open()

    def test_the_count_is_shared_through_the_run_state_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(RUN_STATE_DIR_ENV, str(tmp_path))
        RunBreaker("t-src", 2).record(success=False)
        # A different instance: in the daily run, a different ticker process.
        other = RunBreaker("t-src", 2)
        assert other.record(success=False) is True
        assert (tmp_path / "breaker-t-src.json").exists()

    def test_the_count_crosses_real_processes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # What the daily run actually does: one process per ticker.
        code = (
            "from tradingagents_us.dataflows.fail_fast import RunBreaker;"
            "RunBreaker('t-src', 2).record(success=False)"
        )
        env = {"PATH": "/usr/bin:/bin", RUN_STATE_DIR_ENV: str(tmp_path), "PYTHONPATH": str(AGENT)}
        for _ in range(2):
            subprocess.run([sys.executable, "-c", code], env=env, check=True, cwd=AGENT)
        monkeypatch.setenv(RUN_STATE_DIR_ENV, str(tmp_path))
        assert RunBreaker("t-src", 2).is_open()

    def test_a_corrupt_state_file_counts_from_zero(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(RUN_STATE_DIR_ENV, str(tmp_path))
        (tmp_path / "breaker-t-src.json").write_text("{not json", encoding="utf-8")
        b = RunBreaker("t-src", 2)
        assert not b.is_open()
        b.record(success=False)
        assert not b.is_open()

    def test_an_unusable_state_dir_falls_back_to_the_process(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("", encoding="utf-8")
        monkeypatch.setenv(RUN_STATE_DIR_ENV, str(blocker / "sub"))
        b = RunBreaker("t-src", 2)
        b.record(success=False)
        assert b.record(success=False) is True

    def test_a_zero_limit_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            RunBreaker("t-src", 0)
