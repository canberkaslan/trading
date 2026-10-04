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
  (g) at the open, a separate step, our queued market exit fills, half-fills,
      or is rejected, expired or cancelled; a gap fills every standing stop or
      none; or there is no session at all (a weekday exchange holiday: the
      timer runs Mon-Fri and daily_run.sh skips weekends only), and run 2
      meets yesterday's exit still queued;
  (h) partial fills: of a stop or our exit, in the run or at the open.

The model keeps the Alpaca rules the close relies on: an open sell reserves its
shares, and while the lot is long a sell for more than is unreserved is refused
(403 held_for_orders); on a flat book a sell is a short sale and is accepted (a
margin account); client_order_id is unique and can be looked up.

Each scenario is: run 1 on day D (a time exit), the night (requests still in
flight land), the open (g), then run 2 on D+1 against a clean broker, in two
variants: the lot is still due (another time exit), or it has moved and only
its stops are maintained (`--backfill-stops`, the back-fill pass).

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

Exploration, all of it deterministic:

  * exhaustive: on five books (one stop; a bracket's take-profit + held stop;
    two stops; a stop over 6 of 10 shares; yesterday's exit still queued, the
    stop it replaced released, as the run after a holiday finds it), every
    schedule with at most two non-clean choices anywhere in run 1, and every
    schedule of three within ten calls of each other; for each, every night,
    every open (the holiday among them) and both run-2 variants;
  * random: four fixed seeds, 6000 schedules each, with per-call event and
    fault rates from 3-10% and 12-30% (one seed draws no events at all: the
    after-hours run as it is scheduled); one night, open and variant drawn per
    schedule.

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
"""

from __future__ import annotations

import dataclasses

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
@pytest.mark.parametrize("market_open", [False, True], ids=["after-close", "in-session"])
def test_exhaustive_exploration(harness: Harness, market_open: bool) -> None:
    harness.market_open = market_open
    stats = Stats()
    for max_faults, window in EXHAUSTIVE:
        for book in BOOKS:
            stats.merge(explore_bounded(harness, book, max_faults, window))
    # In the session the close defers at its first read, so there is little
    # left to explore: the fills it meets, the re-cover, and run 2. Each floor
    # is about three quarters of what the run explores (127k after the close,
    # 5.2k in the session), the margin the original 200k-of-266k floor kept.
    assert stats.scenarios >= (3_900 if market_open else 95_000), "the exploration shrank"
    _judge(harness, stats, "exhaustive")


@pytest.mark.slow
@pytest.mark.parametrize("market_open", [False, True], ids=["after-close", "in-session"])
def test_random_sweep(harness: Harness, market_open: bool) -> None:
    harness.market_open = market_open
    stats = Stats()
    for seed, n, p_env, p_fault in RANDOM:
        stats.merge(explore_random(harness, BOOKS, seed, n, p_env=p_env, p_fault=p_fault))
    assert stats.scenarios >= 20_000
    _judge(harness, stats, "random")


class TestTheModel:
    """The broker model keeps the rules the close relies on, and a clean run closes."""

    @pytest.mark.parametrize("book", BOOKS)
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

    def test_a_next_run_that_does_not_back_fill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tem, "ARGV", ["--submit", "--refresh-bars"])
        stats = explore_bounded(Harness(monkeypatch), "stop", 1)
        assert "I2 naked after run 2 (moved)" in stats.findings
