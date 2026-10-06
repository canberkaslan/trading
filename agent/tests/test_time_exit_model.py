"""Model check: the time exit against a broker whose every answer may be ambiguous.

PR #52 made time exits execute: `execution.protected_close.close_with_protection`
releases the stop (and any bracket leg), confirms the release, sells the holding
at market under the time-exit stamp, and re-arms the stop when the sell does
not go through. `scripts/manage_positions.py` drives it from the daily run at
22:30 UTC, after the US close, so the sell queues for the next open. Its review
never converged, and what kept it open is one class of input: broker states the
close cannot read for certain. This module does not argue about them one at a
time. It explores them.

What runs is the real code path. `manage_positions.main()` with the daily run's
flags (`--submit --backfill-stops --refresh-bars`) plans the time exit and calls
`close_with_protection`, then its same-run re-cover and `cover_beside_exit`. Only
the broker is a model (`tests/time_exit_model.py`), and from the moment the close
starts until run 1 ends, every answer it gives is a choice:

  (a) answered;
  (b) timed out AFTER its effect landed;
  (c) timed out WITHOUT it;
      ... or timed out with the request still in flight: it lands two calls
      later, or when a later choice says so, or overnight (after the run gave up);
  (d) a cancel left `pending_cancel`: for two calls, or until a later choice
      lands it, or until the night, which lands it or never does (it stands
      again); a cancel refused outright (429), or one that errs and lands later;
  (e) before any call, a stop fills, fully or half (also one in pending_cancel),
      only when run 1 is in the session: the model's clock then says open,
      as Alpaca's does, and after the close it fills nothing, as Alpaca does;
  (f) a bracket's take-profit cancel takes its stop along, leaves it live, or
      leaves it pending; a filled leg cancels its sibling;
  (g) at the open, a separate step, our queued market exit fills, half-fills
      (the rest expired, or left `done_for_day` or `calculated`, the end-of-day
      states a day order can sit in), or is rejected, expired or cancelled; a
      gap fills every standing stop or none; or there is no session at all (a
      weekday exchange holiday: the timer runs Mon-Fri and daily_run.sh skips
      weekends only), and run 2 meets yesterday's exit still queued;
  (h) partial fills: of a stop or our exit, in the run or at the open;
  (i) a listing behind the book: list_orders returns the book as it stood one,
      two or three changes back (a cancel or a fill that took effect, an order
      the broker took), while get_order, the client-id lookup and the holding
      are current. Alpaca's listing lags its per-order reads right after a
      write: on 2026-10-05 GOOGL's verdict listed three sells working that the
      release had read cancelled, one by one, seconds before. And it catches
      up, as Alpaca's did seconds later: once the book stops changing, at most
      `LISTING_CATCH_UP` (two) listings in a row lag it, and every one after
      them is current until the next change.

The model keeps the Alpaca rules the close relies on: an open sell reserves its
shares, and while the lot is long a sell for more than is unreserved is refused
(403 held_for_orders); on a flat book a sell is a short sale and is accepted (a
margin account); client_order_id is unique and can be looked up.

Each scenario is: run 1 on day D (a time exit), the night (requests still in
flight land), the open (g), then run 2 on D+1 against a clean broker, in two
variants: the lot is still due (another time exit), or it has moved and only
its stops are maintained (`--backfill-stops`, the back-fill pass).

Run 1 starts at one of three times. At 22:30 UTC, after the close, as
scheduled. Past 00:00 UTC and before D+1's open: the same run fired late
(ai-trader.timer is Persistent=true, so a box that was down at 22:30 runs it
at boot) or rerun by hand. Its trade date is then D+1's, the same as run 2's,
and its exit still queues for D+1's open. Or in the session, by hand.

INVARIANTS, asserted in every scenario. "Covered" means a live stop (new,
accepted, held, partially_filled) holds the share back, each bracket pair once;
a share a working exit of ours holds back is on its way out at the open, and
what the lot had at the start counts both: a lot whose queued exit is
cancelled and never replaced has lost its way out as surely as a stop.

  I1  Never short, and never more sold than held: the holding never goes below
      zero, and no sell stands on a flat book after run 1 or after run 2 (a
      short sale waiting for its trigger).
  I2  Held shares go uncovered for at most one run: after the next daily run
      every share still held is covered by a stop, or by a working exit of
      ours, or the book is flat.
  I3  Never two live sells for the same shares: the sells standing on a long lot
      never reserve more than it holds.
  I4  The time exit either completes (flat, or a working exit holds every share
      no stop does) or leaves the protection it found (every share a stop
      covered, less what a stop has sold since, covered again), and its outcome
      says which: an outcome that claims a close, or `unchanged`, must be true
      once everything it sent has landed.
  I5  Whatever the code cannot resolve is surfaced: if after the night the lot
      is neither flat, nor exiting, nor protected as in I4, run 1 exited 3
      (manage_positions' "a time exit may have left shares with no stop",
      which daily_run.sh pages on). Exit 0 or 1 claims the lot is closed or as
      protected as before.
  I6  The time exit releases protection only: a stop, or a leg of a bracket or
      OCO pair. A working sell that is neither (above all an exit of ours
      queued on an earlier trade date and still waiting for an open) is never
      cancelled, in run 1 or in run 2. Cancelling it puts the lot's way out
      behind a cancel that may stick, and its resend spends exit budget again.
  I7  An exit of ours that the open rejected, expired, cancelled or only half
      filled, on a lot still held, fails run 2 (rc != 0), in both variants:
      the lot spent that session with neither stop nor exit, and by the end
      of run 2 it is covered again, so nothing else would ever say so. Also
      when run 1 was past 00:00 UTC, and the exit's stamp carries run 2's
      own trade date. And where run 2 closes the lot again, that close names
      the exit in its outcome (`missed_exits`), which is what its log line
      says: it sends the next stamp (`-r2`) past the dead one, and a close
      that reads that dead stamp as today's own says nothing of the session.
  I8  Precision. I4 and I5 accept `naked` with rc 3 in any scenario, so on
      their own they pass a close that never puts a stop back. Where every
      read was answered, nothing filled or landed by choice, and at most two
      writes went wrong in a way the broker settles by itself (refused, or a
      reply lost whether or not the request was carried out), the lot ends
      the night flat, exiting or protected; with one such write, run 1 also
      exits 0 or 1, not 3 (on a book fully covered at the start: a failed
      back-fill of shares that were naked before is not the time exit's).
      Not a cancel left pending or a bracket sibling left behind: those
      leave the broker itself in doubt. A listing behind the book (i) is an
      answered read, and no fault here: what it shows stale or leaves out
      the close read by its id. A run 2 armed the same way
      (`Harness.run2_chooser`, pinned schedules only) is held to the same
      end state on a lot it found protected, in place of I2.
  I9  Liveness. A run 2 that met a clean broker takes a lot still due for its
      time exit out: after it the lot is flat or exiting, not only
      protected. I2 and I5 pass a close that sends nothing, run after run,
      beside a sell it reads as in doubt: the lot stays past its time exit
      for good, and the pass pages each night (rc 3) over a lot as covered
      as it was.
  I10 Precision under a listing that lags. Where every fault run 1 met is a
      listing behind the book (i), the close ends in the status, and run 1 in
      the exit code, that a clean broker gives: a listing that still shows
      working what the close read gone by its id, or does not show yet what
      it placed, is no second seller and no bare share. GOOGL's time exit of
      2026-10-05 went as planned and was called `unknown`, two sellers, rc 3.
      With lag beside any other fault I1-I9 hold as before: what the close
      takes from its own reads over the listing must hide no real gap.

Exploration, all of it deterministic, for each of run 1's three times:

  * exhaustive: on seven books (one stop; a bracket's take-profit + held stop;
    two stops; a stop over 6 of 10 shares; yesterday's exit still queued, the
    stop it replaced released, as the run after a holiday finds it; a bracket
    whose stop was ratcheted, its replacement listed apart from the take-
    profit it is still OCO-linked to; three one-share brackets beside a
    seven-share stop, GOOGL's shape), every
    schedule with at most two non-clean choices anywhere in run 1, and every
    schedule of three within ten calls of each other; for each, every night,
    every open (the holiday among them) and both run-2 variants;
  * due only: the `dead_legs` book (GOOGL's book on 10-04: three one-share
    take-profits whose bracket stops are gone, beside a seven-share stop),
    explored as above with run 2 in the `due` variant alone. In `moved` the
    back-fill pass cannot put a stop under a share a take-profit holds back
    (the broker refuses it, held_for_orders), time exit or not, and I2 would
    judge that pass rather than the close;
  * random: four fixed seeds, 6000 schedules each, with per-call event and
    fault rates from 3-10% and 12-30% (one seed draws no events at all: the
    after-hours run as it is scheduled); one night, open and variant drawn per
    schedule;
  * streaks: from each call of the clean run, each fault its kind allows, on
    3, 10 or 20 consecutive calls of that kind (every DELETE throttled, every
    one lost in flight, every read timed out, every listing behind the book),
    with every night, open and variant. The release sends a DELETE at each
    read until the order goes, and what it decides at the end of its confirm
    window takes ten failures in a row to reach: deeper than the bounded
    explorers go, and rarer than the random sweep draws;
  * pinned: schedules deeper than the explorers reach, each found by a review
    and written down as the story it tells (`Scripted`, `TestDeepSchedules`).

TestTheHarnessHasTeeth breaks the code on purpose (a close that lies about
its outcome or never re-arms, a pass with no re-cover or no back-fill) and
requires the invariants to notice.

Any violation prints a minimal trace: faults are dropped one at a time while
the same class still shows, then the scenario is replayed call by call.

The classes carry " [run 1 in market hours]" when the scenario needs a fill
during run 1. The daily run is after the close and cannot meet one, but
manage_positions can be run by hand at any time, and protected_close's own
contract covers regular hours ("In regular hours it fills").

EXPECTATION ON MAIN. The harness was committed before the fix, against main
4bf5f32, where it finds the classes in KNOWN_ON_MAIN. With FIXED False the
exploration must find exactly those, so a new class fails the test and so does
one that went away. The fix flips FIXED to True, and then the exploration must
find nothing at all. Nothing here is xfail: every run explores in full and
prints what it found.

The listing lag (i) and I10 came after FIXED was flipped, committed before
their fix against d879bb3. There the exploration found I10 alone, in two
classes, `exit_submitted` rc 0 turned into `unknown` with rc 3 (the page) or
with rc 1 (a second lagging listing hid the exit from the re-cover, whose
back-fill the exit then refused), and nothing of I1-I9; and
TestAListingBehindTheBook's replay of the GOOGL trace failed. With the lag
made eventually consistent (`LISTING_CATCH_UP`), against main 0d6ab41, the
exploration found the same two classes, and the replay failed the same way:
`unknown`, "two sellers", rc 3. That was before I8 held a lagging listing
beside a refused or lost write (`ModelBroker.settled_writes`). Since then,
0d6ab41's after-close exhaustive exploration finds 557 violations in six
classes: the two of I10 (56), I8 rc 3 over a lot fully covered at the start
with at most one such write (439), and I8 shares left with no stop after
run 1 (62). One of these: book two_stops, the exit refused, the put-back's
listing one change behind still shows stop-B working, so 6 of the 10 shares
go back; the re-cover's, three behind, shows stop-A and stop-B and places no
back-fill, and 4 shares spend the night with no stop. On main, a lagging
listing beside a refused exit leaves shares naked, not only a false page.
The fix reads a listing again, for seconds, while it is older than what the
close read by id (`protected_close.listing_behind`); with it the exploration
finds nothing.
"""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import pytest

from scripts import manage_positions as mp
from tests import time_exit_model as tem
from tests.time_exit_model import (
    BOOKS,
    Harness,
    Scenario,
    Stats,
    build_book,
    explore_bounded,
    explore_random,
    report,
)
from tradingagents_us.dataflows.alpaca_broker import AlpacaRequestError
from tradingagents_us.execution import protected_close as pc

#: Flip to True once the time exit resolves the ambiguous states: the
#: exploration must then find no violation at all. Flipped by the redesign
#: that acts only while the market is shut (protected_close's GUARD): with
#: run 1 after the close, as scheduled, every class main had is gone, and with
#: run 1 in the session the close defers and the fills it meets break nothing.
FIXED = True

#: The classes each exploration finds on main (4bf5f32). Two root causes:
#:
#: R1  an exit whose POST reply was lost lands after the close stopped looking
#:     for it (23 s of lookups, then one more after the re-arm), on a lot the
#:     re-armed stop has sold meanwhile: nothing reserves the shares any more,
#:     so the late exit is a short sale, and the close says `unchanged`, rc 1.
#:     `_exit_landed`'s "once a re-armed stop stands, a later landing is
#:     refused" does not hold once that stop fills.
#: R2  cover counted off a listing read BEFORE the holding: a stop that fills,
#:     wholly or in part, between the two reads still counts at its old size.
#:     In protected_close (`_read_truth` -> `_rearm` -> `_verdict`) the re-arm
#:     comes out short and the outcome still says `unchanged`. In the same-run
#:     re-cover (`_recover_unclosed`) the back-fill comes out short too, and a
#:     `cover_beside_exit` that reports `unchanged` takes the name off
#:     `uncovered`, so the run exits 1 ("as protected as before") over naked
#:     shares.
_MH = " [run 1 in market hours]"
KNOWN_ON_MAIN: dict[str, frozenset[str]] = {
    "exhaustive": frozenset({
        "I1 short" + _MH,                                          # R1
        "I1 sell standing on a flat book after run 1" + _MH,       # R1
        "I4 close says unchanged but the lot is flat+sell" + _MH,  # R1
        "I5 flat+sell after run 1, reported as rc=1" + _MH,        # R1
        "I5 naked after run 1, reported as rc=1" + _MH,            # R2, the re-cover
    }),
    "random": frozenset({
        "I1 short" + _MH,                                          # R1
        "I1 sell standing on a flat book after run 1" + _MH,       # R1
        "I4 close says unchanged but the lot is flat+sell" + _MH,  # R1
        "I5 flat+sell after run 1, reported as rc=1" + _MH,        # R1
        "I4 close says unchanged but the lot is naked" + _MH,      # R2, the close
    }),
}

#: (max non-clean choices, window between consecutive ones or None).
EXHAUSTIVE = ((2, None), (3, 10))

#: When run 1 starts, and whether the clock then says open: after the close as
#: scheduled, past 00:00 UTC (a late or repeated run, see the module doc), or
#: in the session, by hand.
RUN1 = {
    "after-close": (tem.RUN1_AT, False),
    "past-midnight": (tem.CATCH_UP_AT, False),
    "in-session": (tem.RUN1_AT, True),
}

#: Each exploration's floor, about three quarters of what it covers (599k
#: scenarios after the close, either time, and 11.7k in the session; 505k
#: and 10.5k before listings could lag): the margin the original
#: 200k-of-266k floor kept. In the session the close defers at its first
#: read, so there is little left to explore: the fills it meets, the
#: re-cover, and run 2.
EXHAUSTIVE_FLOOR = {"after-close": 449_000, "past-midnight": 449_000, "in-session": 8_800}

#: The due-only exploration's floor, about three quarters of what it covers
#: (111k scenarios after the close, either time, and 9.3k in the session).
DUE_ONLY_FLOOR = {"after-close": 83_000, "past-midnight": 83_000, "in-session": 7_000}

#: Each streak exploration's floor, about three quarters of what it covers
#: (7.4k scenarios after the close, either time, and 0.7k in the session, the
#: due-only books' among them).
STREAK_FLOOR = {"after-close": 5_500, "past-midnight": 5_500, "in-session": 550}

#: (seed, schedules, per-call event rate, per-call fault rate).
RANDOM = (
    (101, 6000, 0.06, 0.12),
    (202, 6000, 0.00, 0.30),
    (303, 6000, 0.10, 0.25),
    (404, 6000, 0.03, 0.20),
)


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(monkeypatch)


def _judge(harness: Harness, stats: Stats, part: str) -> None:
    text = report(harness, stats, f"{part}: ")
    print(text)
    found = frozenset(stats.findings)
    if FIXED:
        if found:
            pytest.fail(f"the time exit still violates its invariants:\n{text}", pytrace=False)
        return
    known = KNOWN_ON_MAIN[part]
    if found != known:
        pytest.fail(
            f"the {part} exploration no longer finds exactly main's classes.\n"
            f"new: {sorted(found - known)}\ngone: {sorted(known - found)}\n"
            "A fix flips FIXED (and then must find nothing); a new class is a "
            f"regression.\n{text}",
            pytrace=False,
        )


@pytest.mark.slow
@pytest.mark.parametrize("run1", list(RUN1))
def test_exhaustive_exploration(harness: Harness, run1: str) -> None:
    harness.run1_at, harness.market_open = RUN1[run1]
    stats = Stats()
    for max_faults, window in EXHAUSTIVE:
        for book in BOOKS:
            stats.merge(explore_bounded(harness, book, max_faults, window))
    assert stats.scenarios >= EXHAUSTIVE_FLOOR[run1], "the exploration shrank"
    _judge(harness, stats, "exhaustive")


@pytest.mark.slow
@pytest.mark.parametrize("run1", list(RUN1))
def test_random_sweep(harness: Harness, run1: str) -> None:
    harness.run1_at, harness.market_open = RUN1[run1]
    stats = Stats()
    for seed, n, p_env, p_fault in RANDOM:
        stats.merge(explore_random(harness, BOOKS, seed, n, p_env=p_env, p_fault=p_fault))
    assert stats.scenarios >= 20_000
    _judge(harness, stats, "random")


@pytest.mark.slow
@pytest.mark.parametrize("run1", list(RUN1))
def test_due_only_exploration(harness: Harness, run1: str) -> None:
    harness.run1_at, harness.market_open = RUN1[run1]
    stats = Stats()
    for max_faults, window in EXHAUSTIVE:
        for book in tem.DUE_ONLY_BOOKS:
            stats.merge(explore_bounded(harness, book, max_faults, window, variant="due"))
    assert stats.scenarios >= DUE_ONLY_FLOOR[run1], "the exploration shrank"
    _judge(harness, stats, "exhaustive")


@pytest.mark.parametrize("run1", list(RUN1))
def test_streak_exploration(harness: Harness, run1: str) -> None:
    harness.run1_at, harness.market_open = RUN1[run1]
    stats = Stats()
    for book in BOOKS:
        stats.merge(tem.explore_streaks(harness, book))
    for book in tem.DUE_ONLY_BOOKS:
        stats.merge(tem.explore_streaks(harness, book, variant="due"))
    assert stats.scenarios >= STREAK_FLOOR[run1], "the exploration shrank"
    _judge(harness, stats, "streak")


class TestDeepSchedules:
    """Counterexamples a review found, pinned as the story they tell.

    Most are deeper than the explorers reach; the rest were found on a start
    book the explorers did not have then, and pin its shortest story. Each
    replays one scripted schedule through run 1, then every night, open and
    run-2 variant after it, and must find nothing.
    """

    def _judge(self, harness: Harness, book: str, chooser: tem.Scripted) -> None:
        stats = Stats()
        harness.evaluate(Scenario(book, ()), stats, chooser=chooser)
        assert not chooser.rules, f"the schedule did not play out: {chooser.rules} left"
        if stats.findings:
            pytest.fail(report(harness, stats, f"{book}: "), pytrace=False)

    def test_a_lost_back_fill_beside_a_half_re_armed_lot_is_not_called_covered(
        self, harness: Harness
    ) -> None:
        # Six faults. The exit POST is lost and never lands; of the two stops
        # it released, the 6-share re-arm stands and the 4-share one is
        # refused; the verdict cannot read the book, so the close hands the
        # stamp on as unsettled; and the re-cover's 4-share back-fill beside
        # it is lost too. Still 6 of 10 covered: four shares that had a stop
        # have none, and the pass must say so (rc 3), not "as protected as
        # before" (rc 1).
        self._judge(harness, "two_stops", tem.Scripted(
            ("submit_order(market", "timeout_lost"),
            ("submit_order(stop sell 4 @88", "422"),
            ("list_orders", "timeout", 3),
            ("-cover", "timeout_lost"),
        ))

    @pytest.mark.parametrize("book", ["stop", "bracket", "two_stops"])
    def test_a_delete_lost_in_flight_then_throttled_through_the_confirm_window(
        self, harness: Harness, book: str
    ) -> None:
        # Eleven faults. The first DELETE times out with the cancel still on
        # its way, and the next ten, one per read of the confirm window, are
        # throttled. The order reads working all the while. Only the note
        # that the first DELETE may yet land keeps the release waiting it out
        # past the window; without it the stop is taken at its word, the
        # close says `unchanged` (rc 1), and the cancel lands in the night on
        # a lot nothing else covers.
        self._judge(harness, book, tem.Scripted(
            ("cancel_order", "timeout_late"),
            ("cancel_order", "429", 10),
        ))

    def test_a_stuck_take_profit_cancel_is_on_its_way_to_a_ratcheted_stop_listed_apart(
        self, harness: Harness
    ) -> None:
        # One fault, on the `ratcheted` book. The take-profit's cancel reads
        # pending_cancel through both windows, so the release ends there. The
        # stop that replaced the bracket's own is listed at the top level,
        # and only the filled entry and the replaced leg share the take-
        # profit's root: read by the nesting, the take-profit has a partner
        # and the stop has none, and the stop counted as cover. The close
        # said `unchanged` (rc 1), and the cancel landing in the night took
        # the stop along at the broker, OCO-linked as it still is.
        self._judge(harness, "ratcheted", tem.Scripted(("cancel_order(tp-A", "pending_stuck")))

    @pytest.mark.parametrize("book", ["bracket", "three_brackets"])
    def test_a_stop_a_take_profits_cancel_left_behind_is_released_the_next_run(
        self, harness: Harness, book: str
    ) -> None:
        # One fault. The first take-profit's cancel lands and leaves its stop
        # pending_cancel, and that cancel never lands: in the night the stop
        # stands `held` again, its pair ended. Read as a cancel still on its
        # way, it held every close after off (nothing sent, naked, rc 3) on a
        # lot as covered as it was, and the time exit never came (I9).
        tp = "tp-A" if book == "bracket" else "tp-1"
        self._judge(harness, book, tem.Scripted((f"cancel_order({tp}", "ok+sib_pending")))

    def test_a_share_a_failed_re_arm_left_bare_beside_a_bracket_is_back_filled(
        self, harness: Harness
    ) -> None:
        # Two faults, on the `three_brackets` book (GOOGL's shape). The second
        # take-profit's cancel leaves its stop pending, and the re-arm of the
        # first bracket's one-share stop is refused: run 1 pages (rc 3). In
        # the night the pending cancel never lands, so that stop stands `held`
        # with its pair ended (a close after releases it with the rest). The
        # third take-profit, working beside its own stop, held the re-cover
        # off: the first bracket's share had no stop after run 2.
        self._judge(harness, "three_brackets", tem.Scripted(
            ("cancel_order(tp-2", "ok+sib_pending"),
            ("-arm-", "422"),
        ))


class TestAListingBehindTheBook:
    """A listing read right after a write may not show it yet (i); I10 holds the close to it."""

    def test_googl_on_2026_10_05_is_one_seller_not_two(self, harness: Harness) -> None:
        # The live trace, on GOOGL's shape (`three_brackets`: a stop over most
        # of the lot, three one-share brackets, take-profit new and stop
        # held). The release cancels all seven sells and reads each one gone
        # by its own id; the exit is accepted. The verdict's listing is three
        # changes behind, and shows the last bracket's take-profit and stop
        # and the standalone stop still working beside the exit, as the live
        # one did. Taken at its word: `unknown`, "two sellers", rc 3, and the
        # re-cover passed the lot over for the exit standing on it. A clean
        # broker closes it with rc 0, and so must this.
        chooser = tem.Scripted(("submit_order(market", "ok"), ("list_orders", "lag3"))
        stats = Stats()
        b = harness.evaluate(Scenario("three_brackets", ()), stats, chooser=chooser)
        assert not chooser.rules, f"the schedule did not play out: {chooser.rules} left"
        assert b.stale == [["sl-3", "stop-S", "tp-3"]], b.stale
        (close,) = [o for kind, o in b.outcomes if kind == "close"]
        assert (close.status, b.rc) == ("exit_submitted", 0), (close.detail, b.rc)
        if stats.findings:
            pytest.fail(report(harness, stats, "three_brackets: "), pytrace=False)


class TestARunPastMidnight:
    """Run 1 past 00:00 UTC stamps its exit with the trade date run 2 has too.

    That exit still queues for the open between them, and if the open does not
    fill it the lot spends the session with neither stop nor exit: run 2 must
    say so (I7) whether the stamp's date is its own or not.
    """

    @pytest.mark.parametrize("book", BOOKS)
    @pytest.mark.parametrize(
        "opening", [(how, "hold") for how in tem.OPEN_EXIT_OUTCOMES[1:]], ids=lambda o: o[0]
    )
    @pytest.mark.parametrize("variant", tem.RUN2_VARIANTS)
    def test_an_exit_the_open_did_not_fill_fails_run_2(
        self, harness: Harness, book: str, opening: tuple[str, str], variant: str
    ) -> None:
        harness.run1_at = tem.CATCH_UP_AT
        stats = Stats()
        b = harness.evaluate(Scenario(book, (), night=(), opening=opening, variant=variant), stats)
        close = next(o for kind, o in b.outcomes if kind == "close")
        assert close.ok, close
        if stats.findings:
            pytest.fail(report(harness, stats, f"{book}: "), pytrace=False)

    def test_a_holiday_leaves_the_exit_queued_and_nothing_missed(self, harness: Harness) -> None:
        harness.run1_at = tem.CATCH_UP_AT
        stats = Stats()
        harness.evaluate(Scenario("stop", (), night=(), opening=tem.OPEN_HOLIDAY), stats)
        assert not stats.findings

    @pytest.mark.parametrize("book", ["stop", "bracket"])
    @pytest.mark.parametrize("rearm", ["timeout_lost", "422"])
    def test_two_closes_under_one_stamp_each_place_their_own_back_fill(
        self, harness: Harness, book: str, rearm: str
    ) -> None:
        # Both runs armed, each with the same two settled write faults: the
        # exit's POST is lost and never arrives, and the re-arm is lost or
        # refused. Run 1 back-fills beside the exit it never found, under
        # `<stamp>-cover`, and ends protected (rc 1). Run 2 shares its trade
        # date, and with no order under the stamp it sends that stamp again,
        # releases the back-fill, and meets the same two faults. Its own
        # back-fill went under the same id: refused as a duplicate, resolved
        # to the back-fill it had just cancelled, and the lot entered the
        # next session with neither stop nor exit (rc 3).
        harness.run1_at = tem.CATCH_UP_AT

        def script() -> tem.Scripted:
            return tem.Scripted(("submit_order(market", "timeout_lost"), ("-arm-", rearm))

        harness.run2_chooser = script
        run1 = script()
        stats = Stats()
        b = harness.evaluate(
            Scenario(book, (), night=(), opening=("fill", "hold"), variant="due"), stats,
            chooser=run1,
        )
        assert not run1.rules, f"run 1's schedule did not play out: {run1.rules} left"
        (run2,) = harness.run2_choosers
        assert not run2.rules, f"run 2's schedule did not play out: {run2.rules} left"
        assert [kind for kind, _ in b.outcomes] == ["close", "cover"]
        if stats.findings:
            pytest.fail(report(harness, stats, f"{book}: "), pytrace=False)


class TestTheModel:
    """The broker model keeps the rules the close relies on, and a clean run closes."""

    @pytest.mark.parametrize("book", [*BOOKS, *tem.DUE_ONLY_BOOKS])
    def test_a_clean_broker_closes_every_book(self, harness: Harness, book: str) -> None:
        stats = Stats()
        b = harness.evaluate(Scenario(book, ()), stats)
        (close,) = [o for kind, o in b.outcomes if kind == "close"]
        # A lot whose exit is already queued is on its way out: nothing is sent.
        assert close.status == ("already_exiting" if book == "queued" else "exit_submitted")
        assert b.kind(tem.cover_wanted(book)) == "exiting"
        assert not stats.findings
        openings = len(tem.OPEN_EXIT_OUTCOMES) + 1  # the holiday
        assert stats.scenarios == openings * len(tem.RUN2_VARIANTS)

    def test_the_calendar_skips_weekends_and_holidays_and_agrees_with_the_clock(self) -> None:
        b = build_book("stop")
        b.open_market(tem.OPEN_HOLIDAY)
        b.base_time, b.elapsed = tem.RUN2_AT, 0.0
        days = [s.date for s in b.calendar(tem.RUN1_AT.date(), tem.RUN2_AT.date() + timedelta(4))]
        assert days == [date(2026, 10, 1), date(2026, 10, 5), date(2026, 10, 6)]
        assert b.clock().next_open == "2026-10-05T13:30:00+00:00"

    def test_a_holiday_leaves_the_queued_exit_for_run_2(self) -> None:
        b = build_book("stop")
        b.cancel_order("stop-A")
        b.submit_order(symbol="XOM", qty=10, side="sell", order_type="market",
                       time_in_force="day", client_order_id="tr-exit-time-XOM-20261001")
        b.open_market(tem.OPEN_HOLIDAY)
        assert b.held == 10.0
        assert [(r.role, r.status) for r in b.sells()] == [("exit", "accepted")]

    def test_a_cancelled_queued_exit_is_not_protected(self) -> None:
        b = build_book("queued")
        b.cancel_order("exit-prev")
        assert b.kind(tem.cover_wanted("queued")) == "naked"
        assert b.violations and b.violations[0][0].startswith("I6 ")

    def test_a_sell_beyond_the_unreserved_shares_is_refused(self) -> None:
        b = build_book("stop")
        with pytest.raises(AlpacaRequestError) as refused:
            b.submit_order(symbol="XOM", qty=10, side="sell", order_type="market")
        assert refused.value.status_code == 403 and "held_for_orders" in refused.value.body

    def test_a_sell_on_a_flat_book_is_a_short_sale(self) -> None:
        b = build_book("stop")
        b.cancel_order("stop-A")
        b.held = 0.0
        b.submit_order(symbol="XOM", qty=10, side="sell", order_type="stop", stop_price=90.0)
        assert b.kind(10.0) == "flat+sell"

    def test_a_bracket_leg_takes_its_sibling_with_it(self) -> None:
        b = build_book("bracket")
        b.cancel_order("tp-A")
        assert b.recs["sl-A"].status == "canceled"
        assert b.reserved() == 0.0

    def test_a_listing_behind_the_book_catches_up_once_the_book_stops_changing(self) -> None:
        # However hard the chooser pulls the listing back, it lags a write for
        # LISTING_CATCH_UP listings at most, and the next write starts it over.
        b = build_book("stop")
        b.armed = True
        b.chooser = lambda i, kind, env, outs, what="": (
            None, max((o for o in outs if o.startswith("lag")), default="ok")
        )

        def listed() -> dict[str, str]:
            return {o.id: o.status for o in b.list_orders(status="all") if o.side == "sell"}

        b.cancel_order("stop-A")
        assert [listed() for _ in range(4)] == [
            *[{"stop-A": "new"}] * tem.LISTING_CATCH_UP, *[{"stop-A": "canceled"}] * 2
        ]
        new = b.submit_order(symbol="XOM", qty=10, side="sell", order_type="stop",
                             time_in_force="gtc", stop_price=90.0, client_order_id="re")
        assert new.id not in listed(), "a write starts the lag over"
        assert b.stale == [["stop-A"]] * (tem.LISTING_CATCH_UP + 1)

    def test_a_client_order_id_is_unique(self) -> None:
        b = build_book("stop")
        b.cancel_order("stop-A")
        b.submit_order(symbol="XOM", qty=4, side="sell", order_type="market", client_order_id="x")
        with pytest.raises(AlpacaRequestError, match="unique"):
            b.submit_order(
                symbol="XOM", qty=4, side="sell", order_type="market", client_order_id="x"
            )


class TestTheHarnessHasTeeth:
    """Break the code under test on purpose: the invariants must notice."""

    def test_a_close_that_claims_success_it_did_not_have(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = mp.close_with_protection

        def liar(client, ticker, **kw):  # noqa: ANN001, ANN202
            out = real(client, ticker, **kw)
            return out if out.ok else dataclasses.replace(out, status="exit_submitted")

        monkeypatch.setattr(mp, "close_with_protection", liar)
        stats = explore_bounded(Harness(monkeypatch), "stop", 1)
        assert any(c.startswith("I4 close says exit_submitted") for c in stats.findings)
        assert any(c.startswith("I5 ") for c in stats.findings)

    def test_a_close_that_does_not_name_the_exit_the_open_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The pass still names it and fails (rc 1), so only the close's own
        # report can tell.
        monkeypatch.setattr(pc, "missed_exits", lambda *a, **kw: ())
        stats = explore_bounded(Harness(monkeypatch), "stop", 1)
        assert "I7 run 2's close did not name the exit the open did not fill (due)" in (
            stats.findings
        ), sorted(stats.findings)
        assert not any("unreported" in c for c in stats.findings)

    def test_a_next_run_that_does_not_back_fill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tem, "ARGV", ["--submit", "--refresh-bars"])
        stats = explore_bounded(Harness(monkeypatch), "stop", 1)
        assert "I2 naked after run 2 (moved)" in stats.findings

    @pytest.mark.parametrize("book", ["stop", "bracket", "two_stops"])
    def test_a_close_that_does_not_re_arm(
        self, monkeypatch: pytest.MonkeyPatch, book: str
    ) -> None:
        # The same-run re-cover still back-fills what the close left naked,
        # and run 2 would too: only the close's own report can tell.
        monkeypatch.setattr(pc, "_rearm", lambda ctx, truth: pc._Placed())
        stats = explore_bounded(Harness(monkeypatch), book, 1)
        assert any(c.startswith("I8 rc=3") for c in stats.findings), sorted(stats.findings)

    def test_a_pass_that_does_not_re_cover(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Exit and re-arm both refused: the close says naked (rc 3), and
        # without the re-cover the lot spends the night with no stop.
        monkeypatch.setattr(mp, "_recover_unclosed", lambda *a, **kw: 0)
        stats = explore_bounded(Harness(monkeypatch), "stop", 2)
        assert any(c.startswith("I8 naked") for c in stats.findings), sorted(stats.findings)

    def test_a_release_that_forgets_a_delete_it_got_no_answer_to(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A DELETE that timed out is noted as possibly landed only when the
        # broker refused it: a timed-out cancel still on its way is then
        # forgotten, and the stop it strips counted as cover.
        def forgetful(client, order, release) -> None:  # noqa: ANN001
            try:
                client.cancel_order(order.id)
            except Exception:  # noqa: BLE001
                return
            release.sent[order.id] = order

        monkeypatch.setattr(pc, "_send_cancel", forgetful)
        stats = tem.explore_streaks(Harness(monkeypatch), "stop")
        assert "I4 close says unchanged but the lot is naked" in stats.findings, sorted(
            stats.findings
        )
        assert "I5 naked after run 1, reported as rc=1" in stats.findings

    def test_a_second_close_that_reuses_a_spent_back_fill_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Run 2 armed with the faults run 1 met: only its own end state can
        # tell, since it reports rc 3 and the run after it back-fills.
        monkeypatch.setattr(pc, "MAX_STOP_ID_ATTEMPTS", 1)
        harness = Harness(monkeypatch)
        harness.run1_at = tem.CATCH_UP_AT

        def script() -> tem.Scripted:
            return tem.Scripted(("submit_order(market", "timeout_lost"), ("-arm-", "422"))

        harness.run2_chooser = script
        stats = Stats()
        harness.evaluate(
            Scenario("stop", (), night=(), opening=("fill", "hold"), variant="due"), stats,
            chooser=script(),
        )
        assert "I8 run 2 ends naked with 2 settled write fault(s) (due)" in stats.findings, (
            sorted(stats.findings)
        )

    @pytest.mark.parametrize("book", ["bracket", "three_brackets"])
    def test_a_close_that_waits_on_a_leg_whose_pair_ended_long_ago(
        self, monkeypatch: pytest.MonkeyPatch, book: str
    ) -> None:
        # The book read as it was before: a working leg whose pair ended is in
        # doubt. A take-profit cancel that leaves its stop behind then holds
        # the close off on every run after, on a lot as covered as it was:
        # only liveness can tell.
        real = pc._read_book

        def stalled(client, ticker, reason="time"):  # noqa: ANN001, ANN202
            got = real(client, ticker, reason)
            ended = {
                got.group.get(o.id, o.id) for o in got.records.values()
                if o.status.lower() in pc.PAIR_ENDING_STATUSES
            }
            stuck = tuple(o for o in got.live if got.group.get(o.id, o.id) in ended)
            return dataclasses.replace(
                got, live=tuple(o for o in got.live if o not in stuck),
                unclear=(*got.unclear, *stuck),
            )

        monkeypatch.setattr(pc, "_read_book", stalled)
        stats = explore_bounded(Harness(monkeypatch), book, 1)
        assert "I9 a clean run 2 left a lot due for its time exit held (due)" in (
            stats.findings
        ), sorted(stats.findings)

    def test_a_close_that_takes_a_stop_it_released_as_standing_off_a_lagging_listing(
        self, monkeypatch: pytest.MonkeyPatch, harness: Harness
    ) -> None:
        # The exit refused, and the put-back's listing one change behind: it
        # still shows the released stop working. Taken at its word, nothing
        # goes back and the close pages naked (rc 3) over a write the broker
        # settled by itself. Only I8 can tell, and only if a lagging listing
        # counts as the answered read it is, not as a fault.
        real = pc.listing_behind

        def blind(orders, gone, placed):  # noqa: ANN001, ANN202
            return real(orders, (), placed)

        monkeypatch.setattr(pc, "listing_behind", blind)
        monkeypatch.setattr(mp, "listing_behind", blind)
        stats = Stats()
        chooser = tem.Scripted(("submit_order(market", "422"), ("list_orders", "lag1"))
        harness.evaluate(Scenario("stop", ()), stats, chooser=chooser)
        assert not chooser.rules, f"the schedule did not play out: {chooser.rules} left"
        assert any(c.startswith("I8 rc=3") for c in stats.findings), sorted(stats.findings)

    def test_a_close_and_a_pass_that_put_nothing_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pc, "_rearm", lambda ctx, truth: pc._Placed())
        monkeypatch.setattr(mp, "_recover_unclosed", lambda *a, **kw: 0)
        stats = explore_bounded(Harness(monkeypatch), "stop", 1)
        assert any(c.startswith("I8 naked") for c in stats.findings), sorted(stats.findings)
