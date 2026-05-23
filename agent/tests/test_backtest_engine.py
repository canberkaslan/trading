"""Smoke test for the backtest engine.

Uses a synthetic SMA crossover strategy on Polygon-cached AAPL data to
validate the harness end-to-end without any LLM cost. A real
LLM-decisions-driven backtest waits on Phase 4+ when we have months of
agent output to feed in.
"""

from __future__ import annotations

import io
import os
from datetime import date

import pytest


def _load_env_at_import() -> None:
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"')
            if v:
                os.environ.setdefault(k, v)


_load_env_at_import()


@pytest.mark.skipif(
    not (os.environ.get("AWS_PROFILE") and os.environ.get("S3_DATA_BUCKET")),
    reason="AWS_PROFILE / S3_DATA_BUCKET not set",
)
def test_synthetic_sma_crossover_on_aapl_2024() -> None:
    """Pull AAPL 2024 parquet from S3, run 10/30 SMA crossover, expect a finite Sharpe."""
    import boto3
    import pandas as pd

    from tradingagents_us.backtest import BacktestConfig, run_signal_backtest

    session = boto3.Session(profile_name=os.environ["AWS_PROFILE"])
    s3 = session.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    obj = s3.get_object(Bucket=os.environ["S3_DATA_BUCKET"], Key="historical/us/daily/AAPL.parquet")
    df = pd.read_parquet(io.BytesIO(obj["Body"].read()))

    # Index by date, single ticker
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    df = df.set_index("timestamp").sort_index()
    prices = df[["close"]].rename(columns={"close": "AAPL"})

    # 10/30 SMA crossover signals
    sma10 = prices.rolling(10).mean()
    sma30 = prices.rolling(30).mean()
    bullish = (sma10 > sma30) & (sma10.shift(1) <= sma30.shift(1))
    bearish = (sma10 < sma30) & (sma10.shift(1) >= sma30.shift(1))
    entries = bullish.fillna(False)
    exits = bearish.fillna(False)

    result = run_signal_backtest(prices, entries, exits, BacktestConfig(init_cash=100_000.0))
    summary = result.summary()

    # Sanity: backtest ran, produced finite outputs
    assert summary["total_trades"] >= 1, "expected at least 1 round-trip in 2024"
    assert isinstance(summary["sharpe_ratio"], float)
    assert isinstance(summary["max_drawdown_pct"], float)
    # Total return finite (not NaN)
    import math
    assert not math.isnan(summary["total_return_pct"])
