"""Unit tests for position sizing — the risk layer must be deterministic + tested."""

from __future__ import annotations

import pytest

from tradingagents_us.execution.actionability import normalize_reason
from tradingagents_us.risk.position_sizing import (
    apply_portfolio_caps,
    atr_position_size,
    describe_position_cap_trim,
    kelly_fraction,
    position_cap_headroom,
    vol_target_size,
)


class TestKelly:
    def test_fractional_kelly_at_default(self) -> None:
        # 60% win rate, 2:1 payoff → full Kelly = 0.4; quarter = 0.1
        assert kelly_fraction(0.6, 2.0, kelly_mult=0.25) == pytest.approx(0.1)

    def test_kelly_negative_edge_returns_zero(self) -> None:
        # No edge → never bet
        assert kelly_fraction(0.4, 1.0, kelly_mult=0.25) == 0.0

    def test_kelly_rejects_full(self) -> None:
        with pytest.raises(ValueError):
            kelly_fraction(0.6, 2.0, kelly_mult=1.0)

    def test_kelly_rejects_invalid_prob(self) -> None:
        with pytest.raises(ValueError):
            kelly_fraction(0.0, 2.0)
        with pytest.raises(ValueError):
            kelly_fraction(1.0, 2.0)


class TestATR:
    def test_atr_size_within_risk_budget(self) -> None:
        # $100k equity, 0.5% risk = $500. ATR=2, stop=2*ATR=4. Size = 125 shares.
        size = atr_position_size(equity=100_000, atr=2.0, price=50.0)
        assert size == 125

    def test_atr_returns_zero_on_invalid_inputs(self) -> None:
        assert atr_position_size(equity=0, atr=2, price=50) == 0
        assert atr_position_size(equity=100_000, atr=0, price=50) == 0
        assert atr_position_size(equity=100_000, atr=2, price=0) == 0


class TestVolTarget:
    def test_vol_target_basic(self) -> None:
        # 15% target, 30% asset vol → 0.5x weight; 100k * 0.5 / $50 = 1000 shares
        size = vol_target_size(
            equity=100_000, target_annual_vol=0.15, asset_annual_vol=0.30, price=50.0
        )
        assert size == 1000

    def test_vol_target_caps_at_max_weight(self) -> None:
        # 30% target, 15% asset vol would want 2x — capped at 1.0
        size = vol_target_size(
            equity=100_000, target_annual_vol=0.30, asset_annual_vol=0.15,
            price=50.0, max_weight=1.0,
        )
        assert size == 2000  # 100k / $50


class TestPortfolioCaps:
    def test_cap_trims_oversize(self) -> None:
        # Equity 100k, max 10% per name = 10k. Existing 6k. Headroom 4k @ $50 = 80 shares.
        size = apply_portfolio_caps(
            suggested_shares=500,
            price=50.0,
            equity=100_000,
            existing_position_value=6_000,
            max_position_pct=0.10,
        )
        assert size == 80

    def test_cap_returns_suggested_when_under_limit(self) -> None:
        size = apply_portfolio_caps(
            suggested_shares=50,
            price=50.0,
            equity=100_000,
            existing_position_value=0,
            max_position_pct=0.10,
        )
        assert size == 50

    def test_zero_equity_allows_nothing(self) -> None:
        assert (
            apply_portfolio_caps(
                suggested_shares=50, price=50.0, equity=0, existing_position_value=0
            )
            == 0
        )

    def test_unusable_price_allows_nothing(self) -> None:
        assert (
            apply_portfolio_caps(
                suggested_shares=50, price=0.0, equity=100_000, existing_position_value=0
            )
            == 0
        )


class TestPositionCapHeadroom:
    """The explanation must agree with the number `apply_portfolio_caps` returns."""

    def test_headroom_matches_the_cap_it_explains(self) -> None:
        hr = position_cap_headroom(
            price=50.0, equity=100_000, existing_position_value=6_000, max_position_pct=0.10
        )
        assert hr.max_new_shares == 80
        assert hr.headroom_usd == pytest.approx(4_000.0)
        assert hr.current_weight_pct == pytest.approx(0.06)
        assert not hr.at_cap
        assert hr.priceable

    def test_name_at_the_cap_reports_at_cap_not_merely_zero_shares(self) -> None:
        # Existing 10.4% of a 10% cap — the book cannot add to this name at all.
        hr = position_cap_headroom(
            price=309.35, equity=100_000, existing_position_value=10_400, max_position_pct=0.10
        )
        assert hr.at_cap
        assert hr.max_new_shares == 0
        assert hr.headroom_usd == 0.0
        assert hr.current_weight_pct == pytest.approx(0.104)

    def test_headroom_below_one_share_is_not_at_cap(self) -> None:
        # $210 of room against a $309 share: a granularity limit, not saturation.
        # Reporting this as "at cap" would claim a structural freeze that a small
        # move in equity dissolves on its own.
        hr = position_cap_headroom(
            price=309.35, equity=100_000, existing_position_value=9_790, max_position_pct=0.10
        )
        assert hr.max_new_shares == 0
        assert not hr.at_cap
        assert hr.headroom_usd == pytest.approx(210.0)

    def test_unusable_price_is_not_a_cap_verdict(self) -> None:
        hr = position_cap_headroom(
            price=0.0, equity=100_000, existing_position_value=0, max_position_pct=0.10
        )
        assert not hr.priceable
        assert hr.max_new_shares == 0
        assert not hr.at_cap  # room exists; the price is what is unusable

    def test_zero_equity_reports_no_room_without_a_weight(self) -> None:
        hr = position_cap_headroom(price=50.0, equity=0.0, existing_position_value=1_000)
        assert hr.at_cap
        assert hr.max_new_shares == 0
        assert hr.current_weight_pct == 0.0


class TestDescribePositionCapTrim:
    def test_at_cap_names_the_weight_and_the_cap(self) -> None:
        hr = position_cap_headroom(
            price=309.35, equity=100_000, existing_position_value=10_400, max_position_pct=0.10
        )
        text = describe_position_cap_trim("AAPL", 309.35, hr)
        assert text == "AAPL at 10.4% of equity, cap 10.0%, headroom=$0.00"

    def test_sub_share_headroom_says_so(self) -> None:
        hr = position_cap_headroom(
            price=309.35, equity=100_000, existing_position_value=9_790, max_position_pct=0.10
        )
        text = describe_position_cap_trim("AAPL", 309.35, hr)
        assert text == "AAPL headroom=$210.00, cap 10.0%, below 1 share @ $309.35"

    def test_unusable_price_says_price_not_cap(self) -> None:
        hr = position_cap_headroom(price=0.0, equity=100_000, existing_position_value=0)
        assert describe_position_cap_trim("AAPL", 0.0, hr) == "AAPL unusable price=$0.00"

    @pytest.mark.parametrize(
        "existing,price",
        [(10_400.0, 309.35), (9_790.0, 309.35), (0.0, 0.0)],
    )
    def test_detail_never_nests_parentheses(self, existing: float, price: float) -> None:
        # The actionability report strips one trailing `(...)` to bucket reasons.
        # A nested pair would leave a dangling fragment and split one cause across
        # buckets — which is exactly the scatter this detail exists to prevent.
        hr = position_cap_headroom(price=price, equity=100_000, existing_position_value=existing)
        text = describe_position_cap_trim("AAPL", price, hr)
        assert "(" not in text and ")" not in text

    def test_detailed_reason_still_buckets_under_the_original_key(self) -> None:
        hr = position_cap_headroom(
            price=309.35, equity=100_000, existing_position_value=10_400, max_position_pct=0.10
        )
        reason = (
            "trimmed_to_zero_by_portfolio_caps "
            f"({describe_position_cap_trim('AAPL', 309.35, hr)})"
        )
        assert normalize_reason(reason) == "trimmed_to_zero_by_portfolio_caps"
