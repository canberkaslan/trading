"""The position-management pass — the half of the risk layer that never ran.

These tests exist because `atr_trailing_stop` and `time_exit` shipped with zero
callers, so nothing has ever proven they behave when wired to real positions.
The safety properties are asserted directly rather than inferred from a happy
path: a widened stop and an exit that opens a position are the two failures that
cost real money, and neither is visible in an "it returned an action" test.
"""

from __future__ import annotations

import pytest

from tradingagents_us.risk.position_manager import (
    ATR_PERIOD,
    Bar,
    ManagedPosition,
    ManagementConfig,
    PlaceStop,
    RatchetStop,
    TimeExit,
    average_true_range,
    plan_actions,
    true_range,
)


def flat_bars(n: int = 30, high: float = 101.0, low: float = 99.0, close: float = 100.0) -> list[Bar]:
    return [Bar(high, low, close) for _ in range(n)]


class TestTrueRange:
    def test_uses_the_widest_of_the_three_spans(self) -> None:
        # Plain intraday range when the gap is small.
        assert true_range(prev_close=100.0, high=102.0, low=99.0) == 3.0

    def test_counts_an_overnight_gap_up(self) -> None:
        # Gapped from 100 to a 105-107 bar: the true range is 7, not 2.
        assert true_range(prev_close=100.0, high=107.0, low=105.0) == 7.0

    def test_counts_an_overnight_gap_down(self) -> None:
        assert true_range(prev_close=100.0, high=95.0, low=93.0) == 7.0


class TestAverageTrueRange:
    def test_none_when_history_is_shorter_than_the_period(self) -> None:
        # Not a partial average: a 3-bar ATR under a 14-bar setting is a
        # different statistic, and it would place the stop far too tight on
        # exactly the names with the least history.
        assert average_true_range(flat_bars(ATR_PERIOD), ATR_PERIOD) is None
        assert average_true_range([], ATR_PERIOD) is None

    def test_computes_once_there_is_exactly_enough(self) -> None:
        assert average_true_range(flat_bars(ATR_PERIOD + 1), ATR_PERIOD) == pytest.approx(2.0)

    def test_constant_range_gives_that_range(self) -> None:
        assert average_true_range(flat_bars(40), ATR_PERIOD) == pytest.approx(2.0)

    def test_smoothing_pulls_toward_a_new_regime_without_jumping_to_it(self) -> None:
        bars = flat_bars(20) + [Bar(120.0, 80.0, 100.0)]
        atr = average_true_range(bars, ATR_PERIOD)
        assert atr is not None
        # One 40-wide bar must move a 2.0 ATR, but nowhere near 40.
        assert 2.0 < atr < 6.0


def position(**kw) -> ManagedPosition:
    base = dict(
        ticker="AAPL",
        quantity=10.0,
        avg_entry_price=100.0,
        current_price=120.0,
        bars_held=5,
        current_stop=90.0,
        stop_order_id="ord_1",
    )
    base.update(kw)
    return ManagedPosition(**base)  # type: ignore[arg-type]


class TestRatchet:
    def test_raises_the_stop_when_price_has_run(self) -> None:
        # ATR 2.0, mult 3.0, close 120 -> candidate 114, well above the 90 stop.
        actions, skips = plan_actions([position()], {"AAPL": flat_bars(30)})
        assert len(actions) == 1
        act = actions[0]
        assert isinstance(act, RatchetStop)
        assert act.old_stop == 90.0
        assert act.new_stop == pytest.approx(114.0)
        assert act.stop_order_id == "ord_1"
        assert skips == []

    def test_never_lowers_a_stop(self) -> None:
        # Price has fallen back to 95; the ATR candidate is 89, below the stop.
        # The stop must stay where it is — widening it is the one edit that
        # turns a risk control into a loss amplifier.
        actions, skips = plan_actions(
            [position(current_price=95.0, current_stop=94.0)], {"AAPL": flat_bars(30)}
        )
        assert actions == []
        assert [s.reason for s in skips] == ["stop_unchanged"]

    def test_ignores_a_ratchet_too_small_to_be_worth_an_order(self) -> None:
        # Candidate is 0.1% above the current stop; the floor is 0.2%.
        actions, skips = plan_actions(
            [position(current_price=120.0, current_stop=113.886)], {"AAPL": flat_bars(30)}
        )
        assert actions == []
        assert [s.reason for s in skips] == ["stop_unchanged"]

    def test_reports_an_unprotected_position_instead_of_placing_an_order(self) -> None:
        # Placing a stop is strictly risk-reducing but it is still an ORDER,
        # and this module does not submit them.
        actions, skips = plan_actions(
            [position(current_stop=None, stop_order_id=None)], {"AAPL": flat_bars(30)}
        )
        assert actions == []
        assert [s.reason for s in skips] == ["no_stop_order"]
        assert "unprotected" in skips[0].detail

    def test_skips_rather_than_guesses_when_history_is_short(self) -> None:
        actions, skips = plan_actions([position()], {"AAPL": flat_bars(4)})
        assert actions == []
        assert [s.reason for s in skips] == ["insufficient_bars"]

    def test_skips_a_ticker_with_no_bars_at_all(self) -> None:
        actions, skips = plan_actions([position()], {})
        assert actions == []
        assert [s.reason for s in skips] == ["insufficient_bars"]


class TestTimeExit:
    def test_closes_a_position_that_has_gone_nowhere(self) -> None:
        actions, _ = plan_actions(
            [position(bars_held=25, current_price=101.0)], {"AAPL": flat_bars(30)}
        )
        assert len(actions) == 1
        act = actions[0]
        assert isinstance(act, TimeExit)
        assert act.quantity == 10.0
        assert act.pnl_pct == pytest.approx(0.01)

    def test_holds_a_position_that_is_still_moving(self) -> None:
        # 25 bars held but +20%: the thesis is playing out, so age is not a
        # reason to close it.
        actions, _ = plan_actions(
            [position(bars_held=25, current_price=120.0)], {"AAPL": flat_bars(30)}
        )
        assert not any(isinstance(a, TimeExit) for a in actions)

    def test_holds_inside_the_window_however_flat(self) -> None:
        actions, skips = plan_actions(
            [position(bars_held=5, current_price=100.0, current_stop=99.9)],
            {"AAPL": flat_bars(30)},
        )
        assert not any(isinstance(a, TimeExit) for a in actions)

    def test_a_time_exit_suppresses_the_ratchet_for_that_position(self) -> None:
        # Emitting both would have the runner replace a stop it is about to
        # cancel by closing the position.
        actions, _ = plan_actions(
            [position(bars_held=25, current_price=101.0, current_stop=50.0)],
            {"AAPL": flat_bars(30)},
        )
        assert len(actions) == 1
        assert isinstance(actions[0], TimeExit)

    def test_closes_a_flat_LOSER_too_not_only_a_flat_winner(self) -> None:
        actions, _ = plan_actions(
            [position(bars_held=25, current_price=98.0)], {"AAPL": flat_bars(30)}
        )
        assert isinstance(actions[0], TimeExit)
        assert actions[0].pnl_pct < 0


class TestSafetyInvariants:
    def test_no_action_ever_opens_or_grows_a_position(self) -> None:
        # The action union has exactly two members, and neither carries a side,
        # a price to buy at, or a quantity that is not the one already held.
        positions = [
            position(bars_held=25, current_price=101.0),
            position(ticker="MSFT", current_price=200.0, avg_entry_price=150.0),
        ]
        actions, _ = plan_actions(
            positions, {"AAPL": flat_bars(30), "MSFT": flat_bars(30)}
        )
        for act in actions:
            if isinstance(act, TimeExit):
                held = next(p for p in positions if p.ticker == act.ticker)
                assert act.quantity == held.quantity
            else:
                assert isinstance(act, RatchetStop)
                assert act.new_stop > act.old_stop

    def test_every_position_is_either_acted_on_or_explained(self) -> None:
        # A guard that silently does nothing is indistinguishable from a guard
        # that is not wired — which is the exact bug this module fixes.
        positions = [
            position(),
            position(ticker="MSFT", current_stop=None, stop_order_id=None),
            position(ticker="XOM", bars_held=25, current_price=100.5),
            position(ticker="V", current_price=0.0),
        ]
        bars = {t: flat_bars(30) for t in ("AAPL", "MSFT", "XOM", "V")}
        actions, skips = plan_actions(positions, bars)
        touched = {a.ticker for a in actions} | {s.ticker for s in skips}
        assert touched == {"AAPL", "MSFT", "XOM", "V"}

    def test_a_zero_price_is_skipped_not_divided_by(self) -> None:
        actions, skips = plan_actions(
            [position(current_price=0.0)], {"AAPL": flat_bars(30)}
        )
        assert actions == []
        assert [s.reason for s in skips] == ["non_positive_price"]

    def test_config_is_honoured_rather_than_hardcoded(self) -> None:
        tight = ManagementConfig(atr_mult=1.0)
        actions, _ = plan_actions([position()], {"AAPL": flat_bars(30)}, tight)
        assert isinstance(actions[0], RatchetStop)
        # close 120 - 2.0 ATR * 1.0 = 118, vs 114 at the default 3.0.
        assert actions[0].new_stop == pytest.approx(118.0)


class TestStopBackfill:
    """Placing protection where there is none.

    Written against a real finding: a coverage run on the live paper book showed
    75.5% of held shares with no protective stop at all — 8 of 10 names naked,
    on an account whose go-live checklist assumes every entry ships a bracket.
    `stop_coverage` could always compute that number and nothing ever ran it,
    and the backfill it describes did not exist.
    """

    def test_reports_rather_than_places_by_default(self) -> None:
        actions, skips = plan_actions(
            [position(current_stop=None, stop_order_id=None, naked_quantity=10.0)],
            {"AAPL": flat_bars(30)},
        )
        assert actions == []
        assert [s.reason for s in skips] == ["no_stop_order"]

    def test_places_when_asked(self) -> None:
        cfg = ManagementConfig(backfill_missing_stops=True)
        actions, _ = plan_actions(
            [position(current_stop=None, stop_order_id=None, naked_quantity=10.0)],
            {"AAPL": flat_bars(30)},
            cfg,
        )
        assert len(actions) == 1
        act = actions[0]
        assert isinstance(act, PlaceStop)
        # close 120 - ATR 2.0 * 3.0 = 114
        assert act.stop_price == pytest.approx(114.0)

    def test_sizes_off_the_naked_quantity_not_the_holding(self) -> None:
        # The single most dangerous mistake available here: re-protecting shares
        # that already have a stop leaves two stops on one lot, and when they
        # both trigger the account is short.
        cfg = ManagementConfig(backfill_missing_stops=True)
        actions, _ = plan_actions(
            [position(quantity=32.0, current_stop=None, stop_order_id=None, naked_quantity=29.0)],
            {"AAPL": flat_bars(30)},
            cfg,
        )
        assert isinstance(actions[0], PlaceStop)
        assert actions[0].quantity == 29.0

    def test_will_not_place_for_a_position_with_nothing_naked(self) -> None:
        cfg = ManagementConfig(backfill_missing_stops=True)
        actions, skips = plan_actions(
            [position(current_stop=None, stop_order_id=None, naked_quantity=0.0)],
            {"AAPL": flat_bars(30)},
            cfg,
        )
        assert actions == []
        assert [s.reason for s in skips] == ["no_stop_order"]

    def test_never_places_a_stop_at_or_above_the_live_price(self) -> None:
        # A stop above the market fires the instant it is accepted — that is a
        # market sell wearing a stop's clothes, not protection.
        cfg = ManagementConfig(backfill_missing_stops=True, atr_mult=0.0)
        actions, skips = plan_actions(
            [position(current_stop=None, stop_order_id=None, naked_quantity=10.0)],
            {"AAPL": flat_bars(30)},
            cfg,
        )
        assert actions == []
        assert [s.reason for s in skips] == ["stop_would_widen"]

    def test_a_wild_atr_cannot_produce_a_negative_stop(self) -> None:
        cfg = ManagementConfig(backfill_missing_stops=True, atr_mult=1000.0)
        actions, skips = plan_actions(
            [position(current_stop=None, stop_order_id=None, naked_quantity=10.0)],
            {"AAPL": flat_bars(30)},
            cfg,
        )
        # Floored at a cent, and a cent is below the price, so it is placeable.
        assert isinstance(actions[0], PlaceStop)
        assert actions[0].stop_price == 0.01

    def test_a_time_exit_still_wins_over_a_backfill(self) -> None:
        cfg = ManagementConfig(backfill_missing_stops=True)
        actions, _ = plan_actions(
            [position(bars_held=25, current_price=101.0, current_stop=None,
                      stop_order_id=None, naked_quantity=10.0)],
            {"AAPL": flat_bars(30)},
            cfg,
        )
        assert len(actions) == 1
        assert isinstance(actions[0], TimeExit)
