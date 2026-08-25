"""The reporting half of the off-box watchdog.

The policy decides *what* is wrong; this decides how often a human hears about
it. The watchdog wakes every 30 minutes, so the assertions that matter are the
ones about staying quiet: a dark host that commented on each run would post 48
times a day about a single outage, and by day two nobody reads it.
"""

from __future__ import annotations

import urllib.error
from datetime import UTC, datetime

import pytest

from scripts import watchdog
from tradingagents_us.monitoring.liveness import (
    STATE_DARK,
    STATE_DEGRADED,
    STATE_EDGE_DOWN,
    BackupSignal,
    HealthProbe,
    classify,
)

NOW = datetime(2026, 8, 25, 6, 30, tzinfo=UTC)
REPO = "canberkaslan/trading"


class FakeGitHub:
    """Records every write so the tests can assert on silence as well as noise."""

    def __init__(self, open_issue: dict[str, object] | None = None) -> None:
        self.open_issue = open_issue
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []

    def __call__(
        self,
        method: str,
        url: str,
        token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> object:
        self.calls.append((method, url, payload))
        if method == "GET" and "/issues?" in url:
            return [self.open_issue] if self.open_issue else []
        if method == "POST" and url.endswith("/issues"):
            return {"number": 7}
        return {}

    @property
    def writes(self) -> list[tuple[str, str, dict[str, object] | None]]:
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> FakeGitHub:
    fake = FakeGitHub()
    monkeypatch.setattr(watchdog, "_github_request", fake)
    return fake


def _verdict(state: str):
    matrix = {
        STATE_DARK: (HealthProbe(False, 530), BackupSignal(age_hours=30.0)),
        STATE_EDGE_DOWN: (HealthProbe(False, 530), BackupSignal(age_hours=3.0)),
        STATE_DEGRADED: (HealthProbe(True, 200), BackupSignal(age_hours=30.0)),
        "up": (HealthProbe(True, 200), BackupSignal(age_hours=3.0)),
    }
    health, backup = matrix[state]
    return classify(health, backup)


def _issue(state: str, number: int = 7) -> dict[str, object]:
    return {
        "number": number,
        "body": f"{watchdog._state_marker(state)}\n\nsomething happened",
    }


class TestIncidentLifecycle:
    def test_first_sighting_opens_one_issue(self, gh: FakeGitHub) -> None:
        result = watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        assert "opened #7" in result
        posts = [c for c in gh.writes if c[0] == "POST"]
        assert len(posts) == 1
        assert posts[0][2]["labels"] == [watchdog.ISSUE_LABEL]

    def test_same_state_on_the_next_run_says_nothing(self, gh: FakeGitHub) -> None:
        gh.open_issue = _issue(STATE_DARK)
        result = watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        assert "staying quiet" in result
        assert gh.writes == []

    def test_de_escalation_also_stays_quiet(self, gh: FakeGitHub) -> None:
        """Dark -> edge_down is still an open outage, not news worth a second ping."""
        gh.open_issue = _issue(STATE_DARK)
        result = watchdog.report(_verdict(STATE_EDGE_DOWN), REPO, "t", NOW)
        assert "staying quiet" in result
        assert gh.writes == []

    def test_escalation_comments_once_and_retitles(self, gh: FakeGitHub) -> None:
        gh.open_issue = _issue(STATE_DEGRADED)
        result = watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        assert "escalated #7" in result
        methods = [c[0] for c in gh.writes]
        assert methods == ["PATCH", "POST"]
        assert "dark" in str(gh.writes[0][2]["title"]).lower()

    def test_recovery_closes_the_issue_with_a_comment(self, gh: FakeGitHub) -> None:
        gh.open_issue = _issue(STATE_DARK)
        result = watchdog.report(_verdict("up"), REPO, "t", NOW)
        assert "closed #7" in result
        methods = [c[0] for c in gh.writes]
        assert methods == ["POST", "PATCH"]
        assert gh.writes[1][2] == {"state": "closed"}

    def test_healthy_with_nothing_open_is_a_no_op(self, gh: FakeGitHub) -> None:
        """The normal case, ~48 times a day: touch nothing."""
        result = watchdog.report(_verdict("up"), REPO, "t", NOW)
        assert "nothing to do" in result
        assert gh.writes == []

    def test_issue_without_a_marker_is_treated_as_new_news(self, gh: FakeGitHub) -> None:
        """A hand-filed or hand-edited issue must not be able to mute the watchdog."""
        gh.open_issue = {"number": 7, "body": "someone opened this by hand"}
        result = watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        assert "escalated #7" in result


class TestStateMarker:
    def test_round_trips(self) -> None:
        body = f"{watchdog._state_marker(STATE_DARK)}\n\ntext"
        assert watchdog._recorded_state(body) == STATE_DARK

    @pytest.mark.parametrize("body", ["", "no marker here", "<!-- watchdog-state:  -->"])
    def test_missing_or_empty_marker_reads_as_unknown(self, body: str) -> None:
        assert watchdog._recorded_state(body) is None


class TestHealthProbe:
    def test_cloudflare_tunnel_error_is_not_reached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise urllib.error.HTTPError("u", 530, "origin down", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
        probe = watchdog.probe_health("https://example.invalid/healthz")
        assert not probe.reached_origin
        assert probe.status == 530

    def test_client_error_still_counts_as_reached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise urllib.error.HTTPError("u", 404, "nope", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
        assert watchdog.probe_health("https://example.invalid/healthz").reached_origin

    def test_transport_failure_reports_no_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise TimeoutError("timed out")

        monkeypatch.setattr(watchdog.urllib.request, "urlopen", boom)
        probe = watchdog.probe_health("https://example.invalid/healthz")
        assert probe.status is None
        assert probe.error == "TimeoutError"


class TestBackupProbe:
    def test_github_failure_yields_unknown_not_stale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one reading that could invent an outage — never take it."""

        def boom(*_args: object, **_kwargs: object) -> object:
            raise OSError("github is having a day")

        monkeypatch.setattr(watchdog, "_github_request", boom)
        signal = watchdog.probe_backup("owner/repo", None, NOW)
        assert signal.age_hours is None
        assert not signal.stale

    def test_missing_access_names_the_fix_instead_of_looking_like_an_outage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GitHub hides private repos as 404. That is our misconfiguration, not their fault.

        The live first run hit exactly this: the built-in GITHUB_TOKEN cannot
        read the private backups repo, so the second signal was permanently
        unavailable. An alert that says "GitHub error" sends somebody to
        status.github.com; one that says "no read access, set the secret" gets
        the watchdog its second eye back.
        """

        def denied(*_args: object, **_kwargs: object) -> object:
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(watchdog, "_github_request", denied)
        signal = watchdog.probe_backup("owner/private-repo", None, NOW)
        assert signal.age_hours is None
        assert not signal.stale
        assert "no read access" in (signal.error or "")
        assert "WATCHDOG_BACKUP_TOKEN" in (signal.error or "")

    def test_other_http_errors_stay_generic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*_args: object, **_kwargs: object) -> object:
            raise urllib.error.HTTPError("u", 500, "oops", {}, None)  # type: ignore[arg-type]

        monkeypatch.setattr(watchdog, "_github_request", boom)
        signal = watchdog.probe_backup("owner/repo", None, NOW)
        assert signal.error == "HTTP 500"
        assert not signal.stale

    def test_age_is_measured_from_the_commit_timestamp(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            watchdog,
            "_github_request",
            lambda *_a, **_k: [
                {"commit": {"committer": {"date": "2026-08-24T02:16:47Z"}}}
            ],
        )
        signal = watchdog.probe_backup("owner/repo", None, NOW)
        assert signal.age_hours == pytest.approx(28.23, abs=0.01)
        assert signal.stale
