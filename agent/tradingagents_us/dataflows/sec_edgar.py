"""SEC EDGAR — official US filings source.

Public, free, no auth. Requires a descriptive User-Agent (SEC policy).
Rate limit: 10 req/sec.

We use the data.sec.gov submissions endpoint for filings index and the
company facts endpoint for normalized financial data.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import httpx

BASE = "https://data.sec.gov"
WWW_BASE = "https://www.sec.gov"


@dataclass(frozen=True)
class Filing:
    cik: str
    accession_no: str
    form: str  # "10-K", "10-Q", "8-K", "4", etc.
    filing_date: date
    primary_document: str
    primary_document_description: str

    @property
    def url(self) -> str:
        acc = self.accession_no.replace("-", "")
        return (
            f"{WWW_BASE}/Archives/edgar/data/{int(self.cik)}/"
            f"{acc}/{self.primary_document}"
        )


FormType = Literal["10-K", "10-Q", "8-K", "4", "DEF 14A", "S-1"]


class EdgarClient:
    """SEC EDGAR client. Rate-limited to 10 req/sec per SEC policy."""

    def __init__(self, user_agent: str | None = None, timeout_s: float = 30.0) -> None:
        ua = user_agent or os.environ.get("SEC_EDGAR_USER_AGENT")
        if not ua:
            raise ValueError("SEC_EDGAR_USER_AGENT required (e.g. 'Name email@example.com')")
        self._http = httpx.Client(
            timeout=timeout_s,
            headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"},
        )
        self._last_call: float = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def ticker_to_cik(self, ticker: str) -> str:
        """Map ticker → 10-digit CIK. SEC ticker file is cached so this is fast."""
        data = self._get(f"{WWW_BASE}/files/company_tickers.json")
        upper = ticker.upper()
        for row in data.values():
            if row.get("ticker") == upper:
                return f"{int(row['cik_str']):010d}"
        raise KeyError(f"ticker not found in SEC tickers: {ticker}")

    def submissions(self, cik: str) -> dict:
        """Filing index for a CIK (most recent 1000 filings)."""
        return self._get(f"{BASE}/submissions/CIK{cik}.json")

    def recent_filings(
        self,
        cik: str,
        forms: tuple[FormType, ...] = ("10-K", "10-Q", "8-K"),
        since: date | None = None,
    ) -> list[Filing]:
        """Return filings of requested forms since a given date."""
        data = self.submissions(cik)
        recent = data["filings"]["recent"]
        accs = recent["accessionNumber"]
        forms_arr = recent["form"]
        dates = recent["filingDate"]
        prim_docs = recent["primaryDocument"]
        prim_descs = recent["primaryDocDescription"]

        out: list[Filing] = []
        for i, form in enumerate(forms_arr):
            if form not in forms:
                continue
            fdate = date.fromisoformat(dates[i])
            if since and fdate < since:
                continue
            out.append(
                Filing(
                    cik=cik,
                    accession_no=accs[i],
                    form=form,
                    filing_date=fdate,
                    primary_document=prim_docs[i],
                    primary_document_description=prim_descs[i],
                )
            )
        return out

    def company_facts(self, cik: str) -> dict:
        """Normalized XBRL financial facts for the company.

        Returns raw structure; downstream code extracts e.g. Revenues, NetIncomeLoss,
        Assets, Liabilities, etc.
        """
        return self._get(f"{BASE}/api/xbrl/companyfacts/CIK{cik}.json")

    # ----------------------------- http -------------------------------

    def _get(self, url: str) -> dict:
        # Enforce 10 req/sec
        delta = time.monotonic() - self._last_call
        if delta < 0.11:
            time.sleep(0.11 - delta)
        for attempt in range(4):
            try:
                r = self._http.get(url)
                self._last_call = time.monotonic()
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                return r.json()
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise RuntimeError(f"edgar request failed: {url}")
