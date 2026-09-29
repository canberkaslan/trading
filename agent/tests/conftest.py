"""Shared fixtures for the ops alert channel."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from tests.github_fake import FAKE_GITHUB_TOKEN, FakeGitHub


@pytest.fixture(autouse=True)
def _no_live_alert_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer shell holding the real token must not make the suite file issues."""
    for var in ("OPS_ALERT_GITHUB_TOKEN", "OPS_ALERT_GITHUB_REPO", "HEALTHCHECK_URL"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_github(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeGitHub]:
    """GitHub configured and faked; push pointed at an empty scratch DB.

    The push half runs for real against a fresh sqlite file, so it reports "no
    registered devices", and PUSH_DISABLED backs that up. Without it the push
    half would read ./local.db, which on a dev checkout can hold a real device
    token.
    """
    from tradingagents_us.notifications import ops_channel

    fake = FakeGitHub()
    real_client = ops_channel._github_client
    monkeypatch.setattr(
        ops_channel,
        "_github_client",
        lambda token: real_client(token, transport=httpx.MockTransport(fake.handle)),
    )
    monkeypatch.setenv("OPS_ALERT_GITHUB_TOKEN", FAKE_GITHUB_TOKEN)
    monkeypatch.setenv("TRADE_LOG_DB_URL", f"sqlite:///{tmp_path / 'alerts.db'}")
    monkeypatch.setenv("PUSH_DISABLED", "1")
    yield fake


@pytest.fixture(autouse=True)
def _isolated_run_coordination(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the run-wide breakers and the host-wide submit lock out of the suite.

    A developer box can be mid daily run: its breaker files and its submit lock
    must neither leak into a test nor be taken by one.
    """
    monkeypatch.delenv("TRADINGAGENTS_RUN_STATE_DIR", raising=False)
    monkeypatch.setenv("TRADE_SUBMIT_LOCK_PATH", str(tmp_path / "trade-submit.lock"))
