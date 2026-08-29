"""The reporting half of the off-box watchdog.

The policy decides *what* is wrong; this decides how often a human hears about
it. The watchdog wakes every 30 minutes, so the assertions that matter are the
ones about staying quiet: a dark host that commented on each run would post 48
times a day about a single outage, and by day two nobody reads it.
"""

from __future__ import annotations

import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from scripts import watchdog
from tradingagents_us.monitoring.incident_clock import humanize_duration, render_timeline
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


def _issue(
    state: str,
    number: int = 7,
    *,
    first_seen: datetime | None = None,
    checked_at: datetime | None = None,
    created_at: str | None = None,
) -> dict[str, object]:
    markers = [watchdog._state_marker(state)]
    if first_seen is not None:
        markers.append(watchdog._marker(watchdog._FIRST_SEEN_MARKER, first_seen.isoformat()))
    if checked_at is not None:
        markers.append(watchdog._marker(watchdog._CHECKED_AT_MARKER, checked_at.isoformat()))
    issue: dict[str, object] = {
        "number": number,
        "body": "\n".join(markers) + "\n\nsomething happened",
    }
    if created_at is not None:
        issue["created_at"] = created_at
    return issue


class TestIncidentLifecycle:
    def test_first_sighting_opens_one_issue(self, gh: FakeGitHub) -> None:
        result = watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        assert "opened #7" in result
        posts = [c for c in gh.writes if c[0] == "POST"]
        assert len(posts) == 1
        assert posts[0][2]["labels"] == [watchdog.ISSUE_LABEL]

    def test_same_state_on_the_next_run_refreshes_the_clock_but_says_nothing(
        self, gh: FakeGitHub
    ) -> None:
        """No comment — but the body must still record that a check happened.

        The whole failure this replaces: four days of correct silence left issue
        #1 byte-identical to the hour it was opened, so 'nothing new' and 'the
        watchdog died too' looked exactly alike.
        """
        gh.open_issue = _issue(STATE_DARK)
        result = watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        assert "staying quiet" in result

        methods = [c[0] for c in gh.writes]
        assert methods == ["PATCH"], "a quiet run must not comment"
        payload = gh.writes[0][2]
        assert set(payload) == {"body"}, "quiet runs must not retitle either"
        assert "Last checked 2026-08-25 06:30 UTC" in str(payload["body"])

    def test_de_escalation_also_stays_quiet(self, gh: FakeGitHub) -> None:
        """Dark -> edge_down is still an open outage, not news worth a second ping."""
        gh.open_issue = _issue(STATE_DARK)
        result = watchdog.report(_verdict(STATE_EDGE_DOWN), REPO, "t", NOW)
        assert "staying quiet" in result
        assert [c[0] for c in gh.writes] == ["PATCH"]
        assert "comments" not in gh.writes[0][1]

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


class TestIncidentClock:
    """The issue body has to say how old it is, or its silence means nothing."""

    def test_a_new_incident_stamps_both_instants(self, gh: FakeGitHub) -> None:
        watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        body = str(gh.writes[0][2]["body"])
        assert watchdog._parse_stamp(
            watchdog._read_marker(body, watchdog._FIRST_SEEN_MARKER)
        ) == NOW
        assert watchdog._parse_stamp(
            watchdog._read_marker(body, watchdog._CHECKED_AT_MARKER)
        ) == NOW

    def test_escalation_keeps_the_original_start_time(self, gh: FakeGitHub) -> None:
        """An outage that gets worse is the same outage — its age must not reset.

        The previous body wrote `_First seen: {now}_` on every escalation, so a
        four-day dark host that escalated on day four read as four minutes old.
        """
        started = datetime(2026, 8, 25, 7, 24, tzinfo=UTC)
        gh.open_issue = _issue(STATE_DEGRADED, first_seen=started)
        watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)

        body = str(gh.writes[0][2]["body"])
        assert (
            watchdog._parse_stamp(watchdog._read_marker(body, watchdog._FIRST_SEEN_MARKER))
            == started
        )
        assert "First seen 2026-08-25 07:24 UTC" in body

    def test_an_issue_predating_the_marker_falls_back_to_created_at(
        self, gh: FakeGitHub
    ) -> None:
        """Issue #1 has no marker but is four days old; `now` would erase that."""
        gh.open_issue = _issue(STATE_EDGE_DOWN, created_at="2026-08-25T07:24:46Z")
        watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        assert "First seen 2026-08-25 07:24 UTC" in str(gh.writes[0][2]["body"])

    def test_a_late_run_is_reported_as_late(self, gh: FakeGitHub) -> None:
        """The cron says every 30 min; the measured median is 55. Say which happened."""
        gh.open_issue = _issue(
            STATE_DARK, checked_at=NOW - timedelta(minutes=67), first_seen=NOW
        )
        watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        body = str(gh.writes[0][2]["body"])
        assert "previous check was 67 min earlier" in body
        assert "older than the schedule suggests" in body

    def test_an_on_time_run_does_not_cry_wolf(self, gh: FakeGitHub) -> None:
        gh.open_issue = _issue(
            STATE_DARK, checked_at=NOW - timedelta(minutes=31), first_seen=NOW
        )
        watchdog.report(_verdict(STATE_DARK), REPO, "t", NOW)
        body = str(gh.writes[0][2]["body"])
        assert "previous check 31 min earlier" in body
        assert "older than the schedule" not in body

    def test_recovery_says_how_long_it_was_down(self, gh: FakeGitHub) -> None:
        gh.open_issue = _issue(STATE_DARK, first_seen=NOW - timedelta(hours=101))
        watchdog.report(_verdict("up"), REPO, "t", NOW)
        assert "after 4d 5h down" in str(gh.writes[0][2]["body"])

    @pytest.mark.parametrize("raw", [None, "", "not a date", "2026-13-45"])
    def test_a_mangled_stamp_never_raises(self, raw: str | None) -> None:
        """A hand-edited body costs a line of prose, not the outage report."""
        assert watchdog._parse_stamp(raw) is None

    def test_a_naive_stamp_is_read_as_utc(self) -> None:
        assert watchdog._parse_stamp("2026-08-25T07:24:46") == datetime(
            2026, 8, 25, 7, 24, 46, tzinfo=UTC
        )


class TestTimelineText:
    def test_no_previous_check_admits_it_rather_than_inventing_a_gap(self) -> None:
        text = render_timeline(NOW, first_seen=NOW, previous_check=None)
        assert "no measured cadence yet" in text

    def test_an_undated_incident_is_not_restamped_as_new(self) -> None:
        text = render_timeline(NOW, first_seen=None, previous_check=None)
        assert "not recorded" in text
        assert "open 0s" not in text

    def test_the_footer_explains_what_a_frozen_clock_means(self) -> None:
        """The line that turns a stale page into a signal instead of a shrug."""
        text = render_timeline(NOW, first_seen=NOW, previous_check=NOW)
        assert "If it stops advancing, the watchdog has stopped" in text

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (48, "48s"),
            (60 * 67, "67 min"),
            (3600 * 4.5, "4.5h"),
            (3600 * 101, "4d 5h"),
            (-90, "0s"),
        ],
    )
    def test_durations_read_the_way_a_human_would_say_them(
        self, seconds: float, expected: str
    ) -> None:
        assert humanize_duration(seconds) == expected


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


class TestHostProbe:
    """The credential-free signal: does the origin's kernel answer a socket.

    Everything else the watchdog reads can be revoked, rate-limited or 404'd.
    This one needs an address and nothing more, which is why it is the tiebreak
    when the others go quiet.
    """

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("203.0.113.10", [("203.0.113.10", 22)]),
            ("203.0.113.10:2222", [("203.0.113.10", 2222)]),
            (" 203.0.113.10:22 , 203.0.113.10:443 ", [("203.0.113.10", 22), ("203.0.113.10", 443)]),
            ("box.example:notaport", [("box.example:notaport", 22)]),
            ("", []),
        ],
    )
    def test_parses_targets(self, spec: str, expected: list[tuple[str, int]]) -> None:
        assert watchdog._parse_targets(spec) == expected

    def test_unset_means_absent_not_unreachable(self) -> None:
        """No address configured is a signal that was never taken — not a bad one."""
        probe = watchdog.probe_host(None)
        assert not probe.configured
        assert not probe.answered

    def test_accepted_connection_proves_the_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Sock:
            def __enter__(self) -> Sock:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

        monkeypatch.setattr(watchdog.socket, "create_connection", lambda *a, **k: Sock())
        probe = watchdog.probe_host("203.0.113.10:22")
        assert probe.answered

    def test_refused_connection_also_proves_the_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*_a: object, **_k: object) -> object:
            raise ConnectionRefusedError

        monkeypatch.setattr(watchdog.socket, "create_connection", refuse)
        probe = watchdog.probe_host("203.0.113.10:22")
        assert probe.answered, "an RST is the kernel answering — that is the whole question"

    def test_timeout_is_silence_not_proof_of_death(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def drop(*_a: object, **_k: object) -> object:
            raise TimeoutError

        monkeypatch.setattr(watchdog.socket, "create_connection", drop)
        probe = watchdog.probe_host("203.0.113.10:22")
        assert probe.configured and not probe.answered

    def test_tries_every_target_before_concluding_silence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One firewalled port must not be able to declare the machine gone."""
        seen: list[tuple[str, int]] = []

        def maybe(addr: tuple[str, int], **_k: object) -> object:
            seen.append(addr)
            if addr[1] == 22:
                raise TimeoutError
            raise ConnectionRefusedError

        monkeypatch.setattr(watchdog.socket, "create_connection", maybe)
        probe = watchdog.probe_host("203.0.113.10:22,203.0.113.10:443")
        assert probe.answered
        assert seen == [("203.0.113.10", 22), ("203.0.113.10", 443)]

    def test_reports_no_address_and_no_exception_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The incident issues are public; the address behind Cloudflare is not.

        Exception strings are the usual way an address escapes into a log — a
        `gaierror` carries the hostname — so the probe reports fixed categories
        and never the error it caught.
        """

        def leak(*_a: object, **_k: object) -> object:
            raise OSError("no route to host 203.0.113.10:22")

        monkeypatch.setattr(watchdog.socket, "create_connection", leak)
        probe = watchdog.probe_host("203.0.113.10:22")
        assert "203.0.113.10" not in probe.describe()
        assert "203.0.113.10" not in repr(probe)
        assert "no route" not in (probe.detail or "")


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
