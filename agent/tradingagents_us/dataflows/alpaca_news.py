"""Alpaca news dataflow — the Benzinga wire, on credentials we already hold.

The news and sentiment analysts both call `get_news`/`get_global_news`, and
both of those are backed by Alpha Vantage, which raises
`AlphaVantageNotConfiguredError` when `ALPHA_VANTAGE_API_KEY` is unset. It is
unset on the box. So two of the four analysts have been deciding with no news
at all — not degraded news, none — while the run looked healthy.

Alpaca serves the Benzinga feed at `data.alpaca.markets/v1beta1/news` under the
same `ALPACA_API_KEY` / `ALPACA_API_SECRET` the broker client already uses. No
additional subscription, no second vendor, nothing to sign up for. That makes
it the cheapest possible way to stop running blind, which is why it lands
before the Alpha Vantage key is chased down.

It does NOT make Alpha Vantage redundant. Alpaca carries headlines and article
summaries; it does not carry Alpha Vantage's per-ticker sentiment score or its
topic taxonomy (fiscal policy, M&A, IPO). When that key arrives the two are
complementary — this one is the wire, that one is the scored digest.

Redistribution note: Benzinga content may be used inside our own application
but article bodies may not be republished. We surface headline + summary to the
LLM and keep the URL for attribution, which is use, not redistribution.

Everything degrades the way `finnhub.py` does: `news_block` never raises, so a
dead feed costs the analysts a paragraph rather than killing the run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

_BASE = "https://data.alpaca.markets/v1beta1"
_TIMEOUT = 10.0

# The wire is noisy and the analyst prompt is not the place to dump a week of
# it. Enough rows to see a theme, few enough to stay a paragraph.
_DEFAULT_LIMIT = 15
_DEFAULT_LOOKBACK_DAYS = 5

# Benzinga tags a single article with every symbol it mentions, and a
# market-wrap piece can carry forty. Past this many the tag list says "this is
# a roundup", not "this is about your ticker", so it is reported as such rather
# than printed in full.
_BROAD_TAG_THRESHOLD = 8


def _creds() -> tuple[str, str] | None:
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET")
    return (key, secret) if key and secret else None


@dataclass(frozen=True)
class Article:
    created_at: str
    headline: str
    summary: str
    source: str
    url: str
    symbols: tuple[str, ...]

    @property
    def is_roundup(self) -> bool:
        """A piece tagged with many symbols is about the market, not a name."""
        return len(self.symbols) > _BROAD_TAG_THRESHOLD


class AlpacaNewsClient:
    """Thin REST client for the Alpaca/Benzinga news endpoint.

    Methods raise on transport errors; `news_block` wraps them for graceful
    degradation, mirroring `FinnhubClient` / `finnhub_block`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout_s: float = _TIMEOUT,
    ) -> None:
        self.api_key = api_key or os.environ["ALPACA_API_KEY"]
        self.api_secret = api_secret or os.environ["ALPACA_API_SECRET"]
        self._http = httpx.Client(
            timeout=timeout_s,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AlpacaNewsClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def news(
        self,
        symbols: str | None = None,
        *,
        limit: int = _DEFAULT_LIMIT,
        lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> list[Article]:
        """Recent articles, newest first.

        `symbols` is a comma-separated list; omitting it returns the whole wire,
        which is what the global/macro view wants.
        """
        params: dict[str, object] = {
            "limit": limit,
            "sort": "desc",
            # The endpoint defaults to a wide window. Pinning the start keeps a
            # "recent news" block from quietly including last month's story.
            "start": (
                datetime.now(UTC) - timedelta(days=lookback_days)
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if symbols:
            params["symbols"] = symbols

        r = self._http.get(f"{_BASE}/news", params=params)
        r.raise_for_status()
        payload = r.json()
        return [_to_article(row) for row in payload.get("news", [])]


def _to_article(row: dict) -> Article:
    return Article(
        created_at=str(row.get("created_at", ""))[:16],
        headline=str(row.get("headline", "")).strip(),
        # Benzinga leaves `summary` empty on plenty of rows; the headline still
        # carries the fact, so an absent summary is not a reason to drop it.
        summary=str(row.get("summary", "") or "").strip(),
        source=str(row.get("source", "")),
        url=str(row.get("url", "")),
        symbols=tuple(str(s) for s in (row.get("symbols") or [])),
    )


def _fmt(articles: list[Article], *, focus: str | None) -> str:
    if not articles:
        return "  (no articles in the window)"
    lines: list[str] = []
    for a in articles:
        if a.is_roundup:
            tags = f"[roundup, {len(a.symbols)} symbols]"
        else:
            tags = f"[{', '.join(a.symbols)}]" if a.symbols else ""
        lines.append(f"  {a.created_at} {tags} {a.headline}")
        if a.summary:
            # One line each: the analyst needs the gist, and an unbounded dump
            # of article bodies would crowd out every other tool's output.
            lines.append(f"      {a.summary[:220]}")
    header = f" for {focus}" if focus else ""
    return f"  ({len(articles)} articles{header})\n" + "\n".join(lines)


def news_block(symbol: str | None = None) -> str:
    """Compact news block for the LLM analysts. Never raises.

    Pass a symbol for the ticker-specific view, or nothing for the global wire.
    """
    if _creds() is None:
        return (
            "[Alpaca news] ALPACA_API_KEY / ALPACA_API_SECRET not set — "
            "news unavailable."
        )
    label = symbol.upper() if symbol else None
    try:
        with AlpacaNewsClient() as nc:
            articles = nc.news(symbols=label)
    except Exception as exc:  # noqa: BLE001 — degrade, never break the pipeline
        target = label or "market"
        return f"[Alpaca news] fetch failed for {target}: {exc}"

    title = f"Alpaca/Benzinga news — {label}" if label else "Alpaca/Benzinga wire — market"
    return f"[{title}]\n{_fmt(articles, focus=label)}"
