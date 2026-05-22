"""Unit tests for position sizing — the risk layer must be deterministic + tested."""

from __future__ import annotations

import pytest

from tradingagents_tr.risk.position_sizing import (
    apply_portfolio_caps,
    atr_position_size,
    kelly_fraction,
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
        size = vol_target_size(equity=100_000, target_annual_vol=0.15, asset_annual_vol=0.30, price=50.0)
        assert size == 1000

    def test_vol_target_caps_at_max_weight(self) -> None:
        # 30% target, 15% asset vol would want 2x — capped at 1.0
        size = vol_target_size(
            equity=100_000, target_annual_vol=0.30, asset_annual_vol=0.15, price=50.0, max_weight=1.0
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
