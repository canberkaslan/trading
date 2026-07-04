"""Sortino/Sharpe convention tests — the eval gate math must be honest."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradingagents_us.risk import metrics


def test_sortino_uses_full_sample_downside_deviation():
    # 3 up days, 1 down day. Correct downside deviation is computed across
    # ALL observations (shortfall^2 mean), not the std of the lone negative.
    r = pd.Series([0.01, 0.02, -0.02, 0.01])
    expected_dd = np.sqrt((np.array([0.0, 0.0, -0.02, 0.0]) ** 2).mean())
    expected = r.mean() / expected_dd * np.sqrt(252)
    assert metrics.sortino(r) == pytest.approx(expected)


def test_sortino_all_positive_is_inf():
    r = pd.Series([0.01, 0.005, 0.02])
    assert metrics.sortino(r) == float("inf")


def test_sortino_empty_is_zero():
    assert metrics.sortino(pd.Series(dtype=float)) == 0.0


def test_sortino_risk_free_reduces_ratio():
    r = pd.Series([0.001, 0.002, -0.001, 0.001] * 10)
    assert metrics.sortino(r, risk_free_rate=0.05) < metrics.sortino(r, risk_free_rate=0.0)


def test_sortino_single_negative_return_not_zero_denominator():
    # Old subset-std implementation returned 0.0 here (std of one point is
    # NaN); the shortfall formulation handles it.
    r = pd.Series([0.01, -0.01, 0.02])
    v = metrics.sortino(r)
    assert np.isfinite(v) and v != 0.0


def test_sharpe_risk_free_reduces_ratio():
    r = pd.Series([0.002, -0.001, 0.003, 0.001] * 15)
    assert metrics.sharpe(r, risk_free_rate=0.05) < metrics.sharpe(r, risk_free_rate=0.0)
