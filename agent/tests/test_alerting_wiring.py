"""A preflight result reaches the off-box watchdog: preflight -> state file -> /readyz -> incident.

Every alert on the box goes through a channel that may itself be the problem:
the push needs an app, the GitHub half a token, the dead-man's switch a URL.
On 2026-09-14 all of that was missing or unread, and nothing said so for two
weeks. The watchdog runs on GitHub and files as github-actions[bot], so it needs
nothing from the box but a public /readyz.

Each hop has unit tests of its own. These drive the real preflight entry point,
the real API route and the real watchdog cycle in one line, so what is proven is
that a failure recorded on the box is the incident filed off it, not only that
each piece works when handed the right input.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from scripts import preflight, watchdog
from tests.github_fake import FakeGitHub
from tests.test_watchdog import FakeGitHub as WatchdogGitHub
from tests.test_watchdog import _Resp
from tradingagents_us.monitoring.alerting_state import read_preflight
from tradingagents_us.monitoring.liveness import (
    STATE_PREFLIGHT_FAILED,
    STATE_UNALERTED,
    STATE_UNHEARD,
    STATE_UP,
    BackupSignal,
    HealthProbe,
    HostProbe,
)

NOW = datetime(2026, 9, 14, 22, 7, tzinfo=UTC)
HC = "https://hc-ping.example/uuid-not-real"
_HARD_CHECKS = (
    "_check_alpaca",
    "_check_anthropic",
    "_check_polygon",
    "_check_finnhub",
    "_check_openrouter",
    "_check_db",
    "_check_disk",
)


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One box: preflight and the API share a state file and an environment."""
    state = tmp_path / "preflight.state.json"
    monkeypatch.setenv("PREFLIGHT_STATE_PATH", str(state))
    monkeypatch.setenv("TRADE_LOG_DB_URL", f"sqlite:///{tmp_path / 'box.db'}")
    monkeypatch.setenv("PUSH_DISABLED", "1")
    for name in _HARD_CHECKS:
        monkeypatch.setattr(preflight, name, lambda failures: None)
    monkeypatch.setattr(preflight, "_check_fred", lambda: None)
    return state


def _refuse(monkeypatch: pytest.MonkeyPatch, check: str, message: str) -> None:
    monkeypatch.setattr(
        preflight, f"_check_{check}", lambda failures: failures.append((check, message))
    )


def _readyz(monkeypatch: pytest.MonkeyPatch, *, real_db: bool = False) -> bytes:
    """GET /readyz from the real app, with a broker and DB that answer.

    `real_db` serves it from the box's own sqlite file, so the device table the
    push sender reads is the one /readyz reports on. Built here rather than via
    `deps.get_repo`, whose process-wide cache would pin the first test's file.
    """
    import os

    import api.main as api_main
    from tradingagents_us.storage import TradeLogRepository, make_engine

    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.setattr(api_main, "get_alpaca", MagicMock)
    if real_db:
        url = os.environ["TRADE_LOG_DB_URL"]
        monkeypatch.setattr(
            api_main, "get_repo", lambda: TradeLogRepository(engine=make_engine(url))
        )
    else:
        monkeypatch.setattr(api_main, "get_repo", MagicMock)
    r = TestClient(api_main.app).get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"  # alerting never changes what status means
    return r.content


def _watchdog_cycle(monkeypatch: pytest.MonkeyPatch, readyz: bytes) -> tuple[str, WatchdogGitHub]:
    """One real check_once against that /readyz body; host and backup healthy."""
    gh = WatchdogGitHub()
    monkeypatch.setattr(watchdog, "_github_request", gh)
    monkeypatch.setattr(watchdog, "probe_health", lambda url: HealthProbe(True, 200))
    monkeypatch.setattr(
        watchdog, "probe_backup", lambda repo, token, now: BackupSignal(age_hours=3.0)
    )
    monkeypatch.setattr(watchdog, "probe_host", lambda spec: HostProbe(configured=False))
    monkeypatch.setattr(watchdog.urllib.request, "urlopen", lambda *a, **k: _Resp(readyz))
    monkeypatch.setenv("GITHUB_TOKEN", "watchdog-token-not-real")
    verdict = watchdog.check_once(NOW)
    return verdict.state, gh


def _opened(gh: WatchdogGitHub) -> list[dict[str, object]]:
    return [p or {} for m, url, p in gh.calls if m == "POST" and url.endswith("/issues")]


def test_a_refused_key_on_a_box_with_no_alert_accounts_pages_off_box(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 2026-09-14 with the broker answering: a data key refused, and nothing
    # configured that could have said so anywhere but the phone.
    _refuse(monkeypatch, "anthropic", "key rejected (HTTP 401)")

    assert preflight.main() == 1
    state, gh = _watchdog_cycle(monkeypatch, _readyz(monkeypatch))

    assert state == STATE_PREFLIGHT_FAILED
    (issue,) = _opened(gh)
    assert str(issue["title"]) == "Watchdog: Box reachable, but the evening preflight failed"
    body = str(issue["body"])
    assert "failed: anthropic" in body
    assert "missing or refused: healthcheck, ops_alert_channel" in body
    assert "key rejected" not in body  # names only leave the box, never messages


def _register_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from tradingagents_us.storage import TradeLogRepository, make_engine
    from tradingagents_us.storage.device_tokens import upsert_token

    repo = TradeLogRepository(engine=make_engine(os.environ["TRADE_LOG_DB_URL"]))
    with repo.session() as s:
        upsert_token(s, token="ExponentPushToken[not-real]", user_id="dev-user",
                     platform="ios", ts=NOW)


def test_missing_alert_accounts_and_no_phone_page_as_reaching_no_one(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The live box on 2026-10-10: no off-phone account and no registered phone.
    # Preflight stays green, and nobody would hear the next failure.
    monkeypatch.delenv("PUSH_DISABLED")
    assert preflight.main() == 0
    readyz = _readyz(monkeypatch, real_db=True)
    state, gh = _watchdog_cycle(monkeypatch, readyz)

    assert state == STATE_UNHEARD
    (issue,) = _opened(gh)
    assert "its own alerts reach no one" in str(issue["title"])
    assert "only the phone" not in str(issue["body"])
    assert b"ExponentPushToken" not in readyz


def test_missing_alert_accounts_with_a_phone_page_once_as_only_the_phone(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PUSH_DISABLED")
    _register_phone(monkeypatch)
    assert preflight.main() == 0
    readyz = _readyz(monkeypatch, real_db=True)
    state, gh = _watchdog_cycle(monkeypatch, readyz)

    assert state == STATE_UNALERTED
    (issue,) = _opened(gh)
    assert "its own alerts reach only the phone" in str(issue["title"])
    assert b"ExponentPushToken" not in readyz  # a boolean leaves the box, never a token


def test_a_malformed_healthcheck_url_is_reported_and_never_published(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEALTHCHECK_URL", "hc-ping.com/uuid-without-scheme")

    preflight.main()
    readyz = _readyz(monkeypatch)
    state, gh = _watchdog_cycle(monkeypatch, readyz)

    assert state == STATE_UNHEARD  # PUSH_DISABLED=1: no phone either
    assert b"uuid-without-scheme" not in readyz
    assert "uuid-without-scheme" not in str(gh.calls)


def test_a_fully_configured_box_files_nothing(
    box: Path, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEALTHCHECK_URL", HC)

    assert preflight.main() == 0
    record = read_preflight(box)
    assert record is not None and record.ok and record.alerting_gaps == ()
    state, gh = _watchdog_cycle(monkeypatch, _readyz(monkeypatch))

    assert state == STATE_UP
    assert _opened(gh) == []


def test_readyz_before_any_preflight_reports_the_config_it_can_see(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    monkeypatch.setenv("HEALTHCHECK_URL", HC)
    payload = json.loads(_readyz(monkeypatch))

    assert payload["alerting"] == {"healthcheck": True, "github": False, "push": False}
    assert payload["preflight"] is None
