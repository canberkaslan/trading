"""Preflight names a missing alert path as a failure, and says so off the phone.

Preflight found the refused broker key every evening at 21:45 for nearly two
weeks and had nowhere to say so but a push to an app being rebuilt. Two gaps
made that possible and neither was ever reported: no HEALTHCHECK_URL, so no
dead-man's switch, and no alert channel other than the app.
"""

from __future__ import annotations

import pytest

from scripts import preflight
from tests.github_fake import FakeGitHub

HC = "https://hc-ping.example/uuid-not-real"


@pytest.fixture
def other_checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything but the alerting config reports healthy, with no network."""
    for name in (
        "_check_alpaca",
        "_check_anthropic",
        "_check_polygon",
        "_check_finnhub",
        "_check_openrouter",
        "_check_db",
        "_check_disk",
    ):
        monkeypatch.setattr(preflight, name, lambda failures: None)
    monkeypatch.setattr(preflight, "_check_fred", lambda: None)


def test_an_unset_healthcheck_url_is_a_named_failure_that_reaches_github(
    other_checks_pass: None, fake_github: FakeGitHub
) -> None:
    assert preflight.main() == 1

    assert len(fake_github.opened) == 1
    body = str(fake_github.opened[0]["body"])
    assert "healthcheck: HEALTHCHECK_URL unset" in body
    assert "<!-- box-alert-kind:preflight -->" in body


def test_a_missing_github_token_is_a_named_failure(
    other_checks_pass: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HEALTHCHECK_URL", HC)
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "tradingagents_us.notifications.ops_channel.send_ops_alert",
        lambda title, body, kind="ops": sent.append((title, body, kind)) or _undelivered(),
    )

    assert preflight.main() == 1
    assert "ops_alert_channel: OPS_ALERT_GITHUB_TOKEN unset" in capsys.readouterr().err
    # Still sent: the push half may reach someone even when GitHub cannot.
    assert sent and sent[0][2] == "preflight"


def test_a_malformed_url_fails_without_echoing_it(
    other_checks_pass: None,
    fake_github: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEALTHCHECK_URL", "hc-ping.com/uuid-without-scheme")
    assert preflight.main() == 1
    assert "not an http(s) URL" in fake_github.posted_text
    assert "uuid-without-scheme" not in fake_github.posted_text


def test_fully_configured_stays_quiet(
    other_checks_pass: None,
    fake_github: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEALTHCHECK_URL", HC)
    assert preflight.main() == 0
    assert fake_github.requests == []


def _undelivered():
    from tradingagents_us.notifications.ops_channel import ChannelResult, Delivery

    return Delivery((ChannelResult("push", False, "no registered devices"),))
