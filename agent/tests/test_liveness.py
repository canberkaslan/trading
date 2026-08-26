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
    STATE_DARK,
    STATE_DEGRADED,
    STATE_EDGE_DOWN,
    STATE_UP,
    STATE_WEDGED,
    BackupSignal,
    HealthProbe,
    HostProbe,
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
