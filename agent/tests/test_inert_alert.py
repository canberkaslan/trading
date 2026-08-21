"""Alert policy for a book that stopped reaching the broker.

The interesting assertions here are the silences: an alerter that pages every
day about the same three-week freeze gets muted, and a muted alerter is worse
than none — it costs the same attention and buys nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tradingagents_us.execution.actionability import ActionabilityReport
from tradingagents_us.notifications.inert_alert import (
    ESCALATION_STEP_RUN_DAYS,
    AlertState,
    decide,
)


def _report(
    *,
    orders: int = 110,
    submitted: int = 0,
    inert_run_days: int = 5,
    by_reason: dict[str, int] | None = None,
    last_submitted: datetime | None = None,
    last_order: datetime | None = datetime(2026, 8, 21, 0, 39, tzinfo=timezone.utc),
) -> ActionabilityReport:
    return ActionabilityReport(
        orders=orders,
        submitted=submitted,
        refused=orders - submitted,
        by_reason=by_reason if by_reason is not None else {"non-actionable rating=Hold": 90},
        inert_run_days=inert_run_days,
        run_days=10,
        last_submitted_at_utc=last_submitted,
        first_order_at_utc=datetime(2026, 7, 22, tzinfo=timezone.utc),
        last_order_at_utc=last_order,
    )


# --------------------------------------------------------------------------
# the alert fires


def test_first_crossing_of_the_threshold_alerts():
    alert = decide(_report(inert_run_days=3), AlertState())
    assert alert is not None
    assert alert.kind == "inert"
    assert "3 run days" in alert.title
    assert alert.next_state.last_inert_run_days == 3
    assert alert.next_state.last_run_date == "2026-08-21"


def test_body_names_the_dominant_blocker_and_the_last_ack():
    alert = decide(
        _report(
            submitted=6,
            by_reason={"trimmed_to_zero_by_portfolio_caps": 93, "rating=Hold": 12},
            last_submitted=datetime(2026, 8, 11, 23, 30, tzinfo=timezone.utc),
        ),
        AlertState(),
    )
    assert alert is not None
    assert "6/110 orders submitted" in alert.body
    assert "trimmed_to_zero_by_portfolio_caps (93)" in alert.body
    assert "2026-08-11" in alert.body


def test_no_ack_in_the_window_says_never_rather_than_leaving_a_blank():
    # A window where nothing ever reached the broker is a worse story than one
    # whose last ack merely aged out; the body must not render them the same.
    alert = decide(_report(last_submitted=None), AlertState())
    assert alert is not None
    assert "last broker ack: never" in alert.body


# --------------------------------------------------------------------------
# the alert stays quiet


def test_below_threshold_is_not_an_alert():
    # Two quiet days is an all-Hold tape, not a frozen book.
    assert decide(_report(inert_run_days=2), AlertState()) is None


def test_idle_window_never_alerts():
    # No order rows at all = the run did not happen. That is the cron's
    # failure, owned by daily_run's exit code and the dead-man's switch;
    # reporting it here would blame the strategy for a scheduling outage.
    empty = ActionabilityReport(orders=0, submitted=0, refused=0)
    assert decide(empty, AlertState()) is None


def test_same_run_day_does_not_alert_twice():
    first = decide(_report(inert_run_days=3), AlertState())
    assert first is not None
    assert decide(_report(inert_run_days=3), first.next_state) is None


def test_one_deeper_run_day_is_not_news():
    state = AlertState(
        last_kind="inert",
        last_run_date="2026-08-20",
        last_inert_run_days=5,
        last_dominant_reason="non-actionable rating=Hold",
    )
    assert decide(_report(inert_run_days=6), state) is None


def test_escalation_step_re_alerts():
    state = AlertState(
        last_kind="inert",
        last_run_date="2026-08-14",
        last_inert_run_days=3,
        last_dominant_reason="non-actionable rating=Hold",
    )
    alert = decide(_report(inert_run_days=3 + ESCALATION_STEP_RUN_DAYS), state)
    assert alert is not None
    assert alert.kind == "inert"


def test_a_new_dominant_blocker_re_alerts_at_the_same_depth():
    # Same depth, different cause: the book went from "no conviction" to
    # "conviction that cannot be sized", which is a different bug to chase.
    state = AlertState(
        last_kind="inert",
        last_run_date="2026-08-20",
        last_inert_run_days=6,
        last_dominant_reason="non-actionable rating=Hold",
    )
    alert = decide(_report(inert_run_days=6, by_reason={"trimmed_to_zero_by_cash_cap": 40}), state)
    assert alert is not None
    assert alert.next_state.last_dominant_reason == "trimmed_to_zero_by_cash_cap"


def test_window_sliding_the_count_down_does_not_re_alert():
    # inert_run_days can shrink as the window moves without the book thawing.
    state = AlertState(
        last_kind="inert",
        last_run_date="2026-08-20",
        last_inert_run_days=9,
        last_dominant_reason="non-actionable rating=Hold",
    )
    assert decide(_report(inert_run_days=4), state) is None


# --------------------------------------------------------------------------
# recovery


def test_recovery_alerts_once_after_a_reported_freeze():
    state = AlertState(
        last_kind="inert",
        last_run_date="2026-08-20",
        last_inert_run_days=9,
        last_dominant_reason="non-actionable rating=Hold",
    )
    alert = decide(_report(submitted=4, inert_run_days=0), state)
    assert alert is not None
    assert alert.kind == "recovered"
    assert alert.next_state.last_kind == "recovered"
    # ...and only once.
    assert decide(_report(submitted=4, inert_run_days=0), alert.next_state) is None


def test_no_recovery_alert_when_the_freeze_was_never_reported():
    # Nobody was told it broke, so nobody needs telling it works.
    assert decide(_report(submitted=4, inert_run_days=0), AlertState()) is None


def test_a_second_freeze_after_recovery_alerts_again():
    recovered = AlertState(last_kind="recovered", last_run_date="2026-08-20")
    alert = decide(_report(inert_run_days=3), recovered)
    assert alert is not None
    assert alert.kind == "inert"


# --------------------------------------------------------------------------
# state persistence


def test_corrupt_state_reads_as_never_alerted():
    # One extra push beats silence about a frozen book.
    assert AlertState.from_dict("not a dict") == AlertState()
    assert AlertState.from_dict({"last_kind": 7, "last_inert_run_days": -3}) == AlertState()


def test_state_round_trips_through_json():
    state = AlertState(
        last_kind="inert",
        last_run_date="2026-08-21",
        last_inert_run_days=9,
        last_dominant_reason="non-actionable rating=Hold",
    )
    assert AlertState.from_dict(json.loads(json.dumps(state.as_dict()))) == state


def test_state_file_is_written_atomically(tmp_path, monkeypatch):
    from scripts import inert_alert as cli

    monkeypatch.setenv("INERT_ALERT_STATE_PATH", str(tmp_path / "s.json"))
    path = cli.state_path()
    assert cli.load_state(path) == AlertState()  # missing file, not an error

    state = AlertState(last_kind="inert", last_run_date="2026-08-21", last_inert_run_days=3)
    cli.save_state(path, state)
    assert cli.load_state(path) == state
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_does_not_persist_state_when_the_push_was_not_delivered(tmp_path, monkeypatch):
    # The one bug that would turn this script into silence: marking a freeze
    # "already reported" when no device ever got it.
    from scripts import inert_alert as cli

    monkeypatch.setenv("INERT_ALERT_STATE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cli, "_report", lambda days: _report(inert_run_days=3))
    monkeypatch.setattr(cli, "_send", lambda *a, **k: (False, "no registered devices"))

    assert cli.main([]) == 0
    assert cli.load_state(cli.state_path()) == AlertState()


def test_cli_persists_state_after_a_delivered_push(tmp_path, monkeypatch):
    from scripts import inert_alert as cli

    monkeypatch.setenv("INERT_ALERT_STATE_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cli, "_report", lambda days: _report(inert_run_days=3))
    monkeypatch.setattr(cli, "_send", lambda *a, **k: (True, "sent to 1 device(s)"))

    assert cli.main([]) == 0
    assert cli.load_state(cli.state_path()).last_kind == "inert"


def test_cli_exits_zero_when_the_report_blows_up(monkeypatch):
    # Appended to daily_run.sh: a broken alerter must never fail the run whose
    # own exit code is the signal that matters.
    from scripts import inert_alert as cli

    def boom(days):
        raise RuntimeError("db gone")

    monkeypatch.setattr(cli, "_report", boom)
    assert cli.main([]) == 0


@pytest.mark.parametrize("verdict_days,expected", [(0, "active"), (3, "inert")])
def test_threshold_is_shared_with_the_api(verdict_days, expected):
    from tradingagents_us.execution.actionability import INERT_THRESHOLD_RUN_DAYS

    assert INERT_THRESHOLD_RUN_DAYS == 3
    assert _report(inert_run_days=verdict_days).verdict() == expected
