"""Stop-coverage accounting.

The first test in this file is the one that matters: a fully bracketed position
reads as PROTECTED even though its stop is in status `held` and nested inside a
parent order. Counting it naively — Alpaca's `status=open` filter, no descent into
legs — reports the position as naked, and the consumer of that number places a
second stop on shares that already have one.
"""

from __future__ import annotations

import pytest

from tradingagents_us.risk.stop_coverage import (
    AMBIGUOUS_STATUSES,
    LIVE_STATUSES,
    TERMINAL_STATUSES,
    CoverageReport,
    OrderView,
    PositionView,
    coverage,
    flatten_orders,
)


def order(symbol="AAPL", side="sell", order_type="stop", status="held", qty=10.0, stop=190.0):
    return OrderView(
        symbol=symbol,
        side=side,
        order_type=order_type,
        status=status,
        remaining_qty=qty,
        stop_price=stop,
    )


def long_position(symbol="AAPL", qty=10.0):
    return PositionView(symbol=symbol, qty=qty, side="long")


# --------------------------------------------------------------------------
# the bug this module exists for
# --------------------------------------------------------------------------


def test_held_stop_counts_as_protection():
    """`held` is where a bracket's stop leg rests. It is live protection."""
    report = coverage([long_position(qty=15)], [order(status="held", qty=15)])
    row = report.symbols[0]
    assert row.naked_qty == 0
    assert row.protected_qty == 15
    assert row.is_fully_protected
    assert report.naked_pct == 0.0


def test_held_is_not_accidentally_in_the_terminal_set():
    """A one-line regression guard: moving `held` to terminal would silently
    turn a protected book into a 100%-naked one, and every other test that uses
    a `new` stop would still pass."""
    assert "held" in LIVE_STATUSES
    assert "held" not in TERMINAL_STATUSES
    assert "held" not in AMBIGUOUS_STATUSES


def test_nested_bracket_leg_is_found_by_flatten():
    """The protective stop of a bracket is a CHILD of the entry order."""

    class Parent:
        def __init__(self, order_view, legs):
            self.view = order_view
            self.legs = legs

    stop_leg = order(status="held", qty=15)
    take_profit = order(order_type="limit", status="new", qty=15, stop=None)
    parent = Parent(order(order_type="market", status="filled", qty=15), [stop_leg, take_profit])

    flat = flatten_orders([parent])
    assert stop_leg in flat
    assert take_profit in flat
    assert len(flat) == 3


def test_the_naive_count_would_have_called_this_naked():
    """Pins the contrast directly: filtering to `new` only — what
    `status=open` does — finds no protection on a book that has it."""
    orders = [order(status="held", qty=15)]
    naive = [o for o in orders if o.status == "new"]

    assert coverage([long_position(qty=15)], naive).naked_pct == 100.0
    assert coverage([long_position(qty=15)], orders).naked_pct == 0.0


# --------------------------------------------------------------------------
# the three buckets
# --------------------------------------------------------------------------


def test_partial_coverage_splits_into_protected_and_naked():
    report = coverage([long_position(qty=31)], [order(status="held", qty=2)])
    row = report.symbols[0]
    assert row.protected_qty == 2
    assert row.naked_qty == 29
    assert not row.is_fully_protected
    assert row.is_actionable
    assert report.naked_pct == pytest.approx(29 / 31 * 100)


def test_no_protective_order_at_all_is_fully_naked():
    report = coverage([long_position(qty=33)], [])
    assert report.symbols[0].naked_qty == 33
    assert report.naked_pct == 100.0


def test_buckets_always_sum_to_the_position():
    """protected + naked + indeterminate == held quantity, in every mix."""
    report = coverage(
        [long_position(qty=20)],
        [order(status="held", qty=5), order(status="pending_cancel", qty=8)],
    )
    row = report.symbols[0]
    assert row.protected_qty + row.naked_qty + row.indeterminate_qty == 20
    assert row.indeterminate_qty == 8
    assert row.naked_qty == 7


def test_indeterminate_blocks_action_without_inventing_coverage():
    """An unknown-status order neither protects nor is ignored — and while one
    stands, a backfill must not place orders for that name."""
    report = coverage([long_position(qty=10)], [order(status="pending_cancel", qty=10)])
    row = report.symbols[0]
    assert row.indeterminate_qty == 10
    assert row.naked_qty == 0
    assert not row.is_fully_protected
    assert not row.is_actionable
    assert report.has_indeterminate


def test_unrecognized_status_is_indeterminate_not_protection():
    """A status Alpaca adds tomorrow must not silently read as covered."""
    report = coverage([long_position(qty=10)], [order(status="brand_new_status", qty=10)])
    assert report.symbols[0].indeterminate_qty == 10
    assert report.symbols[0].protected_qty == 0


def test_indeterminate_never_exceeds_what_is_left_unprotected():
    report = coverage(
        [long_position(qty=10)],
        [order(status="held", qty=8), order(status="pending_cancel", qty=10)],
    )
    row = report.symbols[0]
    assert row.indeterminate_qty == 2
    assert row.naked_qty == 0


# --------------------------------------------------------------------------
# things that are not protection
# --------------------------------------------------------------------------


def test_terminal_orders_do_not_protect():
    for status in sorted(TERMINAL_STATUSES):
        report = coverage([long_position(qty=10)], [order(status=status, qty=10)])
        assert report.symbols[0].naked_qty == 10, status


def test_take_profit_limit_is_not_protection():
    """The live account's five open orders are all take-profit limits. A limit
    ABOVE the market caps the upside; it does nothing on the way down."""
    report = coverage(
        [long_position(qty=56)],
        [order(order_type="limit", status="new", qty=56, stop=None)],
    )
    assert report.symbols[0].naked_qty == 56


def test_a_partially_filled_stop_protects_only_the_remainder():
    report = coverage([long_position(qty=15)], [order(status="partially_filled", qty=4)])
    assert report.symbols[0].protected_qty == 4
    assert report.symbols[0].naked_qty == 11


def test_sell_stop_does_not_protect_a_short():
    """A short is stopped out by a BUY. A sell stop under a short is an entry."""
    short = PositionView(symbol="AAPL", qty=10, side="short")
    assert coverage([short], [order(side="sell", status="held")]).symbols[0].naked_qty == 10
    assert coverage([short], [order(side="buy", status="held")]).symbols[0].naked_qty == 0


def test_stop_on_a_different_symbol_does_not_protect():
    report = coverage([long_position("AAPL", 10)], [order(symbol="MSFT", qty=10)])
    assert report.symbols[0].naked_qty == 10


# --------------------------------------------------------------------------
# hazards worth reporting
# --------------------------------------------------------------------------


def test_excess_protection_is_reported():
    """Two stops for 15 on a 15-share lot: when they trigger, the second one
    sells shares that are no longer there. This is what a backfill run twice
    leaves behind, so the report has to be able to see it."""
    report = coverage(
        [long_position(qty=15)],
        [order(status="held", qty=15), order(status="new", qty=15)],
    )
    row = report.symbols[0]
    assert row.excess_qty == 15
    assert row.naked_qty == 0


def test_orphan_stop_with_no_position_under_it():
    report = coverage([long_position("AAPL", 10)], [order(symbol="TSLA", status="held")])
    assert report.orphan_stop_symbols == ("TSLA",)


def test_orphan_list_ignores_non_protective_orders():
    report = coverage(
        [long_position("AAPL", 10)],
        [order(symbol="TSLA", order_type="limit", status="new", stop=None)],
    )
    assert report.orphan_stop_symbols == ()


def test_stop_levels_and_types_are_carried_through():
    report = coverage(
        [long_position(qty=20)],
        [
            order(status="held", qty=10, stop=190.0),
            order(order_type="trailing_stop", status="held", qty=10, stop=185.0),
        ],
    )
    row = report.symbols[0]
    assert set(row.stop_prices) == {190.0, 185.0}
    assert row.stop_types == ("stop", "trailing_stop")


# --------------------------------------------------------------------------
# whole-book arithmetic
# --------------------------------------------------------------------------


def test_empty_book_is_not_an_exposure():
    """A flat account is 0% naked, not 100% — the difference is whether
    someone gets paged at 3am for holding nothing."""
    assert CoverageReport().naked_pct == 0.0
    assert coverage([], []).naked_pct == 0.0


def test_portfolio_totals_match_the_live_book():
    """The 2026-08-25 finding, as a fixture: 339 shares, 74 covered by four
    bracket stop legs, 265 naked = 78.2%."""
    positions = [
        long_position(sym, qty)
        for sym, qty in [
            ("AAPL", 33), ("AMZN", 42), ("GOOGL", 31), ("JPM", 31), ("META", 19),
            ("MSFT", 27), ("NVDA", 55), ("UNH", 15), ("V", 30), ("XOM", 56),
        ]
    ]
    stops = [
        order(symbol="META", status="held", qty=1, stop=505.0),
        order(symbol="GOOGL", status="held", qty=1, stop=310.0),
        order(symbol="GOOGL", status="held", qty=1, stop=314.0),
        order(symbol="XOM", status="held", qty=56, stop=134.0),
        order(symbol="UNH", status="held", qty=15, stop=338.0),
    ]
    report = coverage(positions, stops)

    assert report.total_qty == 339
    assert report.protected_qty == 74
    assert report.naked_qty == 265
    assert report.naked_pct == pytest.approx(78.2, abs=0.1)
    assert not report.has_indeterminate

    by_symbol = {s.symbol: s for s in report.symbols}
    assert by_symbol["XOM"].is_fully_protected
    assert by_symbol["UNH"].is_fully_protected
    assert by_symbol["GOOGL"].naked_qty == 29
    assert by_symbol["META"].naked_qty == 18
    # The six with no stop at all.
    assert [s.symbol for s in report.symbols if s.protected_qty == 0] == [
        "AAPL", "AMZN", "JPM", "MSFT", "NVDA", "V",
    ]


def test_symbols_are_reported_in_stable_order():
    report = coverage([long_position("MSFT", 1), long_position("AAPL", 1)], [])
    assert [s.symbol for s in report.symbols] == ["AAPL", "MSFT"]
