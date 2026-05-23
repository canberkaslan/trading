"""Survivorship-safe historical S&P 500 constituents.

Scrapes Wikipedia "List of S&P 500 companies" which has two tables:
  - Current constituents (today's index)
  - Selected changes (additions + removals with dates)

Composes a point-in-time membership function: `members_as_of(date)`.

Why we need this: yfinance / Polygon "S&P 500" returns *current* tickers.
A 2015–2025 backtest on current S&P 500 is survivorship-biased — companies
that went bankrupt (Lehman 2008, WaMu 2008, Bear Stearns 2008, etc.) are
excluded entirely. Our scraper reconstructs the historical membership so
backtests are honest.

Sources:
  - https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
  - Polygon ticker_details(as_of=...) for delisted ticker survival metadata
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from io import StringIO
from typing import Iterator

import httpx
import pandas as pd

log = logging.getLogger(__name__)

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = "Trading Research (https://github.com/canberkaslan/trading)"


@dataclass(frozen=True)
class IndexChange:
    effective_date: date
    added: str | None       # ticker added that day
    removed: str | None     # ticker removed that day
    added_name: str | None = None
    removed_name: str | None = None
    reason: str | None = None


def _fetch_html() -> str:
    r = httpx.get(WIKI_URL, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.text


def _parse_date_safe(raw: str | None) -> date | None:
    if not raw or pd.isna(raw):
        return None
    raw = str(raw).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    # Sometimes formatted as "January, 1976" or just a year — return None
    return None


def fetch_current_constituents() -> pd.DataFrame:
    """Return a DataFrame of current S&P 500 constituents.

    Columns: symbol, security, gics_sector, gics_sub_industry, headquarters,
             date_added, cik, founded
    """
    html = _fetch_html()
    tables = pd.read_html(StringIO(html))
    # Table 0 is current constituents
    df = tables[0].copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    # Common columns: symbol, security, gics_sector, ...
    if "symbol" not in df.columns:
        # Older wiki versions used "ticker symbol"
        for c in df.columns:
            if "symbol" in c or "ticker" in c:
                df = df.rename(columns={c: "symbol"})
                break
    df["symbol"] = df["symbol"].astype(str).str.replace(".", "-", regex=False)
    return df


def fetch_changes() -> list[IndexChange]:
    """Return historical additions/removals from the 'Selected changes' table."""
    html = _fetch_html()
    tables = pd.read_html(StringIO(html))
    # Table 1 is typically the changes table; defensive search
    changes_df: pd.DataFrame | None = None
    for tbl in tables[1:]:
        cols_str = " ".join(str(c).lower() for c in tbl.columns)
        if "added" in cols_str and "removed" in cols_str:
            changes_df = tbl
            break
    if changes_df is None:
        log.warning("could not locate changes table in Wikipedia article")
        return []

    # MultiIndex columns flatten: ('Date', '') / ('Added', 'Ticker') etc.
    if isinstance(changes_df.columns, pd.MultiIndex):
        new_cols = []
        for top, sub in changes_df.columns:
            top_s = str(top).strip().lower().replace(" ", "_")
            sub_s = str(sub).strip().lower().replace(" ", "_")
            if sub_s and sub_s != top_s and sub_s != "nan":
                new_cols.append(f"{top_s}_{sub_s}")
            else:
                new_cols.append(top_s)
        changes_df.columns = new_cols
    else:
        changes_df.columns = [str(c).strip().lower().replace(" ", "_") for c in changes_df.columns]

    # Heuristic column resolution (Wiki layout drifts)
    def col(*candidates: str) -> str | None:
        for c in candidates:
            if c in changes_df.columns:
                return c
        return None

    c_date = col("date", "effective_date")
    c_added_t = col("added_ticker", "added_added_ticker", "added", "added_symbol")
    c_added_n = col("added_security", "added_name", "added_company")
    c_rem_t = col("removed_ticker", "removed_removed_ticker", "removed", "removed_symbol")
    c_rem_n = col("removed_security", "removed_name", "removed_company")
    c_reason = col("reason", "reason_for_change")

    out: list[IndexChange] = []
    for _, row in changes_df.iterrows():
        d = _parse_date_safe(row.get(c_date)) if c_date else None
        if not d:
            continue
        out.append(
            IndexChange(
                effective_date=d,
                added=_clean_ticker(row.get(c_added_t) if c_added_t else None),
                removed=_clean_ticker(row.get(c_rem_t) if c_rem_t else None),
                added_name=_clean_str(row.get(c_added_n) if c_added_n else None),
                removed_name=_clean_str(row.get(c_rem_n) if c_rem_n else None),
                reason=_clean_str(row.get(c_reason) if c_reason else None),
            )
        )
    out.sort(key=lambda c: c.effective_date)
    return out


def _clean_ticker(raw: object) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "—", "-", "n/a"):
        return None
    # Yahoo/Polygon use "-" instead of "." for class shares (BRK.B -> BRK-B)
    return re.sub(r"[^\w\-]", "", s).upper().replace(".", "-")


def _clean_str(raw: object) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip()
    return s if s and s.lower() not in ("nan",) else None


def members_as_of(as_of: date, changes: list[IndexChange] | None = None, current: pd.DataFrame | None = None) -> set[str]:
    """Reconstruct the S&P 500 constituent set on a given historical date.

    Algorithm:
      1. Start from today's constituents.
      2. Walk changes BACKWARDS from today to `as_of`, undoing each change:
           change.added (today) means that ticker was added later -> remove it
           change.removed (today) means that ticker was removed later -> add it back
    """
    current_df = current if current is not None else fetch_current_constituents()
    ch = changes if changes is not None else fetch_changes()

    members = set(current_df["symbol"].dropna().astype(str).tolist())

    # Walk backwards from today to as_of
    for change in reversed(ch):
        if change.effective_date <= as_of:
            break
        if change.added:
            members.discard(change.added)
        if change.removed:
            members.add(change.removed)
    return members


def iter_universe_changes(start: date, end: date) -> Iterator[tuple[date, set[str]]]:
    """Yield (date, members_set) for every change date in [start, end].

    Use this to back-test a strategy against a moving membership set.
    """
    current = fetch_current_constituents()
    changes = fetch_changes()
    seen: set[date] = set()
    for c in changes:
        if start <= c.effective_date <= end and c.effective_date not in seen:
            seen.add(c.effective_date)
            yield c.effective_date, members_as_of(c.effective_date, changes, current)
