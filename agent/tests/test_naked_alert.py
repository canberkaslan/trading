"""When the system pages about a book that has lost its stops.

Written after a coverage run on the live paper book returned 75.5% naked — a
number `risk.stop_coverage` could always compute and nothing ever asked for.
"""

from __future__ import annotations

from tradingagents_us.notifications.naked_alert import (
    CoverageFacts,
    NakedAlertState,
    days_between,
    decide,
    recovery_expired,
)


def facts(naked: float, total: float = 100.0, **kw) -> CoverageFacts:
    indet = kw.pop("indet", 0.0)
    return CoverageFacts(total_qty=total, naked_qty=naked, indeterminate_qty=indet, **kw)


class TestSilence:
    def test_an_empty_book_never_pages(self) -> None:
        # Holding nothing is not an exposure. Reporting it as 100% naked would
        # page someone about a flat account.
        assert decide(CoverageFacts(0.0, 0.0, 0.0), NakedAlertState()) is None

    def test_a_deliberate_flatten_never_pages(self) -> None:
        # FLATTEN_ALL is the book being closed on purpose; its stops going away
        # is the intended outcome of that, not a fault.
        assert decide(facts(90.0), NakedAlertState(), kill_switch="FLATTEN_ALL") is None

    def test_a_covered_book_stays_quiet(self) -> None:
        assert decide(facts(2.0), NakedAlertState()) is None

    def test_a_few_shares_between_fill_and_bracket_are_not_news(self) -> None:
        # A partial fill leaves shares briefly uncovered while the bracket arms.
        assert decide(facts(9.0), NakedAlertState()) is None

    def test_indeterminate_alone_does_not_page(self) -> None:
        # An order in an unrecognised status is not evidence of naked exposure,
        # and paging on it would be paging on a guess.
        assert decide(facts(0.0, indet=80.0), NakedAlertState()) is None

    def test_a_lot_our_time_exit_is_selling_does_not_page(self) -> None:
        # 2026-10-05: GOOGL's 32 shares, stops released, our exit queued for
        # the open. Not an exposure, so neither a page nor, the night after,
        # a recovery from one.
        exiting = facts(0.0, 278.0, exiting_qty=32.0, exiting_symbols=("GOOGL",))
        assert decide(exiting, NakedAlertState()) is None
        assert decide(facts(0.0, 246.0), NakedAlertState()) is None


class TestFirstAlert:
    def test_pages_when_the_book_crosses_the_threshold(self) -> None:
        alert = decide(facts(76.0, 100.0, naked_symbols=("AAPL", "META")), NakedAlertState())
        assert alert is not None
        assert alert.kind == "naked"
        assert "76%" in alert.title
        assert "AAPL" in alert.body

    def test_names_the_symbols_but_does_not_list_forty(self) -> None:
        alert = decide(
            facts(76.0, naked_symbols=tuple(f"T{i}" for i in range(20))), NakedAlertState()
        )
        assert alert is not None
        assert "+14 more" in alert.body

    def test_carries_the_indeterminate_caveat_when_there_is_one(self) -> None:
        # The number has a soft edge and the reader is told so, rather than the
        # caveat being dropped because it did not itself trigger the page.
        alert = decide(facts(50.0, indet=20.0), NakedAlertState())
        assert alert is not None
        assert "indeterminate" in alert.body

    def test_omits_the_caveat_when_the_number_is_exact(self) -> None:
        alert = decide(facts(50.0), NakedAlertState())
        assert alert is not None
        assert "indeterminate" not in alert.body
        assert "exiting" not in alert.body

    def test_names_shares_on_their_way_out_apart_from_the_naked_ones(self) -> None:
        alert = decide(
            facts(50.0, exiting_qty=20.0, exiting_symbols=("GOOGL",)), NakedAlertState()
        )
        assert alert is not None
        assert "20 shares are exiting" in alert.body


class TestRepeatSuppression:
    def test_the_same_exposure_tomorrow_is_not_new_information(self) -> None:
        # Paging daily about one unchanged problem trains the reader to swipe
        # the alert away, which is how the next real one gets missed.
        first = decide(facts(76.0), NakedAlertState())
        assert first is not None
        assert decide(facts(76.0), first.next_state) is None

    def test_a_small_worsening_is_still_the_same_problem(self) -> None:
        first = decide(facts(50.0), NakedAlertState())
        assert first is not None
        assert decide(facts(60.0), first.next_state) is None

    def test_a_material_worsening_pages_again(self) -> None:
        # Without this a book drifting from 12% to 80% naked would page once
        # and go quiet for the part that actually matters.
        first = decide(facts(20.0), NakedAlertState())
        assert first is not None
        second = decide(facts(85.0), first.next_state)
        assert second is not None
        assert "85%" in second.title


class TestRecovery:
    def test_reports_recovery_once(self) -> None:
        first = decide(facts(76.0), NakedAlertState())
        assert first is not None
        rec = decide(facts(1.0), first.next_state)
        assert rec is not None
        assert rec.kind == "recovered"
        assert decide(facts(1.0), rec.next_state) is None

    def test_never_reports_a_recovery_that_was_never_a_problem(self) -> None:
        assert decide(facts(1.0), NakedAlertState()) is None

    def test_a_recovery_with_a_lot_on_its_way_out_does_not_call_it_protected(self) -> None:
        first = decide(facts(40.0), NakedAlertState())
        assert first is not None
        rec = decide(
            facts(0.0, exiting_qty=32.0, exiting_symbols=("GOOGL",)), first.next_state
        )
        assert rec is not None
        assert "covered again" in rec.body
        assert "32 shares are exiting at the next open under our time exit: GOOGL" in rec.body

    def test_the_all_clear_names_the_exposure_it_clears(self) -> None:
        # It is retried until a channel takes it, so it can go out runs after
        # the exposure. Undated, a late one reads as news about tonight's book:
        # the live journal said "Stop coverage restored" for three nights about
        # one naked night.
        first = decide(facts(11.5, run_date="2026-10-06"), NakedAlertState())
        assert first is not None
        rec = decide(facts(0.0, run_date="2026-10-09"), first.next_state)
        assert rec is not None
        assert "Last seen naked on 2026-10-06 (12% of the book, 3 days ago)." in rec.body

    def test_a_same_day_all_clear_carries_no_age(self) -> None:
        state = NakedAlertState(last_kind="naked", last_naked_pct=40.0, last_run_date="2026-10-06")
        rec = decide(facts(0.0, run_date="2026-10-06"), state)
        assert rec is not None
        assert "Last seen naked on 2026-10-06 (40% of the book)." in rec.body

    def test_an_undated_exposure_still_gets_its_all_clear(self) -> None:
        # State written before run dates were recorded: no date to name, and
        # that is no reason to withhold the all-clear.
        rec = decide(facts(0.0, run_date="2026-10-09"), NakedAlertState(last_kind="naked"))
        assert rec is not None
        assert "Last seen" not in rec.body

    def test_a_relapse_after_recovery_pages_again(self) -> None:
        first = decide(facts(76.0), NakedAlertState())
        rec = decide(facts(1.0), first.next_state)  # type: ignore[union-attr]
        again = decide(facts(76.0), rec.next_state)  # type: ignore[union-attr]
        assert again is not None
        assert again.kind == "naked"


class TestRecoveryExpiry:
    naked = NakedAlertState(last_kind="naked", last_naked_pct=11.5, last_run_date="2026-10-06")

    def test_retried_inside_the_window(self) -> None:
        assert not recovery_expired(self.naked, "2026-10-09")
        assert not recovery_expired(self.naked, "2026-10-12")

    def test_dropped_once_the_window_has_passed(self) -> None:
        assert recovery_expired(self.naked, "2026-10-13")
        assert recovery_expired(self.naked, "2026-11-30")

    def test_an_exposure_without_a_readable_date_never_expires(self) -> None:
        # No age to measure; dropping an all-clear on a guess is the failure
        # the retry exists to prevent.
        assert not recovery_expired(NakedAlertState(last_kind="naked"), "2026-11-30")
        assert not recovery_expired(self.naked, None)
        assert not recovery_expired(
            NakedAlertState(last_kind="naked", last_run_date="garbage"), "2026-11-30"
        )

    def test_only_a_naked_state_has_an_all_clear_to_expire(self) -> None:
        recovered = NakedAlertState(last_kind="recovered", last_run_date="2026-10-06")
        assert not recovery_expired(recovered, "2026-11-30")

    def test_days_between_counts_calendar_days(self) -> None:
        assert days_between("2026-10-06", "2026-10-09") == 3
        assert days_between("2026-10-06", "2026-10-06") == 0
        assert days_between(None, "2026-10-09") is None
        assert days_between("2026-10-06", "not-a-date") is None


class TestStateRoundTrip:
    def test_survives_serialisation(self) -> None:
        alert = decide(facts(76.0), NakedAlertState())
        assert alert is not None
        restored = NakedAlertState.from_dict(alert.next_state.as_dict())
        assert restored == alert.next_state
        # And the restored state still suppresses the repeat.
        assert decide(facts(76.0), restored) is None

    def test_tolerates_a_missing_or_corrupt_field(self) -> None:
        assert NakedAlertState.from_dict({}) == NakedAlertState()
        assert NakedAlertState.from_dict({"last_naked_pct": None}).last_naked_pct == 0.0
