"""Preflight: a broken dependency fails it; a missing alert path is recorded, not failed on.

Preflight found the refused broker key every evening at 21:45 for nearly two
weeks and had nowhere to say so but a push to an app being rebuilt. Two gaps
made that possible: no HEALTHCHECK_URL, so no dead-man's switch, and no alert
channel other than the app.

Those gaps are not dependency failures. Counted as failures, preflight on a box
without the alert accounts is red every weekday, OnFailure pushes twice a day,
and a real key failure differs only in the count in the title. So the exit code
keeps meaning "a key or dependency is broken", and the gaps are recorded for
/readyz, where the off-box watchdog reads them (see test_alerting_wiring).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from scripts import preflight
from tests.github_fake import FakeGitHub
from tradingagents_us.monitoring.alerting_state import read_preflight

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
def state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "preflight.state.json"
    monkeypatch.setenv("PREFLIGHT_STATE_PATH", str(path))
    return path


@pytest.fixture
def other_checks_pass(monkeypatch: pytest.MonkeyPatch, state_file: Path) -> None:
    """Every dependency check reports healthy, with no network."""
    for name in _HARD_CHECKS:
        monkeypatch.setattr(preflight, name, lambda failures: None)
    monkeypatch.setattr(preflight, "_check_fred", lambda: None)


def _anthropic_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        preflight,
        "_check_anthropic",
        lambda failures: failures.append(("anthropic", "key rejected (HTTP 401)")),
    )


def _expiry_header(days: int) -> dict[str, str]:
    when = datetime.now(UTC) + timedelta(days=days)
    return {"github-authentication-token-expiration": when.strftime("%Y-%m-%d %H:%M:%S UTC")}


class TestAlertingGapsAreNotFailures:
    def test_a_box_with_no_alert_accounts_passes_and_sends_nothing(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        state_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The live box today: secrets.env predates both settings.
        monkeypatch.delenv("OPS_ALERT_GITHUB_TOKEN")

        assert preflight.main() == 0

        assert fake_github.requests == []
        err = capsys.readouterr().err
        assert "healthcheck: HEALTHCHECK_URL unset" in err
        assert "ops_alert_channel: OPS_ALERT_GITHUB_TOKEN unset" in err
        record = read_preflight(state_file)
        assert record is not None and record.ok
        assert record.alerting_gaps == ("healthcheck", "ops_alert_channel")

    def test_a_hard_failure_alerts_and_carries_the_gaps_with_it(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        state_file: Path,
    ) -> None:
        _anthropic_refused(monkeypatch)

        assert preflight.main() == 1

        assert len(fake_github.opened) == 1
        body = str(fake_github.opened[0]["body"])
        assert "anthropic: key rejected (HTTP 401)" in body
        assert "healthcheck: HEALTHCHECK_URL unset" in body
        assert "<!-- box-alert-kind:preflight -->" in body
        assert "Preflight FAILED (1)" in str(fake_github.opened[0]["title"])
        record = read_preflight(state_file)
        assert record is not None
        assert (record.failed, record.alerting_gaps) == (("anthropic",), ("healthcheck",))

    def test_a_malformed_url_is_a_gap_and_is_never_echoed(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        state_file: Path,
    ) -> None:
        monkeypatch.setenv("HEALTHCHECK_URL", "hc-ping.com/uuid-without-scheme")
        _anthropic_refused(monkeypatch)

        assert preflight.main() == 1

        assert "not an http(s) URL" in fake_github.posted_text
        assert "uuid-without-scheme" not in fake_github.posted_text
        assert "uuid-without-scheme" not in state_file.read_text()

    def test_fully_configured_stays_quiet(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        state_file: Path,
    ) -> None:
        monkeypatch.setenv("HEALTHCHECK_URL", HC)

        assert preflight.main() == 0

        # The token was exercised with a read, and nothing was sent.
        assert [(m, p) for m, p, _ in fake_github.requests] == [
            ("GET", "/repos/canberkaslan/trading/actions/workflows/box-alert.yml")
        ]
        assert fake_github.dispatches == []
        record = read_preflight(state_file)
        assert record is not None and record.ok and record.alerting_gaps == ()


class TestTheTokenIsExercisedNotJustPresent:
    """A fine-grained PAT expires after 30 days by default; present is not working."""

    @pytest.mark.parametrize(
        ("status", "words"),
        [(401, "rejected (expired or revoked?)"), (403, "lacks permission"), (404, "cannot see")],
    )
    def test_a_refused_token_is_a_named_gap(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        state_file: Path,
        capsys: pytest.CaptureFixture[str],
        status: int,
        words: str,
    ) -> None:
        monkeypatch.setenv("HEALTHCHECK_URL", HC)
        fake_github.workflow_status = status

        assert preflight.main() == 0

        err = capsys.readouterr().err
        assert f"ops_alert_channel: OPS_ALERT_GITHUB_TOKEN {words}: HTTP {status}" in err
        record = read_preflight(state_file)
        assert record is not None and record.alerting_gaps == ("ops_alert_channel",)

    def test_a_refusal_names_the_expiry_when_github_sends_it(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HEALTHCHECK_URL", HC)
        fake_github.workflow_status = 401
        fake_github.workflow_headers = _expiry_header(-2)

        preflight.main()

        expired = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
        assert f"token expires {expired}" in capsys.readouterr().err

    def test_a_token_about_to_expire_is_a_gap_while_it_still_works(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        state_file: Path,
    ) -> None:
        monkeypatch.setenv("HEALTHCHECK_URL", HC)
        fake_github.workflow_headers = _expiry_header(3)

        assert preflight.main() == 0

        record = read_preflight(state_file)
        assert record is not None and record.alerting_gaps == ("ops_alert_channel",)

    def test_a_token_with_time_left_is_fine(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        state_file: Path,
    ) -> None:
        monkeypatch.setenv("HEALTHCHECK_URL", HC)
        fake_github.workflow_headers = _expiry_header(30)

        preflight.main()

        record = read_preflight(state_file)
        assert record is not None and record.alerting_gaps == ()

    def test_github_unreachable_is_not_a_verdict_on_the_token(
        self,
        other_checks_pass: None,
        fake_github: FakeGitHub,
        monkeypatch: pytest.MonkeyPatch,
        state_file: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("HEALTHCHECK_URL", HC)
        fake_github.error = httpx.ConnectError("down")

        assert preflight.main() == 0

        assert "not checked" in capsys.readouterr().err
        record = read_preflight(state_file)
        assert record is not None and record.alerting_gaps == ()


def test_a_record_that_cannot_be_written_never_fails_preflight(
    other_checks_pass: None,
    fake_github: FakeGitHub,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HEALTHCHECK_URL", HC)
    monkeypatch.setenv("PREFLIGHT_STATE_PATH", str(tmp_path / "no-such-dir" / "state.json"))

    assert preflight.main() == 0
    assert "could not record the result" in capsys.readouterr().err
