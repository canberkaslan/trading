"""Give the sentiment analyst something to read.

Measured, one NVDA council: the Sentiment Analyst received **394 input
tokens** and produced 2,616 output tokens. Every one of its sources had
failed — StockTwits 403, Reddit RSS 429 on both subreddits, yfinance returned
zero headlines for the window, Finnhub unkeyed. It spent real money forming an
opinion out of nothing.

To its credit it said so: "Confidence: Low", with a data-availability caveat
naming each dead source. But it still emitted **Mildly Bullish (6.0/10)**, and
a score is what travels. Downstream, a 6.0 grown from no data is
indistinguishable from a 6.0 grown from real data — the caveat is prose the
portfolio manager may or may not weigh, while the number goes straight into
the council.

ApeWisdom aggregates the same corpus Reddit RSS was failing to fetch, keyless
and in one call. So it is appended to the Reddit block rather than added
elsewhere: it IS Reddit data, and the analyst already knows how to read that
section.

It is appended ALWAYS, not only on failure. The two are different
measurements — RSS gives a handful of individual posts from two subreddits,
ApeWisdom gives mention counts and 24h change across many — and an analyst
that sees the aggregate only when the posts break would be reading a different
instrument on different days, which is worse than reading a consistent one.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from tradingagents_us.dataflows.apewisdom import apewisdom_block
from tradingagents_us.dataflows.fail_fast import HTTP_READ_TIMEOUT_S, RunBreaker

log = logging.getLogger(__name__)

# After this many consecutive failures, stop calling Reddit for the rest of the
# run. A failure is a ticker for which every subreddit was refused, the
# vendor's one back-off included.
#
# That back-off is the vendor's own, about a minute before its single retry,
# and it is kept: the vendor measured that a retry 8, 10 or 30 s after a 429
# still gets one, and one after 60 s goes through. On the box a search often
# gets a 429 and its retry a minute later gets the posts (2026-10-05: back-offs
# of 60-70 s, twice per ticker). A short retry fails every time, and two such
# tickers would open the breaker and drop Reddit's posts for the rest of the
# night; with StockTwits answering 403, they are the sentiment analyst's only
# post-level social source.
#
# Two is not impatience. One failure is a blip; two in a row, each after a
# minute's back-off, means the limiter has us, and the next ticker will be
# refused too. ApeWisdom aggregates the same corpus keylessly, so the wait
# buys nothing that is not already in the block beside it.
#
# "The run" is every ticker of one daily run. The count used to live in this
# process, and daily_run.sh starts one process per ticker, so it reset for
# every ticker and never skipped anything; `RunBreaker` keeps it in the run's
# state directory instead. Tomorrow's run gets a fresh directory and tries
# Reddit again from scratch — the limiter forgets, and a permanent giving-up
# would be a different decision from the one being made here.
_FAILURE_LIMIT = 2

_BREAKER = RunBreaker("reddit", limit=_FAILURE_LIMIT)


def _record(success: bool) -> None:
    _BREAKER.record(success=success)


def _reddit_is_giving_up() -> bool:
    return _BREAKER.is_open()


def supplement(reddit_block: str, ticker: str) -> str:
    """Reddit's own output plus the aggregate view, as one block."""
    try:
        extra = apewisdom_block(ticker)
    except Exception:  # noqa: BLE001 — a supplement must never cost the report
        return reddit_block
    return f"{reddit_block}\n\n{extra}"


def _accepts_timeout(fn: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "timeout" in params


#: How the vendor's block begins when no subreddit could be fetched at all.
_ALL_REFUSED_PREFIX = "<reddit unavailable"


def _every_source_refused(block: str) -> bool:
    """Whether the vendor's text says no subreddit could be fetched.

    Only that counts as a failure. "unavailable" alone is no sign of one: every
    block of posts fetched over RSS carries "scores/comments unavailable" in
    its header, and a subreddit that failed beside others that answered means
    the limiter let us through.
    """
    return block.lstrip().lower().startswith(_ALL_REFUSED_PREFIX)


def install() -> bool:
    """Rebind `fetch_reddit_posts` inside the sentiment analyst. Idempotent.

    The analyst imports the function by name at module level, so replacing it
    there is the seam — no vendor file is edited, and a subtree pull that moves
    the import costs the supplement rather than the run.
    """
    try:
        from tradingagents.agents.analysts import sentiment_analyst as mod
    except ImportError:
        return False

    original: Callable[..., Any] | None = getattr(mod, "fetch_reddit_posts", None)
    if original is None:
        return False
    if getattr(original, "_supplemented", False):
        return True
    takes_timeout = _accepts_timeout(original)

    def supplemented(ticker: str, *args: Any, **kwargs: Any) -> str:
        if _reddit_is_giving_up():
            # Skipped, and SAID so. Reporting this as "no posts found" would
            # claim a silence we never observed — the same distinction the
            # vendor's own empty-result message is careful to make.
            return supplement(
                "<Reddit skipped: the rate limiter refused repeated attempts this run; "
                "this is not an absence of discussion>",
                ticker,
            )

        if takes_timeout:
            # The vendor's default is 10 s per subreddit request.
            kwargs.setdefault("timeout", HTTP_READ_TIMEOUT_S)
        try:
            block = original(ticker, *args, **kwargs)
        except Exception:
            # Reddit itself failing is the case this exists for, so the
            # aggregate still goes in rather than the analyst seeing nothing.
            _record(success=False)
            log.warning("reddit fetch raised; supplying the aggregate alone", exc_info=True)
            return supplement(
                "<Reddit unavailable: fetch raised; this is not an absence of discussion>",
                ticker,
            )

        # The vendor returns its failure as TEXT rather than raising, so a
        # successful call can still mean every source was refused. Counting
        # only exceptions would leave the breaker permanently open-eyed while
        # the run slept through nineteen of exactly this.
        _record(success=not _every_source_refused(block))
        return supplement(block, ticker)

    supplemented._supplemented = True  # type: ignore[attr-defined]
    supplemented._wraps = original  # type: ignore[attr-defined]
    mod.fetch_reddit_posts = supplemented  # type: ignore[attr-defined]
    return True
