"""The Portfolio Manager's rating must never be invented.

`_parse_pm_output` used to default to "Hold" when the regex missed. Hold is a
TRADEABLE rating — the sizer maps it to no order and the decision log records a
deliberate stand-pat — so a parse failure became indistinguishable from the
model actually saying hold, and the 57 "rating=Hold" refusals in the order-flow
report could not be told apart from parser misses.

Upstream fixed the same class of bug in its own parser (#1170, returning a
REVIEW sentinel), but that fix does not reach this code: pipeline.py re-parses
the PM markdown itself rather than using the vendored SignalProcessor's answer.
"""

from __future__ import annotations

import pytest

from tradingagents_us.graph.pipeline import _parse_pm_output


class TestRating:
    @pytest.mark.parametrize(
        "rating", ["Buy", "Overweight", "Hold", "Underweight", "Sell"]
    )
    def test_reads_each_of_the_five(self, rating: str) -> None:
        got, _, _ = _parse_pm_output(f"**Rating**: {rating}\n**Price Target**: $200")
        assert got == rating

    def test_is_case_insensitive_but_normalises(self) -> None:
        # The schema's Literal is case-sensitive; the model's markdown is not.
        assert _parse_pm_output("**Rating**: bUy")[0] == "Buy"

    def test_refuses_to_invent_a_rating(self) -> None:
        with pytest.raises(ValueError, match="no parseable"):
            _parse_pm_output("The committee could not reach a conclusion today.")

    def test_refuses_on_empty_output(self) -> None:
        with pytest.raises(ValueError):
            _parse_pm_output("")

    def test_refuses_on_a_rating_word_outside_the_five(self) -> None:
        # "Strong Buy" is not one of the five. Silently reading it as Buy would
        # be a different fabrication with the same shape.
        with pytest.raises(ValueError):
            _parse_pm_output("**Rating**: Strong Accumulate")

    def test_a_real_hold_still_parses_as_hold(self) -> None:
        # The point is not to stop Hold; it is to stop Hold arriving by accident.
        assert _parse_pm_output("**Rating**: Hold\nrationale…")[0] == "Hold"


class TestOtherFields:
    def test_price_target_and_horizon(self) -> None:
        _, pt, horizon = _parse_pm_output(
            "**Rating**: Buy\n**Price Target**: $1,234.50\n**Time Horizon**: 3-6 months"
        )
        assert pt == 1234.50
        assert horizon == "3-6 months"

    def test_missing_optional_fields_are_none_not_zero(self) -> None:
        # A missing price target is unknown, not "$0" — the approve screen
        # renders the difference, and zero would read as a real level.
        _, pt, horizon = _parse_pm_output("**Rating**: Hold")
        assert pt is None
        assert horizon is None

    def test_an_unparseable_price_target_does_not_take_the_rating_down(self) -> None:
        rating, pt, _ = _parse_pm_output("**Rating**: Sell\n**Price Target**: n/a")
        assert rating == "Sell"
        assert pt is None
