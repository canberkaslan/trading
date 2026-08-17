"""Survivorship-safe historical S&P 500 constituents.

Scrapes Wikipedia for two tables:
  - Current constituents (today's index) — "List of S&P 500 companies"
  - Additions + removals with dates — "Historical components of the S&P 500"

Composes a point-in-time membership function: `members_as_of(date)`.

Why we need this: yfinance / Polygon "S&P 500" returns *current* tickers.
A 2015–2025 backtest on current S&P 500 is survivorship-biased — companies
that went bankrupt (Lehman 2008, WaMu 2008, Bear Stearns 2008, etc.) are
excluded entirely. Our scraper reconstructs the historical membership so
backtests are honest.

The changes table used to live in a "Selected changes" section of the
constituents article; Wikipedia has since split it into its own page. Both
locations are tried, and a missing table is an error rather than an empty
list: silently reconstructing from zero changes hands back *today's* index
labelled as history, which is precisely the survivorship bias this module
exists to remove.

Sources:
  - https://en.wikipedia.org/wiki/List_of_S%26P_500_companies
  - https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500
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
CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
# Ordered by where the additions/removals table currently lives. WIKI_URL stays
# in the list because that is where it lived until Wikipedia split the article.
CHANGES_URLS = (CHANGES_URL, WIKI_URL)
USER_AGENT = "Trading Research (https://github.com/canberkaslan/trading)"


class SP500HistoryUnavailable(RuntimeError):
    """Historical membership could not be sourced (page moved, table gone, network down).

    Raised instead of degrading to "no changes", because a caller that gets an
    empty change list back reconstructs every historical date as today's index.
    """


@dataclass(frozen=True)
class IndexChange:
    effective_date: date
    added: str | None       # ticker added that day
    removed: str | None     # ticker removed that day
    added_name: str | None = None
    removed_name: str | None = None
    reason: str | None = None


def _fetch_html(url: str = WIKI_URL) -> str:
    r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
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
    """Return historical additions/removals, trying each known table location.

    Raises SP500HistoryUnavailable if no page yields a usable changes table.
    """
    problems: list[str] = []
    for url in CHANGES_URLS:
        try:
            html = _fetch_html(url)
        except httpx.HTTPError as e:
            problems.append(f"{url}: fetch failed ({e})")
            continue
        changes = parse_changes(html)
        if changes:
            return changes
        problems.append(f"{url}: no additions/removals rows found")
    raise SP500HistoryUnavailable(
        "S&P 500 change history unavailable — " + "; ".join(problems)
    )


def parse_changes(html: str) -> list[IndexChange]:
    """Parse the additions/removals table out of a Wikipedia article's HTML.

    Returns [] when the page carries no such table — `fetch_changes` decides
    what an empty result means across all candidate pages.
    """
    tables = pd.read_html(StringIO(html))
    # Defensive search: the changes table is not at a fixed index on either page.
    changes_df: pd.DataFrame | None = None
    for tbl in tables:
        cols_str = " ".join(str(c).lower() for c in tbl.columns)
        if "added" in cols_str and "removed" in cols_str:
            changes_df = tbl.copy()
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
            # pandas names spanning/blank header cells "unnamed: N_level_M"
            blank = not sub_s or sub_s == "nan" or sub_s.startswith("unnamed:")
            if not blank and sub_s != top_s:
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


def _assert_covers(changes: list[IndexChange], as_of: date) -> None:
    """Refuse to reconstruct a date the change history cannot reach back to.

    Reconstruction works by undoing every change between `as_of` and today, so
    it is only valid from the oldest change forward. Outside that range the
    walk is a no-op and the caller silently receives today's index — a
    survivorship-biased universe that looks like a real answer.
    """
    if as_of >= date.today():
        return  # nothing to undo; today's constituents are the answer
    if not changes:
        raise SP500HistoryUnavailable(
            f"no index changes available — cannot reconstruct membership as of {as_of}"
        )
    oldest = min(c.effective_date for c in changes)
    if as_of < oldest:
        raise SP500HistoryUnavailable(
            f"change history only reaches back to {oldest}; cannot reconstruct {as_of}"
        )


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
    _assert_covers(ch, as_of)

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
