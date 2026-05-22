"""S3 historical bulk loader.

Pull 10 years of daily OHLCV for our universe → S3 parquet, partitioned by ticker.

Usage:
    python -m tradingagents_us.dataflows.bulk_loader \
        --tickers AAPL MSFT NVDA \
        --start 2015-01-01 \
        --end 2025-12-31 \
        --bucket ai-trader-data-dev \
        --prefix historical/us
"""

from __future__ import annotations

import argparse
import io
import logging
import os
from datetime import date

import boto3
import pandas as pd

from .polygon import PolygonClient

log = logging.getLogger(__name__)


# US-only universe — see ADR-004 (Polygon-sourced, survivorship-safe)
US_UNIVERSE = [
    "SPY",
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "BRK.B", "LLY", "V", "JPM", "UNH", "XOM", "MA", "PG",
    "COST", "HD", "JNJ", "ABBV",
]


def aggregates_to_df(bars: list, ticker: str) -> pd.DataFrame:
    """Convert Polygon aggregates to a survivorship-safe parquet schema."""
    rows = [
        {
            "ticker": ticker,
            "timestamp": pd.Timestamp(b.timestamp_ms, unit="ms", tz="UTC"),
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
            "vwap": b.vwap,
            "transactions": b.transactions,
        }
        for b in bars
    ]
    return pd.DataFrame(rows)


def upload_parquet(df: pd.DataFrame, bucket: str, key: str, profile: str | None = None) -> None:
    session = boto3.Session(profile_name=profile or os.environ.get("AWS_PROFILE"))
    s3 = session.client("s3", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    buf = io.BytesIO()
    df.to_parquet(buf, compression="snappy", index=False)
    buf.seek(0)
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())


def fetch_and_upload(
    ticker: str,
    start: date,
    end: date,
    bucket: str,
    prefix: str,
    profile: str | None = None,
) -> int:
    """Download one ticker and upload as parquet. Returns row count."""
    with PolygonClient() as poly:
        bars = poly.aggregates(ticker, start, end)
    if not bars:
        log.warning("no bars for %s", ticker)
        return 0
    df = aggregates_to_df(bars, ticker)
    key = f"{prefix}/{ticker}.parquet"
    upload_parquet(df, bucket, key, profile)
    log.info("uploaded %s rows=%d s3://%s/%s", ticker, len(df), bucket, key)
    return len(df)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", nargs="+", help="Tickers (or --universe us)")
    parser.add_argument("--universe", choices=["us"], help="Predefined universe")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2015, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--bucket", default=os.environ.get("S3_DATA_BUCKET", "ai-trader-data-dev"))
    parser.add_argument("--prefix", default="historical/us/daily")
    parser.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "rootingo"))
    args = parser.parse_args()

    tickers = US_UNIVERSE if args.universe == "us" else (args.tickers or [])
    if not tickers:
        parser.error("provide --tickers or --universe us")

    total = 0
    for t in tickers:
        try:
            total += fetch_and_upload(t, args.start, args.end, args.bucket, args.prefix, args.profile)
        except Exception:
            log.exception("failed: %s", t)
    log.info("done. total rows=%d tickers=%d", total, len(tickers))


if __name__ == "__main__":
    main()
