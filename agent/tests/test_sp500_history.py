"""Offline tests for the survivor-safe S&P 500 membership reconstruction.

No network: the parser is fed HTML fixtures shaped like the two real Wikipedia
layouts, and the fetch layer is monkeypatched. The live scrape is covered by
tests/test_dataflows_smoke.py::test_sp500_history_survivor_safe.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pandas as pd
import pytest

from tradingagents_us.dataflows import sp500_history as sph
from tradingagents_us.dataflows.sp500_history import (
    IndexChange,
    SP500HistoryUnavailable,
    members_as_of,
    parse_changes,
)

# "Historical components of the S&P 500": two-row header spanning Added/Removed,
# plus a spanning Refs cell that pandas names "Unnamed: N_level_M".
CHANGES_HTML_MULTIINDEX = """
<table class="wikitable">
  <tr>
    <th rowspan="2">Effective Date</th>
    <th colspan="2">Added</th>
    <th colspan="2">Removed</th>
    <th rowspan="2">Reason</th>
    <th rowspan="2">Refs</th>
    <th rowspan="2"></th>
  </tr>
  <tr>
    <th>Ticker</th><th>Security</th><th>Ticker</th><th>Security</th>
  </tr>
  <tr>
    <td>September 16, 2008</td>
    <td>HRS</td><td>Harris Corporation</td>
    <td>LEH</td><td>Lehman Brothers</td>
    <td>Lehman Brothers filed for bankruptcy.</td><td>[263]</td><td></td>
  </tr>
  <tr>
    <td>September 8, 2008</td>
    <td>FLIR</td><td>FLIR Systems</td>
    <td>FNM</td><td>Fannie Mae</td>
    <td>Placed into conservatorship.</td><td>[264]</td><td></td>
  </tr>
  <tr>
    <td>June 30, 2015</td>
    <td>ATVI</td><td>Activision Blizzard</td>
    <td>NE</td><td>Noble Corporation</td>
    <td>Market cap change.</td><td>[100]</td><td></td>
  </tr>
  <tr>
    <td>September 30, 1994</td>
    <td>NBR</td><td>Nabors Industries</td>
    <td>CCB</td><td>Continental Bank</td>
    <td>BankAmerica acquired Continental Bank.</td><td>[287]</td><td></td>
  </tr>
</table>
"""

# The older single-row-header layout, as it lived on the constituents article.
CHANGES_HTML_FLAT = """
<table class="wikitable">
  <tr><th>Date</th><th>Added Ticker</th><th>Removed Ticker</th><th>Reason</th></tr>
  <tr><td>September 16, 2008</td><td>HRS</td><td>LEH</td><td>Bankruptcy.</td></tr>
  <tr><td>June 30, 2015</td><td>ATVI</td><td>NE</td><td>Market cap change.</td></tr>
</table>
"""

# A page carrying a table, but not the one we need (the constituents list).
CONSTITUENTS_HTML = """
<table class="wikitable">
  <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
  <tr><td>MMM</td><td>3M</td><td>Industrials</td></tr>
  <tr><td>AAPL</td><td>Apple Inc.</td><td>Information Technology</td></tr>
</table>
"""


def _current(*symbols: str) -> pd.DataFrame:
    return pd.DataFrame({"symbol": list(symbols)})


# --- parse_changes -----------------------------------------------------------


def test_parses_multiindex_layout() -> None:
    changes = parse_changes(CHANGES_HTML_MULTIINDEX)

    assert [c.effective_date for c in changes] == [
        date(1994, 9, 30),
        date(2008, 9, 8),
        date(2008, 9, 16),
        date(2015, 6, 30),
    ], "changes must come back sorted ascending"
    leh = next(c for c in changes if c.removed == "LEH")
    assert leh.added == "HRS"
    assert leh.removed_name == "Lehman Brothers"
    assert leh.reason == "Lehman Brothers filed for bankruptcy."


def test_parses_flat_single_header_layout() -> None:
    changes = parse_changes(CHANGES_HTML_FLAT)

    assert {c.removed for c in changes} == {"LEH", "NE"}
    assert {c.added for c in changes} == {"HRS", "ATVI"}


def test_returns_empty_when_page_has_no_changes_table() -> None:
    # Empty, not an exception: fetch_changes owns the "nowhere had it" verdict.
    assert parse_changes(CONSTITUENTS_HTML) == []


def test_skips_rows_with_unparseable_dates() -> None:
    html = CHANGES_HTML_FLAT.replace("September 16, 2008", "1976 (approx.)")
    changes = parse_changes(html)

    assert [c.removed for c in changes] == ["NE"]


# --- fetch_changes -----------------------------------------------------------


def test_falls_back_to_the_second_url_when_the_first_lacks_the_table(monkeypatch) -> None:
    seen: list[str] = []

    def fake_fetch(url: str = sph.WIKI_URL) -> str:
        seen.append(url)
        return CONSTITUENTS_HTML if url == sph.CHANGES_URL else CHANGES_HTML_FLAT

    monkeypatch.setattr(sph, "_fetch_html", fake_fetch)
    changes = sph.fetch_changes()

    assert seen == [sph.CHANGES_URL, sph.WIKI_URL]
    assert {c.removed for c in changes} == {"LEH", "NE"}


def test_falls_back_when_the_first_url_is_unreachable(monkeypatch) -> None:
    def fake_fetch(url: str = sph.WIKI_URL) -> str:
        if url == sph.CHANGES_URL:
            raise httpx.ConnectError("boom")
        return CHANGES_HTML_FLAT

    monkeypatch.setattr(sph, "_fetch_html", fake_fetch)

    assert len(sph.fetch_changes()) == 2


def test_raises_when_no_url_yields_a_changes_table(monkeypatch) -> None:
    """The 2026-08 regression: Wikipedia moved the table and we scraped nothing.

    The old code logged a warning and returned [], so every historical universe
    silently became today's index.
    """
    monkeypatch.setattr(sph, "_fetch_html", lambda url=sph.WIKI_URL: CONSTITUENTS_HTML)

    with pytest.raises(SP500HistoryUnavailable) as e:
        sph.fetch_changes()
    # The message has to name both places we looked, or the next person
    # debugging a stale selector starts from zero.
    assert sph.CHANGES_URL in str(e.value)
    assert sph.WIKI_URL in str(e.value)


# --- members_as_of -----------------------------------------------------------


@pytest.fixture()
def changes() -> list[IndexChange]:
    return parse_changes(CHANGES_HTML_MULTIINDEX)


def test_point_in_time_membership_undoes_later_changes(changes) -> None:
    current = _current("AAPL", "HRS", "FLIR", "ATVI")

    m_2007 = members_as_of(date(2007, 12, 31), changes, current)
    assert "LEH" in m_2007 and "FNM" in m_2007
    assert "HRS" not in m_2007, "added in 2008, cannot be a member in 2007"
    assert "AAPL" in m_2007

    m_2010 = members_as_of(date(2010, 12, 31), changes, current)
    assert "LEH" not in m_2010 and "HRS" in m_2010
    assert "NE" in m_2010, "removed in 2015, still a member in 2010"


def test_today_needs_no_reconstruction(changes) -> None:
    current = _current("AAPL", "MSFT")

    assert members_as_of(date.today(), changes, current) == {"AAPL", "MSFT"}
    assert members_as_of(date.today() + timedelta(days=30), changes, current) == {
        "AAPL",
        "MSFT",
    }


def test_refuses_a_date_older_than_the_change_history(changes) -> None:
    # 1990 predates the oldest change, so undoing the whole list still leaves
    # every 1990-to-1994 change unaccounted for — the caller would get a
    # confident-looking universe that nothing in the data supports.
    with pytest.raises(SP500HistoryUnavailable, match="only reaches back to 1994-09-30"):
        members_as_of(date(1990, 1, 1), changes, _current("AAPL"))


def test_refuses_an_empty_change_list_for_a_past_date() -> None:
    with pytest.raises(SP500HistoryUnavailable, match="no index changes available"):
        members_as_of(date(2007, 12, 31), [], _current("AAPL"))


def test_empty_change_list_is_fine_for_today() -> None:
    assert members_as_of(date.today(), [], _current("AAPL")) == {"AAPL"}
