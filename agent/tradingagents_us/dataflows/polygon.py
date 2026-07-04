"""Polygon.io dataflow.

Wraps the Polygon REST API. Survivorship-safe historical via /v2/aggs (delisted
tickers retain their data) and point-in-time index constituents via reference API.

Phase 1: bulk historical OHLCV → S3 parquet.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date
from typing import Literal

import httpx

BASE = "https://api.polygon.io"

Multiplier = int
Timespan = Literal["minute", "hour", "day", "week", "month"]


@dataclass(frozen=True)
class Aggregate:
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None
    transactions: int | None


class PolygonClient:
    """Thin REST client. No rate-limit handling beyond exponential backoff.

    Stocks Starter plan: 5 req/min unlimited monthly. For bulk we sleep 12s
    between paginated calls.
    """

    def __init__(self, api_key: str | None = None, timeout_s: float = 30.0) -> None:
        self.api_key = api_key or os.environ["POLYGON_API_KEY"]
        self._http = httpx.Client(timeout=timeout_s, base_url=BASE)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PolygonClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --------------------------- aggregates ---------------------------

    def aggregates(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
        multiplier: Multiplier = 1,
        timespan: Timespan = "day",
        adjusted: bool = True,
        limit: int = 50_000,
    ) -> list[Aggregate]:
        """Fetch OHLCV bars. Adjusted=True applies SPLIT adjustments only —
        Polygon does not dividend-adjust aggregates. For total return, add
        cash dividends from dividends() on top (see eval_report._spy_return)."""
        path = (
            f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/"
            f"{from_date.isoformat()}/{to_date.isoformat()}"
        )
        params = {
            "adjusted": str(adjusted).lower(),
            "sort": "asc",
            "limit": limit,
            "apiKey": self.api_key,
        }
        bars: list[Aggregate] = []
        next_url: str | None = None
        while True:
            resp = self._get(next_url or path, params if not next_url else {})
            for row in resp.get("results", []) or []:
                bars.append(
                    Aggregate(
                        timestamp_ms=row["t"],
                        open=row["o"],
                        high=row["h"],
                        low=row["l"],
                        close=row["c"],
                        volume=row["v"],
                        vwap=row.get("vw"),
                        transactions=row.get("n"),
                    )
                )
            next_url = resp.get("next_url")
            if not next_url:
                break
            # next_url is full URL; reset params and add apiKey via query
            if "apiKey=" not in next_url:
                next_url += ("&" if "?" in next_url else "?") + f"apiKey={self.api_key}"
            time.sleep(12.0)  # 5 req/min plan
        return bars

    def previous_close(self, ticker: str) -> dict:
        """Quick sanity-check endpoint."""
        return self._get(f"/v2/aggs/ticker/{ticker}/prev", {"apiKey": self.api_key})

    # --------------------------- reference ----------------------------

    def list_tickers(
        self,
        market: Literal["stocks", "crypto", "fx", "otc", "indices"] = "stocks",
        active: bool = True,
        limit: int = 1000,
    ) -> list[dict]:
        """List tickers. Active=False includes delisted (survivorship-safe testing)."""
        params = {
            "market": market,
            "active": str(active).lower(),
            "limit": limit,
            "apiKey": self.api_key,
        }
        out: list[dict] = []
        next_url: str | None = None
        while True:
            resp = self._get(next_url or "/v3/reference/tickers", params if not next_url else {})
            out.extend(resp.get("results", []) or [])
            next_url = resp.get("next_url")
            if not next_url:
                break
            if "apiKey=" not in next_url:
                next_url += ("&" if "?" in next_url else "?") + f"apiKey={self.api_key}"
            time.sleep(12.0)
        return out

    def ticker_details(self, ticker: str, as_of: date | None = None) -> dict:
        """Point-in-time company details (sector, market cap, share count)."""
        params: dict[str, str | int] = {"apiKey": self.api_key}
        if as_of:
            params["date"] = as_of.isoformat()
        return self._get(f"/v3/reference/tickers/{ticker}", params)

    def dividends(self, ticker: str, start: date, end: date) -> list[dict]:
        """Cash dividends with an ex-date inside [start, end].

        Used to turn a price return into a total return (e.g. the SPY
        benchmark in the eval scorecard). Returns the raw result dicts —
        callers usually only need `cash_amount`."""
        params: dict[str, str | int] = {
            "ticker": ticker,
            "ex_dividend_date.gte": start.isoformat(),
            "ex_dividend_date.lte": end.isoformat(),
            "limit": 50,
            "apiKey": self.api_key,
        }
        resp = self._get("/v3/reference/dividends", params)
        return resp.get("results", []) or []

    # ----------------------------- http -------------------------------

    def _get(self, url_or_path: str, params: dict) -> dict:
        """GET with exponential backoff on 429/5xx."""
        is_absolute = url_or_path.startswith("http")
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                if is_absolute:
                    r = self._http.get(url_or_path)
                else:
                    r = self._http.get(url_or_path, params=params)
                if r.status_code == 429:
                    time.sleep(2 ** attempt * 2.0)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError as e:
                last_err = e
                time.sleep(2 ** attempt)
        raise RuntimeError(f"polygon request failed: {url_or_path} — {last_err}")
