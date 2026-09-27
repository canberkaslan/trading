"""Box alerts reach a channel that is not the phone app.

Every alert on the box used to be an Expo push to the devices in local.db. While
the app was being rebuilt, a refused broker key aborted every nightly run for
nearly two weeks, and the only channel that could have said so went nowhere.
"""

from __future__ import annotations

import httpx
import pytest

from tests.github_fake import (
    BOT_LOGIN,
    FAKE_GITHUB_TOKEN,
    FAKE_WORKFLOW_TOKEN,
    FakeGitHub,
    load_workflow,
    workflow_step,
)
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

    def test_the_box_dispatches_and_the_workflow_token_writes_the_issue(
        self, fake_github: FakeGitHub
    ) -> None:
        # GitHub does not notify you about your own activity, and the box's
        # token is the owner's PAT. An issue it wrote would be the owner's own
        # issue and nobody would be emailed; the bot's issue notifies.
        send_ops_alert("Daily run: 11 ticker(s) failed", "Failed: SPY", kind="daily_run")

        calls = list(zip(fake_github.requests, fake_github.auth_headers, strict=True))
        box = [(m, path) for (m, path, _), auth in calls if auth == f"Bearer {FAKE_GITHUB_TOKEN}"]
        assert box == [
            ("POST", "/repos/canberkaslan/trading/actions/workflows/box-alert.yml/dispatches")
        ]
        writes = [auth for (m, path, _), auth in calls if m == "POST" and "/issues" in path]
        assert writes == [f"Bearer {FAKE_WORKFLOW_TOKEN}"]
        assert fake_github.dispatches[0]["ref"] == "main"
        assert fake_github.dispatches[0]["inputs"]["kind"] == "daily_run"

    def test_a_repeat_is_a_comment_on_the_open_issue_not_a_new_issue(
        self, fake_github: FakeGitHub
    ) -> None:
        fake_github.open_issues = [
            {
                "number": 9,
                "body": "<!-- box-alert-kind:daily_run -->\n\nfirst",
                "user": {"login": BOT_LOGIN},
            },
        ]
        delivery = send_ops_alert("Daily run: 11 ticker(s) failed", "Failed: SPY", kind="daily_run")

        assert delivery.delivered
        assert fake_github.opened == []
        assert fake_github.comments[0][0] == "/repos/canberkaslan/trading/issues/9/comments"
        assert "11 ticker(s) failed" in str(fake_github.comments[0][1]["body"])

    def test_other_kinds_and_pull_requests_are_not_mistaken_for_the_thread(
        self, fake_github: FakeGitHub
    ) -> None:
        bot = {"login": BOT_LOGIN}
        fake_github.open_issues = [
            {"number": 3, "body": "<!-- box-alert-kind:backup -->", "user": bot},
            {
                "number": 4,
                "body": "<!-- box-alert-kind:naked_book -->",
                "pull_request": {},
                "user": bot,
            },
        ]
        send_ops_alert("t", "b", kind="naked_book")
        assert len(fake_github.opened) == 1
        assert fake_github.comments == []

    def test_an_issue_a_stranger_opened_with_the_marker_is_not_the_thread(
        self, fake_github: FakeGitHub
    ) -> None:
        # The default repo is public: anyone can open an issue carrying the
        # marker, and alerts must not start landing on it.
        fake_github.open_issues = [
            {
                "number": 5,
                "body": "<!-- box-alert-kind:daily_run -->",
                "user": {"login": "someone-else"},
            }
        ]
        send_ops_alert("t", "b", kind="daily_run")
        assert len(fake_github.opened) == 1
        assert fake_github.comments == []

    def test_a_second_alert_of_a_kind_joins_the_issue_the_first_opened(
        self, fake_github: FakeGitHub
    ) -> None:
        send_ops_alert("Preflight FAILED (1)", "alpaca: 401", kind="preflight")
        send_ops_alert("Preflight FAILED (1)", "alpaca: 401", kind="preflight")
        assert len(fake_github.opened) == 1
        assert [path for path, _ in fake_github.comments] == [
            "/repos/canberkaslan/trading/issues/41/comments"
        ]

    def test_a_kind_that_is_not_a_plain_token_is_sent_as_ops(
        self, fake_github: FakeGitHub
    ) -> None:
        # The kind goes inside an HTML comment in the issue body.
        send_ops_alert("t", "b", kind="x --> <b>")
        assert fake_github.dispatches[0]["inputs"]["kind"] == "ops"
        assert "<!-- box-alert-kind:ops -->" in str(fake_github.opened[0]["body"])

    def test_the_repo_can_be_pointed_somewhere_private(
        self, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPS_ALERT_GITHUB_REPO", "someone/private-ops")
        send_ops_alert("t", "b", kind="ops")
        assert fake_github.requests[0][1] == (
            "/repos/someone/private-ops/actions/workflows/box-alert.yml/dispatches"
        )
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


class TestTheWorkflow:
    """`.github/workflows/box-alert.yml` and the script it runs on the runner."""

    def test_it_writes_issues_with_its_own_token_and_can_do_nothing_else(self) -> None:
        wf = load_workflow()
        assert wf["permissions"] == {"contents": "read", "issues": "write"}
        assert workflow_step()["env"]["GITHUB_TOKEN"] == "${{ secrets.GITHUB_TOKEN }}"

    def test_no_input_is_spliced_into_the_shell(self) -> None:
        # Box-supplied strings: inside `run:` an expression is substituted into
        # the script text before the shell parses it.
        (job,) = load_workflow()["jobs"].values()
        for step in job["steps"]:
            assert "${{" not in str(step.get("run", ""))

    def test_a_failed_write_turns_the_run_red(self) -> None:
        # GitHub emails the dispatcher (the owner) about a failed run: the last
        # way this alert can still reach anyone.
        from scripts import box_alert_issue

        def refuse(method: str, url: str, payload: object = None) -> object:
            raise RuntimeError("HTTP 403")

        env = {"GITHUB_TOKEN": "t", "ALERT_KIND": "daily_run", "ALERT_TITLE": "x"}
        assert box_alert_issue.main(env, request=refuse) == 1
        assert box_alert_issue.main({"ALERT_KIND": "daily_run"}) == 1  # no token


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
