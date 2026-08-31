"""The relay's cadence and chain arithmetic, pinned to exact numbers.

Every instant is passed in, so none of this sleeps or reaches the network. The
properties worth defending are narrow and specific: the cadence must not drift
across a five-hour link, a run must never conclude the chain is alive because it
found *itself*, and no malformed input may take the last alerter down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradingagents_us.monitoring.relay import (
    CHAIN_GRACE_S,
    DEFAULT_BUDGET_S,
    DEFAULT_INTERVAL_S,
    MAX_BUDGET_S,
    RunSummary,
    chain_state,
    next_tick,
    parse_seconds,
    planned_probes,
)

START = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


# --- cadence -----------------------------------------------------------------


def test_first_wait_is_a_full_interval_from_the_launch_probe():
    tick = next_tick(START, START)
    assert tick is not None
    assert tick.index == 1
    assert tick.due_at == _at(1800)
    assert tick.sleep_s == 1800.0


def test_ticks_are_anchored_to_the_start_so_a_slow_probe_cannot_drift_the_cadence():
    # Probe 1 ran long and finished 90s into its slot. The next tick is still at
    # start+3600 — 1710s away — not a full interval from *now*. Across ten ticks
    # the naive version would land minutes late; this is the whole reason the
    # planner takes `started_at` rather than "when the last one finished".
    tick = next_tick(START, _at(1800 + 90))
    assert tick is not None
    assert tick.index == 2
    assert tick.due_at == _at(3600)
    assert tick.sleep_s == 1710.0


def test_an_overrun_past_a_tick_drops_that_probe_rather_than_shifting_the_rest():
    # A probe that hangs for 40 minutes swallows tick 1 entirely. The answer is
    # tick 2 at its original instant — losing one reading, not sliding the plan.
    tick = next_tick(START, _at(2400))
    assert tick is not None
    assert tick.index == 2
    assert tick.due_at == _at(3600)


def test_a_tick_due_exactly_now_is_not_returned_as_the_next_one():
    # start+1800 is tick 1's instant. Standing on it, the next thing to wait for
    # is tick 2; returning tick 1 with sleep 0 would probe twice in one slot.
    tick = next_tick(START, _at(1800))
    assert tick is not None
    assert tick.index == 2
    assert tick.sleep_s == 1800.0


def test_window_ends_when_the_next_tick_would_fall_outside_the_budget():
    # 18600s / 1800s = tick 10 at 18000s is the last one inside the budget.
    last = next_tick(START, _at(17999))
    assert last is not None
    assert last.index == 10
    assert next_tick(START, _at(18000)) is None


def test_a_clock_that_runs_backwards_still_yields_a_forward_tick():
    # Not hypothetical enough to ignore: `now` is sampled per probe, and a
    # negative elapsed would floor-divide to a negative index and hand back a
    # tick in the past — i.e. a busy loop of instant probes.
    tick = next_tick(START, START - timedelta(seconds=5))
    assert tick is not None
    assert tick.index == 1
    assert tick.sleep_s == 1805.0


def test_probe_count_covers_the_launch_probe_plus_every_scheduled_tick():
    assert planned_probes(DEFAULT_INTERVAL_S, DEFAULT_BUDGET_S) == 11


def test_a_non_positive_interval_is_a_programming_error_not_a_silent_zero():
    with pytest.raises(ValueError, match="positive"):
        next_tick(START, START, interval_s=0)


# --- chain state -------------------------------------------------------------


def _run(run_id: int, status: str = "in_progress", age_s: float | None = 60.0) -> RunSummary:
    return RunSummary(
        run_id=run_id,
        status=status,
        started_at=None if age_s is None else _at(-age_s),
    )


def test_no_runs_means_no_chain_and_something_must_start_one():
    state = chain_state([], START)
    assert (state.alive, state.stalled, state.needs_start) == (False, False, True)
    assert "no relay run" in state.detail


def test_a_queued_run_counts_as_alive():
    # During a hand-off the successor sits queued behind the concurrency group.
    # Reading only `in_progress` would call that a dead chain and dispatch a
    # third link every time the chain worked correctly.
    state = chain_state([_run(1, status="queued")], START)
    assert state.alive is True
    assert state.needs_start is False


def test_a_completed_run_does_not_keep_the_chain_alive():
    state = chain_state([_run(1, status="completed", age_s=30.0)], START)
    assert state.needs_start is True


def test_a_run_excludes_itself_so_a_hand_off_cannot_conclude_it_is_covered():
    runs = [_run(99)]
    assert chain_state(runs, START).alive is True
    assert chain_state(runs, START, exclude_ids=[99]).needs_start is True


def test_a_run_past_budget_plus_grace_is_stalled_and_gets_a_replacement_queued():
    stuck = _run(7, age_s=DEFAULT_BUDGET_S + CHAIN_GRACE_S + 60)
    state = chain_state([stuck], START)
    assert (state.alive, state.stalled, state.needs_start) == (True, True, True)
    assert "run 7" in state.detail


def test_a_run_inside_budget_plus_grace_is_healthy_not_stalled():
    state = chain_state([_run(7, age_s=DEFAULT_BUDGET_S + 60)], START)
    assert (state.stalled, state.needs_start) == (False, False)


def test_a_run_with_no_timestamp_is_alive_but_never_declared_stuck():
    # "I cannot tell how old this is" must not become "kill it": a missing field
    # would otherwise cancel a perfectly healthy chain on every check.
    state = chain_state([_run(3, age_s=None)], START)
    assert (state.alive, state.stalled, state.needs_start) == (True, False, False)
    assert "age unknown" in state.detail


def test_one_stalled_run_beside_a_healthy_one_still_asks_for_a_replacement():
    runs = [_run(1, age_s=120.0), _run(2, age_s=DEFAULT_BUDGET_S + CHAIN_GRACE_S + 1)]
    state = chain_state(runs, START)
    assert state.stalled is True
    assert "run 2" in state.detail


# --- configuration boundary --------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", "abc", "0", "-90", "nan-ish"])
def test_unusable_config_falls_back_to_the_default_instead_of_raising(raw):
    # This is the seam between an env var and the one alerter that has to work
    # when everything else is broken. A typo costs the default cadence.
    assert parse_seconds(raw, DEFAULT_INTERVAL_S) == DEFAULT_INTERVAL_S


def test_a_usable_override_is_honoured():
    assert parse_seconds("600", DEFAULT_INTERVAL_S) == 600.0


def test_the_budget_is_capped_below_the_runner_ceiling_however_large_the_override():
    # A window that outlives the six-hour runner kill never reaches its hand-off
    # step, which turns the chain into a single link that dies quietly.
    assert parse_seconds("999999", DEFAULT_BUDGET_S, maximum=MAX_BUDGET_S) == MAX_BUDGET_S
    assert MAX_BUDGET_S < 6 * 3600
