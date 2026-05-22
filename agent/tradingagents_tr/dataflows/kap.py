"""KAP (Kamuyu Aydınlatma Platformu) — public disclosure scraper.

KAP is the TR equivalent of SEC EDGAR. Wraps `pykap` PyPI for structured
endpoints, falls back to Playwright for dynamic-content pages.

NOTE: This is a Phase 1 stub. Real implementation lands in week 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class KAPDisclosure:
    ticker: str
    disclosure_id: str
    published_at_utc: datetime
    category: str  # e.g. "FinansalRapor", "OzelDurum", "Bedelsiz"
    title: str
    body_text: str
    pdf_url: str | None = None


def fetch_disclosures(
    ticker: str,
    start: date,
    end: date,
    limit: int = 100,
) -> list[KAPDisclosure]:
    """Fetch KAP disclosures for a BIST ticker.

    Args:
        ticker: BIST ticker without ".IS" suffix (we add internally)
        start: inclusive start date
        end: inclusive end date
        limit: max disclosures to return

    Returns:
        List of structured disclosures.

    TODO(phase-1):
        - Wire pykap PyPI package
        - Add Playwright fallback for special-disclosure dynamic pages
        - Cache in S3 / Aurora to avoid rate limits
    """
    raise NotImplementedError("kap.fetch_disclosures — phase 1 stub")


def fetch_financial_statements(ticker: str, year: int, quarter: int) -> dict | None:
    """Fetch quarterly financial statements (bilanço, gelir tablosu, nakit akış).

    Returns None if not yet published.

    TODO(phase-1):
        - Endpoint: KAP financial reports XBRL
        - Parse into normalized schema matching SEC 10-Q structure
    """
    raise NotImplementedError("kap.fetch_financial_statements — phase 1 stub")
