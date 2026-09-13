"""Pre-council affordability gate — skip the impossible, never the unlikely."""

from __future__ import annotations

from tradingagents_us.risk.precouncil import should_council


class TestHeldNamesAreNeverSkipped:
    """The hard invariant. The council is the only discretionary exit."""

    def test_a_held_name_runs_even_with_no_cash(self) -> None:
        g = should_council("AAPL", held_qty=33, spendable=0.0, price=332.0)
        assert g.run is True
        assert "exit path" in g.reason

    def test_a_held_name_runs_even_when_open_buys_are_unpriceable(self) -> None:
        # The one state where new exposure is refused outright — a holder still
        # needs the chance to be told to sell.
        g = should_council("AAPL", held_qty=33, spendable=None, price=332.0)
        assert g.run is True

    def test_a_held_name_runs_even_with_no_price(self) -> None:
        assert should_council("AAPL", held_qty=1, spendable=0.0, price=None).run is True

    def test_a_fractional_holding_still_counts_as_held(self) -> None:
        assert should_council("AAPL", held_qty=0.5, spendable=0.0, price=1e9).run is True


class TestProvablyImpossibleBuysAreSkipped:
    def test_cannot_afford_a_single_share(self) -> None:
        g = should_council("BRK.A", held_qty=0, spendable=906.32, price=712_000.0)
        assert g.run is False
        assert "arithmetically possible" in g.reason

    def test_unpriceable_open_buys_skip_new_names(self) -> None:
        g = should_council("NVDA", held_qty=0, spendable=None, price=180.0)
        assert g.run is False
        assert "already refused" in g.reason

    def test_zero_spendable_skips(self) -> None:
        assert should_council("NVDA", held_qty=0, spendable=0.0, price=180.0).run is False

    def test_cash_utilization_tightens_the_budget(self) -> None:
        # At 1.0 the name is affordable; at 0.5 the budget genuinely is not
        # enough for one share, and that is still an arithmetic fact.
        assert should_council("X", held_qty=0, spendable=200.0, price=150.0).run is True
        g = should_council("X", held_qty=0, spendable=200.0, price=150.0, max_cash_utilization=0.5)
        assert g.run is False


class TestUncertaintyNeverSkips:
    """A gate that guesses would shrink the universe for non-book reasons."""

    def test_an_unknown_price_evaluates(self) -> None:
        g = should_council("NEWCO", held_qty=0, spendable=10_000.0, price=None)
        assert g.run is True
        assert "cannot prove unaffordable" in g.reason

    def test_a_nonsense_price_evaluates_rather_than_skipping(self) -> None:
        # A zero or negative close is bad data, not a free share.
        assert should_council("X", held_qty=0, spendable=10_000.0, price=0.0).run is True
        assert should_council("X", held_qty=0, spendable=10_000.0, price=-5.0).run is True


class TestOnlyImpossibilityIsSkipped:
    def test_a_small_but_possible_position_is_NOT_skipped(self) -> None:
        # Exactly one share is affordable. Whether a one-share position is worth
        # holding is the sizer's judgement, made with the council's output —
        # skipping here would be a strategy change disguised as a saving.
        g = should_council("MSFT", held_qty=0, spendable=520.0, price=519.99)
        assert g.run is True

    def test_the_boundary_is_exact_not_approximate(self) -> None:
        assert should_council("X", held_qty=0, spendable=100.0, price=100.0).run is True
        assert should_council("X", held_qty=0, spendable=99.99, price=100.0).run is False

    def test_negative_spendable_is_treated_as_zero_not_as_a_credit(self) -> None:
        g = should_council("X", held_qty=0, spendable=-500.0, price=10.0)
        assert g.run is False


class TestReasonIsAlwaysUsable:
    def test_every_outcome_carries_a_non_empty_reason(self) -> None:
        # A skipped ticker writes no decision row, so this string is the only
        # trace it was considered. An empty one makes a gate bug invisible.
        cases = [
            dict(held_qty=1, spendable=0.0, price=1.0),
            dict(held_qty=0, spendable=None, price=1.0),
            dict(held_qty=0, spendable=0.0, price=1.0),
            dict(held_qty=0, spendable=100.0, price=None),
            dict(held_qty=0, spendable=100.0, price=1.0),
        ]
        for kw in cases:
            g = should_council("T", **kw)  # type: ignore[arg-type]
            assert g.reason.strip()
