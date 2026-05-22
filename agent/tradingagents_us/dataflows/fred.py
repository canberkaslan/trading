"""FRED (Federal Reserve Economic Data) — free macro indicators.

API key required (free, instant signup at https://fred.stlouisfed.org/docs/api/api_key.html).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

import httpx

BASE = "https://api.stlouisfed.org/fred"

# Series IDs we care about
SERIES = {
    "fed_funds_rate":     "DFF",       # Daily Fed Funds Rate
    "10y_treasury":       "DGS10",     # 10-Year Treasury Yield
    "2y_treasury":        "DGS2",
    "cpi_yoy":            "CPIAUCSL",  # CPI All Urban Consumers
    "unemployment":       "UNRATE",
    "vix":                "VIXCLS",    # CBOE VIX
    "dxy":                "DTWEXBGS",  # USD Index (Broad)
    "wti_oil":            "DCOILWTICO",
    "gold":               "GOLDAMGBD228NLBM",
    "yield_curve":        "T10Y2Y",    # 10Y-2Y spread
}


@dataclass(frozen=True)
class Observation:
    date: date
    value: float | None  # FRED uses "." for missing


class FREDClient:
    def __init__(self, api_key: str | None = None, timeout_s: float = 30.0) -> None:
        self.api_key = api_key or os.environ.get("FRED_API_KEY")
        if not self.api_key:
            raise ValueError("FRED_API_KEY required")
        self._http = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> FREDClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def series(
        self,
        series_id: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[Observation]:
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
        }
        if start:
            params["observation_start"] = start.isoformat()
        if end:
            params["observation_end"] = end.isoformat()
        r = self._http.get(f"{BASE}/series/observations", params=params)
        r.raise_for_status()
        return [
            Observation(
                date=date.fromisoformat(o["date"]),
                value=None if o["value"] == "." else float(o["value"]),
            )
            for o in r.json().get("observations", [])
        ]
