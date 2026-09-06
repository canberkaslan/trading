"""The bracket replay, pinned to the cases where a backtest would rather guess.

Most of these are about refusals: the bar that touches both levels, the gap
that must not fill at the level, the entry bar that must not be scanned. Each
one is a place where resolving the ambiguity is easy, produces a plausible
number, and is wrong — which is exactly the kind of thing that survives review
unless a test says otherwise.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from tradingagents_us.backtest.exit_paths import (
    Bar,
    BracketEntry,
    attributed,
    census,
    level_mix,
    replay_signals,
    resolve_bar,
    sample_shortfall,
    simulate_all,
    simulate_exit,
)
from tradingagents_us.execution.exit_quality import bucket_by_exit, strategy_bucket

ENTRY_DAY = date(2024, 3, 1)


def _entry(**kw: object) -> BracketEntry:
    base: dict[str, object] = {
        "trade_id": "t1",
        "symbol": "AAPL",
        "entry_day": ENTRY_DAY,
        "entry_price": 100.0,
        "quantity": 10.0,
        "stop_price": 95.0,
        "take_profit_price": 110.0,
    }
    base.update(kw)
    return BracketEntry(**base)  # type: ignore[arg-type]


def _bar(day: date, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(day=day, open=o, high=h, low=low, close=c)


def test_importing_this_module_does_not_pull_in_vectorbt() -> None:
    """The pure module must not inherit the engine's dependencies.

    It did once, through the package `__init__`, and the cost was not abstract:
    vectorbt imports plotly, plotly 6 raises on vectorbt's `scattermapbox`
    reference, and a module with no plotting in it took down the whole test
    collection on CI while passing on a laptop with an older pin. Asserted in a
    subprocess because `sys.modules` is shared — another test importing
    vectorbt first would make this pass for the wrong reason.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import tradingagents_us.backtest.exit_paths; "
            "sys.exit(1 if 'vectorbt' in sys.modules else 0)",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"vectorbt was imported: {result.stderr}"


# --- the ambiguity this module exists to refuse ------------------------------


def test_a_bar_spanning_both_levels_is_ambiguous_not_a_guess() -> None:
    outcome, price, gapped, reason = resolve_bar(
        _bar(date(2024, 3, 4), 100.0, 111.0, 94.0, 105.0), 95.0, 110.0
    )
    assert outcome == "ambiguous"
    assert price is None and gapped is False
    assert "cannot say which filled first" in reason


def test_ambiguous_positions_are_excluded_from_the_buckets_and_counted() -> None:
    entry = _entry()
    bars = [_bar(date(2024, 3, 4), 100.0, 111.0, 94.0, 105.0)]
    sims = [simulate_exit(entry, bars)]

    assert sims[0].outcome == "ambiguous"
    assert sims[0].realized_pnl is None
    # The bucket view must not see it at all...
    assert attributed(sims) == []
    assert bucket_by_exit(attributed(sims)) == []
    # ...and the census must, or the reader cannot tell an empty split from a
    # split that quietly dropped everything.
    counts = census(sims)
    assert (counts.simulated, counts.resolved, counts.ambiguous) == (1, 0, 1)
    assert counts.ambiguous_share == 1.0


def test_ambiguous_share_ignores_positions_still_open() -> None:
    # One ambiguous, one resolved, one never closed: the share is 1/2, not 1/3.
    sims = [
        simulate_exit(_entry(trade_id="a"), [_bar(date(2024, 3, 4), 100.0, 111.0, 94.0, 105.0)]),
        simulate_exit(_entry(trade_id="b"), [_bar(date(2024, 3, 4), 100.0, 111.0, 99.0, 110.5)]),
        simulate_exit(_entry(trade_id="c"), [_bar(date(2024, 3, 4), 100.0, 101.0, 99.0, 100.5)]),
    ]
    counts = census(sims)
    assert (counts.resolved, counts.ambiguous, counts.still_open) == (1, 1, 1)
    assert counts.ambiguous_share == 0.5


def test_a_same_day_decision_sell_loses_to_a_level_touch_as_ambiguous() -> None:
    # The legs rest all session; the decision sell is submitted once. A day
    # that did both is an ordering the bar cannot supply.
    outcome, _, _, _ = resolve_bar(
        _bar(date(2024, 3, 4), 100.0, 111.0, 94.0, 105.0), 95.0, 110.0, decision_today=True
    )
    assert outcome == "ambiguous"


# --- gaps must not fill at the level ----------------------------------------


def test_a_gap_through_the_stop_fills_at_the_open_not_the_stop() -> None:
    outcome, price, gapped, _ = resolve_bar(
        _bar(date(2024, 3, 4), 92.0, 93.0, 90.0, 91.0), 95.0, 110.0
    )
    assert outcome == "stop"
    assert price == 92.0  # not 95.0 — the $3/share the strategy never had
    assert gapped is True


def test_a_gap_through_the_target_fills_at_the_open_which_is_better() -> None:
    outcome, price, gapped, _ = resolve_bar(
        _bar(date(2024, 3, 4), 114.0, 115.0, 113.0, 114.5), 95.0, 110.0
    )
    assert outcome == "take_profit"
    assert price == 114.0
    assert gapped is True


def test_gapped_exits_are_counted_so_the_cost_is_visible() -> None:
    sims = [
        simulate_exit(_entry(trade_id="g"), [_bar(date(2024, 3, 4), 92.0, 93.0, 90.0, 91.0)]),
        simulate_exit(_entry(trade_id="n"), [_bar(date(2024, 3, 4), 100.0, 111.0, 99.0, 110.5)]),
    ]
    assert census(sims).gapped == 1
    # And the gap really did cost: −$80 rather than the −$50 the level implies.
    gapped = next(s for s in sims if s.entry.trade_id == "g")
    assert gapped.realized_pnl == pytest.approx(-80.0)


# --- the entry bar is not evidence ------------------------------------------


def test_the_entry_bar_is_not_scanned() -> None:
    # A bar that would stop us out, dated the day of the fill: its low may have
    # come before the position existed.
    entry = _entry()
    sim = simulate_exit(entry, [_bar(ENTRY_DAY, 100.0, 101.0, 90.0, 96.0)])
    assert sim.outcome == "open"


def test_scanning_resumes_the_very_next_bar() -> None:
    entry = _entry()
    sim = simulate_exit(
        entry,
        [
            _bar(ENTRY_DAY, 100.0, 101.0, 90.0, 96.0),
            _bar(date(2024, 3, 4), 96.0, 97.0, 94.0, 94.5),
        ],
    )
    assert (sim.outcome, sim.exit_price, sim.exit_day) == ("stop", 95.0, date(2024, 3, 4))


# --- ordinary paths ----------------------------------------------------------


def test_stop_and_target_fill_at_their_levels_when_the_bar_only_touches_one() -> None:
    stopped = simulate_exit(_entry(), [_bar(date(2024, 3, 4), 99.0, 99.5, 94.0, 94.5)])
    assert (stopped.outcome, stopped.exit_price, stopped.gapped) == ("stop", 95.0, False)
    assert stopped.realized_pnl == pytest.approx(-50.0)

    hit = simulate_exit(_entry(), [_bar(date(2024, 3, 4), 101.0, 111.0, 100.5, 110.5)])
    assert (hit.outcome, hit.exit_price, hit.gapped) == ("take_profit", 110.0, False)
    assert hit.realized_pnl == pytest.approx(100.0)


def test_a_decision_sell_fills_at_the_close_on_its_day() -> None:
    entry = _entry(decision_exit_day=date(2024, 3, 5))
    sim = simulate_exit(
        entry,
        [
            _bar(date(2024, 3, 4), 100.0, 101.0, 99.0, 100.5),
            _bar(date(2024, 3, 5), 101.0, 102.0, 100.0, 101.5),
        ],
    )
    assert (sim.outcome, sim.exit_price) == ("decision_sell", 101.5)
    assert sim.holding_days == 4.0


def test_a_position_with_no_levels_runs_to_its_decision_sell() -> None:
    entry = _entry(
        stop_price=None, take_profit_price=None, decision_exit_day=date(2024, 3, 6)
    )
    bars = [
        _bar(date(2024, 3, 4), 100.0, 130.0, 70.0, 120.0),
        _bar(date(2024, 3, 6), 120.0, 121.0, 119.0, 120.5),
    ]
    assert simulate_exit(entry, bars).outcome == "decision_sell"


def test_a_symbol_with_no_bars_is_open_rather_than_dropped() -> None:
    sims = simulate_all([_entry(symbol="NOPE")], {})
    assert len(sims) == 1 and sims[0].outcome == "open"
    assert census(sims).simulated == 1


# --- feeding exit_quality's roll-ups unchanged -------------------------------


def test_resolved_exits_score_through_exit_quality_not_a_second_copy() -> None:
    bars = {
        "AAPL": [_bar(date(2024, 3, 4), 99.0, 99.5, 94.0, 94.5)],
        "MSFT": [_bar(date(2024, 3, 4), 101.0, 111.0, 100.5, 110.5)],
    }
    sims = simulate_all(
        [_entry(trade_id="a"), _entry(trade_id="b", symbol="MSFT")], bars
    )
    rows = attributed(sims)
    buckets = {b.exit_class: b for b in bucket_by_exit(rows)}

    assert buckets["stop"].trades == 1
    assert buckets["stop"].net_pnl == pytest.approx(-50.0)
    assert buckets["take_profit"].trades == 1
    assert buckets["take_profit"].net_pnl == pytest.approx(100.0)

    # The roll-up the live ledger is read on: strategy exits only.
    strategy = strategy_bucket(rows)
    assert strategy.trades == 2
    assert strategy.net_pnl == pytest.approx(50.0)
    assert strategy.win_rate == pytest.approx(0.5)


def test_a_backtest_never_produces_flatten_or_unknown() -> None:
    # There is no operator and no broker to prune an order, so those two of
    # exit_quality's five classes cannot appear. It is what makes a replayed
    # strategy bucket comparable to a live one.
    bars = {"AAPL": [_bar(date(2024, 3, 4), 99.0, 99.5, 94.0, 94.5)]}
    classes = {b.exit_class for b in bucket_by_exit(attributed(simulate_all([_entry()], bars)))}
    assert classes.isdisjoint({"flatten", "unknown"})


def test_sample_shortfall_keeps_paths_that_are_already_big_enough() -> None:
    bars = {"AAPL": [_bar(date(2024, 3, 4), 99.0, 99.5, 94.0, 94.5)]}
    rows = attributed(simulate_all([_entry(trade_id=f"t{i}") for i in range(30)], bars))
    short = sample_shortfall(rows, minimum=30)
    # Present and satisfied, rather than absent — "fine" must not look like
    # "never happened", which is how the live n=8 split was misread.
    assert short["stop"] == 0
    assert short["take_profit"] == 30
    assert short["decision_sell"] == 30


# --- the bracket's real scoreboard -------------------------------------------


def test_level_mix_reads_the_payoff_off_the_fills_not_the_levels() -> None:
    # One target at the level (+$100), one stop that gapped to 92 (−$80)
    # instead of the −$50 the level implied. The realised payoff must reflect
    # the gap, because the gap is the risk the level pretends is not there.
    sims = [
        simulate_exit(_entry(trade_id="a"), [_bar(date(2024, 3, 4), 101.0, 111.0, 100.5, 110.5)]),
        simulate_exit(_entry(trade_id="b"), [_bar(date(2024, 3, 4), 92.0, 93.0, 90.0, 91.0)]),
    ]
    mix = level_mix(sims, nominal_payoff=2.0)
    assert (mix.target_exits, mix.stop_exits) == (1, 1)
    assert mix.avg_win == pytest.approx(100.0)
    assert mix.avg_loss == pytest.approx(80.0)
    assert mix.payoff_ratio == pytest.approx(1.25)
    assert mix.nominal_payoff == 2.0  # what was asked for, kept for the contrast
    assert mix.breakeven_hit_rate == pytest.approx(1 / 2.25)
    assert mix.hit_rate == pytest.approx(0.5)
    assert mix.edge == pytest.approx(0.5 - 1 / 2.25)


def test_level_mix_ignores_decision_sells() -> None:
    # The bracket is being scored, not the agent changing its mind.
    entry = _entry(trade_id="d", decision_exit_day=date(2024, 3, 4))
    sims = [simulate_exit(entry, [_bar(date(2024, 3, 4), 100.0, 101.0, 99.0, 100.5)])]
    assert sims[0].outcome == "decision_sell"
    mix = level_mix(sims)
    assert (mix.target_exits, mix.stop_exits) == (0, 0)


def test_level_mix_has_no_coin_to_read_when_nothing_reached_a_level() -> None:
    sims = [simulate_exit(_entry(), [_bar(date(2024, 3, 4), 100.0, 101.0, 99.0, 100.5)])]
    mix = level_mix(sims)
    # None, not 0.0: "no position reached a level" is not "the strategy never
    # hits its target", and a 0% on a money screen would say the second.
    assert mix.hit_rate is None
    assert mix.payoff_ratio is None
    assert mix.breakeven_hit_rate is None
    assert mix.edge is None


# --- signals into positions --------------------------------------------------


def _flat_bars(n: int, start: date = date(2024, 3, 1)) -> list[Bar]:
    """A boring 100.0 series — any exit in a test using these is deliberate."""
    return [
        _bar(date.fromordinal(start.toordinal() + i), 100.0, 100.5, 99.5, 100.0)
        for i in range(n)
    ]


def test_entry_fills_at_the_next_bar_open_not_the_signal_close() -> None:
    bars = _flat_bars(3)
    # The bar after the signal opens at 120: that is the fill, not the 100 close
    # the signal was computed from.
    bars[1] = _bar(bars[1].day, 120.0, 121.0, 119.0, 120.0)
    sims = replay_signals("AAPL", [bars[0].day], bars, stop_pct=0.05, take_profit_pct=0.10)
    assert len(sims) == 1
    assert sims[0].entry.entry_price == 120.0
    assert sims[0].entry.entry_day == bars[1].day


def test_a_signal_on_the_last_bar_is_skipped_rather_than_filled_at_its_own_close() -> None:
    bars = _flat_bars(2)
    assert replay_signals("AAPL", [bars[1].day], bars, 0.05, 0.10) == []


def test_signals_during_an_open_position_do_not_pyramid() -> None:
    bars = _flat_bars(6)
    sims = replay_signals("AAPL", [b.day for b in bars], bars, 0.05, 0.10)
    # Nothing ever reaches a level, so the first position is still on and every
    # later signal is refused.
    assert len(sims) == 1 and sims[0].outcome == "open"


def test_the_symbol_is_free_again_once_a_position_exits() -> None:
    bars = _flat_bars(8)
    # Bar 2 stops the first position out; a later signal must be tradeable.
    bars[2] = _bar(bars[2].day, 100.0, 100.5, 90.0, 94.0)
    sims = replay_signals("AAPL", [bars[0].day, bars[4].day], bars, 0.05, 0.10)
    assert [s.outcome for s in sims] == ["stop", "open"]


def test_an_ambiguous_exit_frees_the_symbol_because_the_position_did_close() -> None:
    bars = _flat_bars(8)
    # Both levels touched on bar 2: which leg filled is unknown, that it closed
    # is not.
    bars[2] = _bar(bars[2].day, 100.0, 115.0, 90.0, 100.0)
    sims = replay_signals("AAPL", [bars[0].day, bars[4].day], bars, 0.05, 0.10)
    assert [s.outcome for s in sims] == ["ambiguous", "open"]
    assert census(sims).ambiguous == 1


def test_levels_are_set_relative_to_the_fill() -> None:
    bars = _flat_bars(3)
    bars[1] = _bar(bars[1].day, 200.0, 201.0, 199.0, 200.0)
    entry = replay_signals("AAPL", [bars[0].day], bars, 0.05, 0.10)[0].entry
    assert entry.stop_price == pytest.approx(190.0)
    assert entry.take_profit_price == pytest.approx(220.0)


def test_nonsense_level_fractions_are_refused() -> None:
    bars = _flat_bars(3)
    with pytest.raises(ValueError, match="stop_pct"):
        replay_signals("AAPL", [bars[0].day], bars, stop_pct=1.5, take_profit_pct=0.1)
    with pytest.raises(ValueError, match="take_profit_pct"):
        replay_signals("AAPL", [bars[0].day], bars, stop_pct=0.05, take_profit_pct=0.0)


# --- malformed input is loud -------------------------------------------------


def test_a_stop_at_or_above_the_target_is_rejected_not_reported_as_ambiguous() -> None:
    with pytest.raises(ValueError, match="not below take-profit"):
        _entry(stop_price=115.0)


def test_a_stop_above_the_entry_is_rejected() -> None:
    with pytest.raises(ValueError, match="not below entry"):
        _entry(stop_price=105.0)


def test_a_target_below_the_entry_is_rejected() -> None:
    with pytest.raises(ValueError, match="not above entry"):
        _entry(stop_price=None, take_profit_price=90.0)


def test_an_inverted_bar_is_rejected() -> None:
    with pytest.raises(ValueError, match="below low"):
        _bar(date(2024, 3, 4), 100.0, 90.0, 95.0, 96.0)
