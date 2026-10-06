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

import io
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

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
    monkeypatch.setattr(preflight, "_check_stocktwits", lambda: "ok")


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
            ("GET", "/repos/fusapp/trading/actions/workflows/box-alert.yml")
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


class TestDbWritable:
    """A SELECT passes on a WAL database whose sidecars cannot be written; a write does not."""

    def _db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from sqlalchemy import text

        from tradingagents_us.storage import make_engine

        db = tmp_path / "local.db"
        url = f"sqlite:///{db}"
        engine = make_engine(url)
        conn = engine.connect()  # held open, as the API does: keeps -wal/-shm on disk
        conn.execute(text("CREATE TABLE t (x INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))
        conn.commit()
        monkeypatch.setenv("TRADE_LOG_DB_URL", url)
        return db, conn

    def test_a_writable_db_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _db, conn = self._db(tmp_path, monkeypatch)
        failures: list = []
        try:
            preflight._check_db(failures)
        finally:
            conn.close()
        assert failures == []

    def test_unwritable_sidecars_fail_the_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db, conn = self._db(tmp_path, monkeypatch)
        sidecars = [Path(f"{db}-wal"), Path(f"{db}-shm")]
        assert all(p.exists() for p in sidecars)  # the premise: WAL is on
        for p in sidecars:
            p.chmod(0o444)
        failures: list = []
        try:
            preflight._check_db(failures)
        finally:
            for p in sidecars:
                p.chmod(0o644)
            conn.close()
        assert failures, "a DB that cannot take the write lock must fail preflight"
        assert failures[0][0] == "db"


class TestStocktwitsIsReportedNotFailed:
    """The sentiment analyst ran without StockTwits from 2026-09-14 and nothing said so.

    The source answers a Cloudflare bot challenge to every request. Preflight now
    names that on stderr, as a standing outage rather than a blip, and never fails
    on it: there is no key to rotate.
    """

    def _answer(
        self, monkeypatch: pytest.MonkeyPatch, status: int, headers: dict[str, str] | None = None
    ) -> list[Request]:
        seen: list[Request] = []

        reply_headers = Message()
        for key, value in (headers or {}).items():
            reply_headers[key] = value

        def fake_urlopen(req: Request, timeout: float) -> io.BytesIO:
            seen.append(req)
            if status != 200:
                raise HTTPError(req.full_url, status, "Forbidden", reply_headers, None)
            return io.BytesIO(b'{"messages": []}')

        monkeypatch.setattr(preflight, "urlopen", fake_urlopen)
        return seen

    def test_a_bot_challenge_is_named_as_a_standing_outage(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._answer(monkeypatch, 403, {"cf-mitigated": "challenge"})
        msg = preflight._check_stocktwits()
        assert "Cloudflare bot challenge" in msg and "standing outage" in msg
        assert "Cloudflare bot challenge" in capsys.readouterr().err

    def test_a_plain_403_is_not_called_a_challenge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._answer(monkeypatch, 403)
        msg = preflight._check_stocktwits()
        assert msg == "StockTwits answered HTTP 403 (soft)"

    def test_probe_sends_the_fetchers_own_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tradingagents.dataflows.stocktwits import _UA

        seen = self._answer(monkeypatch, 200)
        assert preflight._check_stocktwits() == "ok"
        assert seen[0].full_url.endswith("/api/2/streams/symbol/SPY.json")
        assert seen[0].get_header("User-agent") == _UA

    def test_unreachable_is_soft(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(req: Request, timeout: float) -> io.BytesIO:
            raise URLError("no route")

        monkeypatch.setattr(preflight, "urlopen", boom)
        assert preflight._check_stocktwits().startswith("StockTwits unreachable (soft)")

    def test_a_dead_source_never_fails_preflight(
        self, other_checks_pass: None, fake_github: FakeGitHub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HEALTHCHECK_URL", HC)
        monkeypatch.setattr(
            preflight,
            "_check_stocktwits",
            lambda: "StockTwits blocked by a Cloudflare bot challenge (HTTP 403)",
        )
        assert preflight.main() == 0
        assert fake_github.opened == []
