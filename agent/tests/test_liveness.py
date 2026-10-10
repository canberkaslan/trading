"""Off-box liveness policy — telling a dead host apart from a dead tunnel.

The distinction is the whole product. "The site is down" is not actionable at
3am; "the host is gone, the console is the only way in" and "cloudflared died,
one ssh fixes it" send somebody to two different places. These tests pin the
truth table and, just as importantly, pin what the watchdog refuses to conclude
when it can only see half the picture.
"""

from __future__ import annotations

from tradingagents_us.monitoring.liveness import (
    BACKUP_STALE_AFTER_HOURS,
    PREFLIGHT_STALE_AFTER_HOURS,
    STATE_BROKER_DOWN,
    STATE_DARK,
    STATE_DEGRADED,
    STATE_EDGE_DOWN,
    STATE_PREFLIGHT_FAILED,
    STATE_UNALERTED,
    STATE_UNHEARD,
    STATE_UP,
    STATE_WEDGED,
    BackupSignal,
    HealthProbe,
    HostProbe,
    ReadinessProbe,
    classify,
    severity,
)

ORIGIN_IP = "203.0.113.10"  # stand-in for the real origin, which is a secret


def _fresh() -> BackupSignal:
    return BackupSignal(age_hours=3.0, newest_commit_utc="2026-08-25T02:16:47Z")


def _stale() -> BackupSignal:
    return BackupSignal(age_hours=30.0, newest_commit_utc="2026-08-24T02:16:47Z")


def _unknown() -> BackupSignal:
    return BackupSignal(age_hours=None, error="HTTPError: 403")


def _host_alive() -> HostProbe:
    return HostProbe(configured=True, answered=True, detail="connection accepted")


def _host_silent() -> HostProbe:
    return HostProbe(configured=True, answered=False, detail="timed out or filtered")


def _host_absent() -> HostProbe:
    return HostProbe(configured=False)


class TestTruthTable:
    def test_both_signals_healthy_is_up(self) -> None:
        verdict = classify(HealthProbe(reached_origin=True, status=200), _fresh())
        assert verdict.state == STATE_UP
        assert not verdict.is_incident

    def test_both_signals_silent_is_dark(self) -> None:
        """The 2026-08-24 outage: tunnel error *and* a missed backup push."""
        verdict = classify(HealthProbe(reached_origin=False, status=530), _stale())
        assert verdict.state == STATE_DARK
        assert "dark" in verdict.headline.lower()
        # The remedy has to send somebody to the console, not to ssh — ssh is
        # exactly what does not work when this fires.
        assert "console" in verdict.remedy.lower()

    def test_backup_landing_absolves_the_host(self) -> None:
        """A recent push proves outbound network and a live timer: blame the edge."""
        verdict = classify(HealthProbe(reached_origin=False, status=530), _fresh())
        assert verdict.state == STATE_EDGE_DOWN
        assert "cloudflared" in verdict.remedy

    def test_api_up_with_a_stopped_backup_is_degraded_not_an_outage(self) -> None:
        verdict = classify(HealthProbe(reached_origin=True, status=200), _stale())
        assert verdict.state == STATE_DEGRADED
        assert verdict.is_incident
        assert "backup" in verdict.remedy


class TestSignalReading:
    def test_cloudflare_5xx_is_not_the_origin_answering(self) -> None:
        """530/1033 is the edge saying it never reached us — 'no answer', not a reply."""
        probe = HealthProbe(reached_origin=False, status=530)
        assert "tunnel not reached" in probe.describe()
        assert classify(probe, _stale()).state == STATE_DARK

    def test_a_404_still_proves_the_app_is_alive(self) -> None:
        """Anything the origin formed itself means uvicorn is up, however unhappy."""
        verdict = classify(HealthProbe(reached_origin=True, status=404), _fresh())
        assert verdict.state == STATE_UP

    def test_transport_failure_names_the_error(self) -> None:
        probe = HealthProbe(reached_origin=False, error="timeout")
        assert "timeout" in probe.describe()

    def test_grace_window_absorbs_timer_jitter(self) -> None:
        """A backup 25h old is a late timer, not an outage — do not page for it."""
        just_late = BackupSignal(age_hours=BACKUP_STALE_AFTER_HOURS - 0.1)
        assert not just_late.stale
        assert classify(HealthProbe(reached_origin=True, status=200), just_late).state == STATE_UP

        overdue = BackupSignal(age_hours=BACKUP_STALE_AFTER_HOURS + 0.1)
        assert overdue.stale


class TestUnknownBackupSignal:
    def test_unknown_is_never_treated_as_stale(self) -> None:
        """A GitHub outage must not be allowed to invent a dead host."""
        signal = _unknown()
        assert not signal.stale
        assert not signal.known

    def test_api_up_and_backup_unknown_stays_up(self) -> None:
        verdict = classify(HealthProbe(reached_origin=True, status=200), _unknown())
        assert verdict.state == STATE_UP

    def test_api_down_and_backup_unknown_does_not_claim_dark(self) -> None:
        """Half the picture: report unreachable, but do not send anyone to the console."""
        verdict = classify(HealthProbe(reached_origin=False, status=530), _unknown())
        assert verdict.state == STATE_EDGE_DOWN
        assert "unavailable" in verdict.headline
        assert any("only one of the two primary signals" in r.lower() for r in verdict.reasons)
        assert "console" not in verdict.remedy.lower()


class TestDirectHostProbe:
    """The third signal, added because the first real incident sat blind for a day.

    From 2026-08-25 the backup repo could not be read at all, so the verdict was
    stuck at "cannot tell a dead tunnel from a dead host" while the host was in
    fact dark. A TCP connect needs no credential, so it is the one signal that
    cannot be misconfigured into silence — and unlike the other two it can prove
    the machine is up even when everything running on it is gone.
    """

    def test_a_live_host_settles_the_unreadable_backup_case(self) -> None:
        """The stuck state resolves into an instruction, without the missing token."""
        verdict = classify(HealthProbe(reached_origin=False, status=530), _unknown(), _host_alive())
        assert verdict.state == STATE_EDGE_DOWN
        assert "host is alive" in verdict.headline
        assert "cloudflared" in verdict.remedy
        assert "cannot" not in verdict.headline.lower()

    def test_silence_leans_dark_without_calling_it(self) -> None:
        """A dropping firewall and an unplugged machine are the same from outside."""
        verdict = classify(
            HealthProbe(reached_origin=False, status=530), _unknown(), _host_silent()
        )
        assert verdict.state == STATE_EDGE_DOWN, "silence alone must not manufacture an outage"
        assert any("leans toward a dead host" in r for r in verdict.reasons)
        assert "console" in verdict.remedy.lower()

    def test_answering_host_with_both_paths_dead_is_wedged_not_dark(self) -> None:
        """Kernel up, userspace gone: a shell fixes this, a console trip does not."""
        verdict = classify(HealthProbe(reached_origin=False, status=530), _stale(), _host_alive())
        assert verdict.state == STATE_WEDGED
        assert "ssh" in verdict.remedy.lower()
        assert "unattended" in verdict.body(), "the book is no less unattended than when dark"

    def test_silent_host_corroborates_dark(self) -> None:
        verdict = classify(HealthProbe(reached_origin=False, status=530), _stale(), _host_silent())
        assert verdict.state == STATE_DARK
        assert any("third path" in r for r in verdict.reasons)

    def test_probe_never_downgrades_a_confident_verdict(self) -> None:
        """A live host does not make a landed backup or a healthy API less true."""
        assert classify(HealthProbe(True, 200), _fresh(), _host_alive()).state == STATE_UP
        assert classify(HealthProbe(True, 200), _fresh(), _host_silent()).state == STATE_UP
        assert classify(HealthProbe(True, 200), _stale(), _host_silent()).state == STATE_DEGRADED

    def test_omitting_the_probe_reproduces_the_old_behaviour_exactly(self) -> None:
        """Deployments with no origin address configured must not change at all."""
        for health in (HealthProbe(True, 200), HealthProbe(False, 530)):
            for backup in (_fresh(), _stale(), _unknown()):
                without = classify(health, backup)
                with_absent = classify(health, backup, _host_absent())
                assert without.state == with_absent.state
                assert without.body() == with_absent.body()

    def test_unconfigured_case_names_both_ways_out(self) -> None:
        """Being blind twice in a row is a setup gap; the alert should say which."""
        verdict = classify(
            HealthProbe(reached_origin=False, status=530), _unknown(), _host_absent()
        )
        assert verdict.state == STATE_EDGE_DOWN
        body = verdict.body()
        assert "WATCHDOG_BACKUP_TOKEN" in body and "WATCHDOG_HOST" in body

    def test_a_refused_connection_counts_as_the_host_answering(self) -> None:
        """An RST is the kernel talking. The question is about the host, not the port."""
        refused = HostProbe(configured=True, answered=True, detail="connection refused")
        assert "answered" in refused.describe()
        assert classify(HealthProbe(False, 530), _unknown(), refused).state == STATE_EDGE_DOWN

    def test_absent_signal_reads_as_absent_not_negative(self) -> None:
        assert "not checked" in _host_absent().describe()


class TestOriginAddressStaysSecret:
    """The repo is public and so are the issues this fills in. The origin IP is not.

    Cloudflare's proxy is worth nothing if the address behind it is printed in an
    incident, so the address is never carried on the probe object at all — there
    is nothing for the incident text to accidentally interpolate.
    """

    def test_probe_object_holds_no_address(self) -> None:
        for probe in (_host_alive(), _host_silent(), _host_absent()):
            assert ORIGIN_IP not in repr(probe)
            assert ORIGIN_IP not in probe.describe()

    def test_no_verdict_body_can_carry_it(self) -> None:
        for health in (HealthProbe(True, 200), HealthProbe(False, 530)):
            for backup in (_fresh(), _stale(), _unknown()):
                for host in (_host_alive(), _host_silent(), _host_absent()):
                    assert ORIGIN_IP not in classify(health, backup, host).body()


class TestSeverityOrdering:
    def test_orders_worst_first(self) -> None:
        assert severity(STATE_DARK) > severity(STATE_WEDGED)
        assert severity(STATE_WEDGED) > severity(STATE_EDGE_DOWN)
        assert severity(STATE_EDGE_DOWN) > severity(STATE_DEGRADED)
        assert severity(STATE_DEGRADED) > severity(STATE_UP)

    def test_unrecognised_state_sorts_worst(self) -> None:
        """A state this module grew but the caller has not learned must not read as 'fine'."""
        assert severity("some-future-state") > severity(STATE_DARK)


class TestIncidentBody:
    def test_body_carries_signals_and_remedy(self) -> None:
        body = classify(HealthProbe(reached_origin=False, status=530), _stale()).body()
        assert "Health endpoint" in body
        assert "Last off-box backup" in body
        assert "Next step" in body

    def test_body_says_the_book_is_unattended(self) -> None:
        """The cost of a dark box is an open book nobody is watching — say it."""
        body = classify(HealthProbe(reached_origin=False, status=530), _stale()).body()
        assert "unattended" in body

    def test_body_leaks_no_account_state(self) -> None:
        """The repo is public: liveness facts only, never equity or positions.

        The watchdog is handed nothing from the broker by construction, so this
        guards the construction — if someone later threads account data in to
        make the alert richer, this fails first.
        """
        for probe in (
            HealthProbe(reached_origin=False, status=530),
            HealthProbe(reached_origin=True, status=200),
        ):
            for backup in (_fresh(), _stale(), _unknown()):
                body = classify(probe, backup).body().lower()
                for forbidden in ("equity", "$", "position", "alpaca", "account"):
                    assert forbidden not in body


class TestBrokerReadiness:
    """/healthz answered 200 for all eleven days the broker refused the box's key
    (2026-09-14 onward). Only /readyz's `alpaca` bit could have said so."""

    _ok = HealthProbe(reached_origin=True, status=200)

    def test_broker_refusing_the_key_is_an_incident(self) -> None:
        verdict = classify(self._ok, _fresh(), _host_absent(), ReadinessProbe(broker_ok=False))
        assert verdict.state == STATE_BROKER_DOWN
        assert verdict.is_incident
        assert "secrets.env" in verdict.remedy

    def test_broker_down_outranks_a_stale_backup(self) -> None:
        verdict = classify(self._ok, _stale(), _host_absent(), ReadinessProbe(broker_ok=False))
        assert verdict.state == STATE_BROKER_DOWN
        assert any("backup" in r for r in verdict.reasons)

    def test_unknown_readiness_changes_nothing(self) -> None:
        unknown = ReadinessProbe(broker_ok=None, detail="HTTP 404")
        assert classify(self._ok, _fresh(), None, unknown).state == STATE_UP
        assert classify(self._ok, _stale(), None, unknown).state == STATE_DEGRADED

    def test_readiness_cannot_speak_for_a_missing_origin(self) -> None:
        dead = HealthProbe(reached_origin=False, status=530)
        verdict = classify(dead, _stale(), _host_silent(), ReadinessProbe(broker_ok=False))
        assert verdict.state == STATE_DARK

    def test_severity_sits_between_wedged_and_edge_down(self) -> None:
        assert severity(STATE_EDGE_DOWN) < severity(STATE_BROKER_DOWN) < severity(STATE_WEDGED)


class TestTheBoxReportOnItsOwnAlerting:
    """What /readyz says about the box's alert paths and its last preflight.

    Every on-box alert of the 2026-09-14 outage went to a phone app being
    rebuilt. These are the facts the box can publish without using any of
    those paths, and the watchdog is the one reader that does not depend on
    them.
    """

    _ok = HealthProbe(reached_origin=True, status=200)

    def _ready(self, **kw: object) -> ReadinessProbe:
        return ReadinessProbe(broker_ok=True, **kw)  # type: ignore[arg-type]

    def test_a_missing_alert_path_is_an_incident_that_names_it(self) -> None:
        gaps = self._ready(alerting_gaps=("healthcheck", "ops_alert_channel"))
        verdict = classify(self._ok, _fresh(), None, gaps)
        assert verdict.state == STATE_UNALERTED
        assert verdict.is_incident
        assert any("healthcheck, ops_alert_channel" in r for r in verdict.reasons)
        assert "HEALTHCHECK_URL" in verdict.remedy
        assert "OPS_ALERT_GITHUB_TOKEN" in verdict.remedy

    def test_a_failed_preflight_is_an_incident_that_names_the_checks(self) -> None:
        verdict = classify(
            self._ok,
            _fresh(),
            None,
            self._ready(
                preflight_failed=("anthropic", "polygon"), preflight_at="2026-09-14T21:45:07"
            ),
        )
        assert verdict.state == STATE_PREFLIGHT_FAILED
        assert any("failed: anthropic, polygon" in r for r in verdict.reasons)
        assert "journalctl -u ai-trader-preflight" in verdict.remedy

    def test_a_preflight_that_stopped_recording_is_an_incident(self) -> None:
        verdict = classify(
            self._ok,
            _fresh(),
            None,
            self._ready(preflight_failed=(), preflight_age_hours=PREFLIGHT_STALE_AFTER_HOURS + 1),
        )
        assert verdict.state == STATE_PREFLIGHT_FAILED
        assert "stopped reporting" in verdict.headline

    def test_a_weekend_gap_is_not_stale(self) -> None:
        # Friday 21:45 to Monday 21:45.
        verdict = classify(
            self._ok, _fresh(), None, self._ready(preflight_failed=(), preflight_age_hours=72.5)
        )
        assert verdict.state == STATE_UP

    def test_a_passing_preflight_with_nothing_missing_is_up(self) -> None:
        verdict = classify(
            self._ok, _fresh(), None, self._ready(preflight_failed=(), alerting_gaps=())
        )
        assert verdict.state == STATE_UP

    def test_no_report_at_all_changes_nothing(self) -> None:
        # An older API, or no preflight since this shipped: unknown, not broken.
        assert classify(self._ok, _fresh(), None, self._ready()).state == STATE_UP
        assert classify(self._ok, _stale(), None, self._ready()).state == STATE_DEGRADED

    def test_the_worse_news_wins_and_the_rest_still_rides_along(self) -> None:
        both = self._ready(preflight_failed=("disk",), alerting_gaps=("healthcheck",))
        verdict = classify(self._ok, _stale(), None, both)
        assert verdict.state == STATE_PREFLIGHT_FAILED
        text = "\n".join(verdict.reasons)
        assert "failed: disk" in text and "healthcheck" in text and "backup" in text

        unalerted = classify(self._ok, _stale(), None, self._ready(alerting_gaps=("healthcheck",)))
        assert unalerted.state == STATE_UNALERTED
        assert any("backup" in r for r in unalerted.reasons)

    def test_a_refused_broker_still_outranks_them_and_keeps_them_visible(self) -> None:
        ready = ReadinessProbe(
            broker_ok=False, preflight_failed=("alpaca",), alerting_gaps=("healthcheck",)
        )
        verdict = classify(self._ok, _fresh(), None, ready)
        assert verdict.state == STATE_BROKER_DOWN
        text = "\n".join(verdict.reasons)
        assert "failed: alpaca" in text and "healthcheck" in text

    def test_severity_order(self) -> None:
        assert (
            severity(STATE_DEGRADED)
            < severity(STATE_UNALERTED)
            < severity(STATE_UNHEARD)
            < severity(STATE_EDGE_DOWN)
            < severity(STATE_PREFLIGHT_FAILED)
            < severity(STATE_BROKER_DOWN)
        )


class TestWhoTheBoxAlertsActuallyReach:
    """`unalerted` said "only the phone"; on 2026-10-10 there was no phone.

    Issue #91 sat open for five days reading "its own alerts reach only the
    phone" while every alert on the box logged `push: no registered devices`.
    /readyz now says whether a phone is registered, and the watchdog only
    claims the phone when the box says there is one.
    """

    _ok = HealthProbe(reached_origin=True, status=200)
    _both = ("healthcheck", "ops_alert_channel")

    def test_no_off_phone_path_and_no_phone_reaches_no_one(self) -> None:
        verdict = classify(
            self._ok, _fresh(), None,
            ReadinessProbe(broker_ok=True, alerting_gaps=self._both, push_devices=False),
        )
        assert verdict.state == STATE_UNHEARD
        assert verdict.is_incident
        assert verdict.headline == "Box reachable, but its own alerts reach no one"
        text = "\n".join(verdict.reasons)
        assert "only the phone" not in text and "only to the mobile app" not in text
        assert "no phone registered for push" in text
        assert "HEALTHCHECK_URL" in verdict.remedy and "notifications" in verdict.remedy

    def test_a_registered_phone_keeps_the_old_reading(self) -> None:
        verdict = classify(
            self._ok, _fresh(), None,
            ReadinessProbe(broker_ok=True, alerting_gaps=self._both, push_devices=True),
        )
        assert verdict.state == STATE_UNALERTED
        assert "reach only the phone" in verdict.headline

    def test_an_api_that_does_not_say_is_not_read_as_silence(self) -> None:
        # Older API without the field: no evidence either way, so no escalation.
        verdict = classify(
            self._ok, _fresh(), None, ReadinessProbe(broker_ok=True, alerting_gaps=self._both)
        )
        assert verdict.state == STATE_UNALERTED

    def test_one_off_phone_path_left_is_named_and_the_phone_is_not_claimed(self) -> None:
        verdict = classify(
            self._ok, _fresh(), None,
            ReadinessProbe(broker_ok=True, alerting_gaps=("healthcheck",), push_devices=False),
        )
        assert verdict.state == STATE_UNALERTED
        assert "phone" not in verdict.headline.replace("no phone is registered", "")
        assert any("reported only through ops_alert_channel" in r for r in verdict.reasons)

    def test_no_phone_is_not_an_incident_when_the_off_phone_paths_exist(self) -> None:
        # The phone is what the other two back up; with both set the box is heard.
        verdict = classify(
            self._ok, _fresh(), None, ReadinessProbe(broker_ok=True, push_devices=False)
        )
        assert verdict.state == STATE_UP
        assert any("no phone registered for push" in r for r in verdict.reasons)

    def test_a_worse_state_still_outranks_it_and_carries_the_line(self) -> None:
        ready = ReadinessProbe(
            broker_ok=False, alerting_gaps=self._both, push_devices=False
        )
        verdict = classify(self._ok, _fresh(), None, ready)
        assert verdict.state == STATE_BROKER_DOWN
        assert any("no phone registered for push" in r for r in verdict.reasons)

class TestRemediesNameTheLiveBox:
    """A remedy is an instruction, so it must point at a host that exists.

    Until 2026-10-03 every remedy still said `ssh agentmesh` and "the Hetzner
    console" (decommissioned 2026-09-08), and `watchdog.yml` probed the orphaned
    trader-stg copy while `watchdog-relay.yml` probed trader.fusapp.com — two
    halves of one alerting path watching two different machines.
    """

    DEAD = ("agentmesh", "Hetzner", "trader-stg")

    def _every_branch(self) -> list:
        ready_broker = ReadinessProbe(broker_ok=False)
        return [
            classify(HealthProbe(reached_origin=False, status=530), _stale(), _host_silent()),
            classify(HealthProbe(reached_origin=False, status=530), _stale(), _host_alive()),
            classify(HealthProbe(reached_origin=False, status=530), _fresh()),
            classify(HealthProbe(reached_origin=False, status=530), _unknown(), _host_alive()),
            classify(HealthProbe(reached_origin=False, status=530), _unknown(), _host_silent()),
            classify(HealthProbe(reached_origin=False, status=530), _unknown(), _host_absent()),
            classify(HealthProbe(reached_origin=True, status=200), _stale()),
            classify(HealthProbe(reached_origin=True, status=200), _fresh(), ready=ready_broker),
        ]

    def test_no_remedy_names_a_decommissioned_host(self) -> None:
        for verdict in self._every_branch():
            for dead in self.DEAD:
                assert dead not in verdict.body(), (verdict.state, dead)

    def test_recovery_curl_is_the_url_the_watchdog_probes(self) -> None:
        from tradingagents_us.monitoring.liveness import DEFAULT_HEALTH_URL

        bodies = " ".join(v.body() for v in self._every_branch())
        assert f"curl -sS {DEFAULT_HEALTH_URL}" in bodies

    def test_both_workflows_probe_the_same_box(self) -> None:
        import re
        from pathlib import Path

        from tradingagents_us.monitoring.liveness import DEFAULT_HEALTH_URL

        workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows"
        urls = {
            name: re.findall(r"WATCHDOG_HEALTH_URL:\s*(\S+)", (workflows / name).read_text())
            for name in ("watchdog.yml", "watchdog-relay.yml")
        }
        assert urls == {name: [DEFAULT_HEALTH_URL] for name in urls}
