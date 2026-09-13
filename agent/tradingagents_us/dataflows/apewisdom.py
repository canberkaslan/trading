"""ApeWisdom — Reddit mention counts, keyless.

The vendored Reddit path fetches per-subreddit RSS and is being rate-limited in
production: a single NVDA run logged two 429s costing 121.7 seconds of pure
sleeping, and the daily run does that once per ticker. Across eleven names
that is roughly twenty minutes of a post-close window spent waiting, for a
signal that arrives as raw posts the LLM then has to read.

ApeWisdom already aggregates the same corpus — it counts mentions and upvotes
per ticker across the retail subreddits — and serves it as one JSON call with
no key and no account. So this replaces a slow scrape of raw text with a fast
fetch of the number that scrape was trying to compute.

Verified against the live endpoint rather than the documentation: 7 pages of
100, fields `rank, ticker, name, mentions, upvotes, rank_24h_ago,
mentions_24h_ago`. A User-Agent is sent as courtesy to a free public service,
not out of necessity — the endpoint answers 200 without one, contrary to what
was assumed.

What this is NOT: a sentiment score. It counts how loudly a ticker is being
discussed, not whether the discussion is positive, and the block says so in as
many words. A mention spike is a crowding signal — often a reason for caution
rather than conviction — and an LLM handed a bare number will otherwise read
"lots of mentions" as "bullish".

No SLA, no published terms, one maintainer. It degrades like every other
dataflow here: `apewisdom_block` never raises.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

_BASE = "https://apewisdom.io/api/v1.0"
_TIMEOUT = 10.0
_UA = "ai-trader/1.0 (personal research; contact via repository)"

# One page is the top 100 by mention count. A name outside it is, by definition,
# not one Reddit is talking about — which is itself the answer.
_DEFAULT_PAGES = 1


@dataclass(frozen=True)
class Mention:
    rank: int
    ticker: str
    name: str
    mentions: int
    upvotes: int
    rank_24h_ago: int | None
    mentions_24h_ago: int | None

    @property
    def mention_change_pct(self) -> float | None:
        """Change vs 24h ago. None when there is no prior figure to compare.

        Returned as None rather than 0.0 when the previous count is missing or
        zero: "no change" and "nothing to compare against" are different facts,
        and a new entrant showing 0% would read as the quietest name on the
        list when it is the opposite.
        """
        prior = self.mentions_24h_ago
        if prior is None or prior <= 0:
            return None
        return (self.mentions - prior) / prior * 100.0


class ApeWisdomClient:
    """Thin REST client. Methods raise; `apewisdom_block` degrades."""

    def __init__(self, timeout_s: float = _TIMEOUT) -> None:
        self._http = httpx.Client(timeout=timeout_s, headers={"User-Agent": _UA})

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ApeWisdomClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def top_mentions(self, pages: int = _DEFAULT_PAGES) -> list[Mention]:
        out: list[Mention] = []
        for page in range(1, max(1, pages) + 1):
            r = self._http.get(f"{_BASE}/filter/all-stocks/page/{page}")
            r.raise_for_status()
            for row in r.json().get("results", []):
                out.append(_to_mention(row))
        return out


def _to_int(value: object) -> int | None:
    # The API returns these as strings on some rows; a silent 0 would be a lie.
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _to_mention(row: dict) -> Mention:
    return Mention(
        rank=_to_int(row.get("rank")) or 0,
        ticker=str(row.get("ticker", "")).upper(),
        name=str(row.get("name", "")),
        mentions=_to_int(row.get("mentions")) or 0,
        upvotes=_to_int(row.get("upvotes")) or 0,
        rank_24h_ago=_to_int(row.get("rank_24h_ago")),
        mentions_24h_ago=_to_int(row.get("mentions_24h_ago")),
    )


def find(mentions: list[Mention], symbol: str) -> Mention | None:
    target = symbol.upper()
    return next((m for m in mentions if m.ticker == target), None)


def apewisdom_block(symbol: str, pages: int = _DEFAULT_PAGES) -> str:
    """Compact retail-attention block for the LLM. Never raises."""
    try:
        with ApeWisdomClient() as c:
            rows = c.top_mentions(pages)
    except Exception as exc:  # noqa: BLE001 — degrade, never break the pipeline
        return f"[ApeWisdom] fetch failed for {symbol.upper()}: {exc}"

    sym = symbol.upper()
    hit = find(rows, sym)
    caveat = (
        "NOTE: these are mention COUNTS, not sentiment. A spike means the name is "
        "being discussed loudly, not that the discussion is positive; heavy retail "
        "attention is often a crowding risk rather than a bullish signal."
    )

    if hit is None:
        # Absence is a real answer here, and a useful one.
        return (
            f"[ApeWisdom retail attention — {sym}]\n"
            f"  Not in the top {len(rows)} most-mentioned tickers — no meaningful "
            f"retail chatter.\n  {caveat}"
        )

    change = hit.mention_change_pct
    change_txt = "no prior figure" if change is None else f"{change:+.0f}% vs 24h ago"
    rank_txt = (
        f"rank {hit.rank}"
        if hit.rank_24h_ago is None
        else f"rank {hit.rank} (was {hit.rank_24h_ago})"
    )
    return (
        f"[ApeWisdom retail attention — {sym}]\n"
        f"  {rank_txt} of {len(rows)} tracked\n"
        f"  {hit.mentions} mentions ({change_txt}), {hit.upvotes} upvotes\n"
        f"  {caveat}"
    )
