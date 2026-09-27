"""Box alerts reach a channel that is not the phone app.

Every alert on the box used to be an Expo push to the devices in local.db. While
the app was being rebuilt, a refused broker key aborted every nightly run for
nearly two weeks, and the only channel that could have said so went nowhere.
"""

from __future__ import annotations

import httpx
import pytest

from tests.github_fake import FAKE_GITHUB_TOKEN, FakeGitHub
from tradingagents_us.notifications import ops_channel
from tradingagents_us.notifications.ops_channel import (
    ChannelResult,
    Delivery,
    scrub,
    send_ops_alert,
)


def _github(delivery: Delivery) -> ChannelResult:
    return next(r for r in delivery.results if r.channel == "github")


class TestGitHubChannel:
    def test_opens_an_issue_when_none_is_open_for_the_kind(self, fake_github: FakeGitHub) -> None:
        delivery = send_ops_alert("Preflight FAILED (1)", "alpaca: HTTP 401", kind="preflight")

        assert delivery.delivered
        assert len(fake_github.opened) == 1
        issue = fake_github.opened[0]
        assert "Preflight FAILED" in str(issue["title"])
        assert "alpaca: HTTP 401" in str(issue["body"])
        assert "<!-- box-alert-kind:preflight -->" in str(issue["body"])
        # Not the watchdog's label: the watchdog closes its own issues when the
        # box answers, and must never close one of these.
        assert issue["labels"] == ["box-alert"]
        assert fake_github.requests[-1][1] == "/repos/canberkaslan/trading/issues"
        assert fake_github.auth_headers[-1] == f"Bearer {FAKE_GITHUB_TOKEN}"

    def test_a_repeat_is_a_comment_on_the_open_issue_not_a_new_issue(
        self, fake_github: FakeGitHub
    ) -> None:
        fake_github.open_issues = [
            {"number": 9, "body": "<!-- box-alert-kind:daily_run -->\n\nfirst"},
        ]
        delivery = send_ops_alert("Daily run: 11 ticker(s) failed", "Failed: SPY", kind="daily_run")

        assert delivery.delivered
        assert fake_github.opened == []
        assert fake_github.comments[0][0] == "/repos/canberkaslan/trading/issues/9/comments"
        assert "11 ticker(s) failed" in str(fake_github.comments[0][1]["body"])

    def test_other_kinds_and_pull_requests_are_not_mistaken_for_the_thread(
        self, fake_github: FakeGitHub
    ) -> None:
        fake_github.open_issues = [
            {"number": 3, "body": "<!-- box-alert-kind:backup -->"},
            {"number": 4, "body": "<!-- box-alert-kind:naked_book -->", "pull_request": {}},
        ]
        send_ops_alert("t", "b", kind="naked_book")
        assert len(fake_github.opened) == 1
        assert fake_github.comments == []

    def test_the_repo_can_be_pointed_somewhere_private(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPS_ALERT_GITHUB_REPO", "someone/private-ops")
        send_ops_alert("t", "b", kind="ops")
        assert fake_github.requests[-1][1] == "/repos/someone/private-ops/issues"

    def test_unconfigured_is_reported_by_name_not_silent(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPS_ALERT_GITHUB_TOKEN")
        delivery = send_ops_alert("t", "b")
        assert not _github(delivery).delivered
        assert "OPS_ALERT_GITHUB_TOKEN" in _github(delivery).detail
        assert fake_github.requests == []

    def test_a_github_outage_never_raises_into_the_caller(self, fake_github: FakeGitHub) -> None:
        fake_github.error = httpx.ConnectError("boom")
        delivery = send_ops_alert("t", "b")
        assert not delivery.delivered
        assert "failed" in _github(delivery).detail

    def test_a_rejected_token_is_not_counted_as_delivered(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            fake_github, "handle", lambda request: httpx.Response(401, json={"message": "Bad"})
        )
        assert not send_ops_alert("t", "b").delivered


class TestBothChannels:
    def test_push_is_still_attempted_alongside_github(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pushed: list[tuple[str, str, str]] = []

        def push(title: str, body: str, kind: str) -> ChannelResult:
            pushed.append((title, body, kind))
            return ChannelResult("push", True, "sent to 1 device(s)")

        monkeypatch.setattr(ops_channel, "_send_push", push)
        delivery = send_ops_alert("title", "body", kind="backup")
        assert pushed == [("title", "body", "backup")]
        assert [r.channel for r in delivery.results] == ["push", "github"]

    def test_no_registered_device_no_longer_means_nobody_hears(
        self, fake_github: FakeGitHub
    ) -> None:
        # The real push half against an empty device table: exactly the state
        # the box was in while the app was rebuilt.
        delivery = send_ops_alert("Preflight FAILED (1)", "alpaca: 401")
        push = next(r for r in delivery.results if r.channel == "push")
        assert not push.delivered
        assert delivery.delivered
        assert len(fake_github.opened) == 1


class TestNothingSecretIsPublished:
    def test_secret_env_values_and_url_credentials_are_scrubbed(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ALPACA_API_SECRET", "alpaca-secret-value-123")
        monkeypatch.setenv("HEALTHCHECK_URL", "https://hc-ping.com/secret-uuid-abc")
        body = (
            "polygon: GET https://api.polygon.io/v2/x?apiKey=POLYGONKEY999 failed; "
            "alpaca-secret-value-123 in a traceback; "
            "db postgresql://trader:hunter2pass@db:5432/x; "
            "ping https://hc-ping.com/secret-uuid-abc"
        )
        send_ops_alert("Preflight FAILED", body)

        published = fake_github.posted_text
        for leaked in (
            "POLYGONKEY999",
            "alpaca-secret-value-123",
            "hunter2pass",
            "secret-uuid-abc",
            FAKE_GITHUB_TOKEN,
        ):
            assert leaked not in published
        assert "api.polygon.io" in published  # the useful part survives

    def test_ordinary_text_is_untouched(self) -> None:
        line = "alpaca: base URL is NOT paper: 'https://api.alpaca.markets/v2'"
        assert scrub(line) == line
