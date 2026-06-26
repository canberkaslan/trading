"""Historical OHLCV loader for backtests.

yfinance — free and deep (decades), good enough for deterministic-strategy
backtests. NOTE: pulling a *fixed current* universe is survivorship-biased
(these names all survived); results are optimistic and validate mechanics
more than edge. A survivorship-safe run needs delisted-inclusive data
(Polygon historical / FNSPID) — tracked as a follow-up.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf


def load_ohlcv(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Return {field: DataFrame[date x ticker]} for close/high/low/open/volume.

    Adjusted prices (splits + dividends) so total return is captured."""
    raw = yf.download(
        tickers, start=start, end=end, interval="1d",
        progress=False, auto_adjust=True, group_by="column",
    )
    fields = {}
    for field in ("Close", "High", "Low", "Open", "Volume"):
        if field in raw.columns.get_level_values(0):
            df = raw[field].copy()
        else:  # single ticker -> flat columns
            df = raw[[field]].copy()
            df.columns = tickers
        fields[field.lower()] = df.dropna(how="all")
    return fields


# Market-regime windows for stress-testing across conditions.
REGIMES: dict[str, tuple[str, str]] = {
    "full 2018-2025": ("2018-01-01", "2025-12-31"),
    "COVID crash 2020": ("2020-01-01", "2020-06-30"),
    "2022 bear": ("2022-01-01", "2022-12-31"),
    "2023-24 bull": ("2023-01-01", "2024-12-31"),
}
