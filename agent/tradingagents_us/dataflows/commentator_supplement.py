"""Put one commentator's views in front of the sentiment analyst, labelled as opinion.

ADR-009. Off unless COMMENTATOR_FEED=1. The cached, English-extracted items in
`commentator_items` go into the sentiment analyst's system message as their own
`### Commentator feed` section, inserted before `## How to analyze this data`
(appended at the end if a vendor update ever moves that heading). Nowhere else:
not the Reddit block, whose header tells the analyst it is Reddit; not the
StockTwits slot, whose guidance is about Bullish/Bearish ratios; and not the
news vendor chain, whose first successful vendor replaces the others and which
the sentiment analyst also reads, so the same item would count twice.

Why the sentiment analyst and nothing downstream: it already has the rule this
needs — "distinguish opinion from event" — and its output is one input among
four to a debate. A single commentator should be able to colour a sentiment
read, and should have no path to an order that bypasses the rest of the graph.

Look-ahead. The vendor's `in_window` admits the whole trade date (`[start,
end + 1 day)`), which on a backtest lets in a video published after the
decision would have been made. So on top of it:
  - backtest (trade date before today): published strictly before the trade
    date's 00:00 UTC — conservative, since the run time on that day is unknown;
  - live (trade date is today): published no later than this run's start.
An undated item is refused outright, backtest or live.

The seam is the same one `sentiment_supplement` uses: the analyst node resolves
`_build_system_message` from its module globals at call time, so rebinding that
name wraps it with no vendor file edited. The wrapper checks the flag on every
call, so a process that installed it and then runs with the flag off produces
the vendor's prompt byte for byte.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any

from tradingagents_us.dataflows.commentator import config
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage import commentator as store
from tradingagents_us.storage.commentator import StoredItem

log = logging.getLogger(__name__)

#: The heading the block is inserted in front of (sentiment_analyst.py).
MARKER = "## How to analyze this data"

#: At most this many items about the ticker itself.
MAX_ITEMS_PER_TICKER = 5

#: Market-wide items are keyed as SPY by the extractor. SPY's own analyst reads
#: them as ticker items; every other ticker gets at most this many.
MARKET_KEY = "SPY"
MAX_MACRO_LINES = 1

_SOURCE_LABEL = {"youtube": "YouTube", "x": "X"}

_GUIDANCE = (
    "One commentator's published views, machine-extracted from Turkish titles, "
    "descriptions and posts and paraphrased in English. Read them under rule 4 "
    "below as opinion, never as events:\n"
    "- One person's view is not consensus. Weigh it as a single opinion, however "
    "confident it sounds.\n"
    "- An item whose stance is `unstated` shows only what he talked about; it must "
    "not move overall_score.\n"
    "- Known bias: he runs a paid community and his content carries promotion; his "
    "headline tone skews bullish.\n"
    "- Report this source on its own line in the narrative, starting "
    "`Commentator view:`. Paraphrase; never quote him."
)

_state_lock = threading.Lock()
_run_starts: dict[tuple[str, str], datetime] = {}
_consumed: dict[tuple[str, str], list[tuple[str, str]]] = {}


# ------------------------------------------------------------------ run bookkeeping


def begin_run(ticker: str, trade_date: str, now: datetime | None = None) -> None:
    """Mark when this ticker's run started; the live cutoff is measured from it."""
    with _state_lock:
        _run_starts[(ticker, trade_date)] = now or datetime.now(UTC)


def _run_start(ticker: str, trade_date: str) -> datetime | None:
    with _state_lock:
        return _run_starts.get((ticker, trade_date))


def _record_consumed(ticker: str, trade_date: str, items: Sequence[StoredItem]) -> None:
    with _state_lock:
        _consumed[(ticker, trade_date)] = [(i.source, i.item_id) for i in items]


def consumed(ticker: str, trade_date: str) -> list[tuple[str, str]]:
    """`(source, item_id)` pairs the last prompt built for this run contained."""
    with _state_lock:
        return list(_consumed.get((ticker, trade_date), []))


def bind_decision(ticker: str, trade_date: str, decision_id: str) -> None:
    """Hand this run's items to the store, to be written when the decision is saved."""
    with _state_lock:
        refs = _consumed.pop((ticker, trade_date), [])
        _run_starts.pop((ticker, trade_date), None)
    store.stash_decision_refs(decision_id, refs)


# ------------------------------------------------------------------ selection


def _day(value: str) -> datetime:
    return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=UTC)


def cutoff(end_date: str, *, run_start: datetime | None, now: datetime) -> tuple[datetime, bool]:
    """`(instant, inclusive)`: nothing published after it may be shown.

    A trade date before today is a backtest: strictly before that day's 00:00
    UTC. Today (or later) is live: no later than the run's start, or `now` when
    the run did not record one.
    """
    if date.fromisoformat(end_date) < now.astimezone(UTC).date():
        return _day(end_date), False
    return (run_start or now).astimezone(UTC), True


def select(
    items: Sequence[StoredItem],
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    run_start: datetime | None,
    now: datetime,
) -> tuple[list[StoredItem], list[StoredItem]]:
    """`(ticker_items, macro_items)` the analyst for `ticker` may see, newest first."""
    from tradingagents.dataflows.date_window import in_window

    start_dt, end_dt = _day(start_date), _day(end_date)
    limit, inclusive = cutoff(end_date, run_start=run_start, now=now)

    def admissible(item: StoredItem) -> bool:
        pub = item.published_at
        if pub is None:  # refused: an undated item cannot be kept out of a backtest
            return False
        if not in_window(pub, start_dt, end_dt):
            return False
        if pub > limit or (pub == limit and not inclusive):
            return False
        return item.is_market_content and not item.is_promo

    pool = sorted((i for i in items if admissible(i)), key=lambda i: i.published_at, reverse=True)
    sym = ticker.upper()

    def is_market_wide(item: StoredItem) -> bool:
        return MARKET_KEY in item.tickers or bool(item.macro_topics)

    if sym == MARKET_KEY:
        return [i for i in pool if is_market_wide(i)][:MAX_ITEMS_PER_TICKER], []
    own = [i for i in pool if sym in i.tickers][:MAX_ITEMS_PER_TICKER]
    macro = [i for i in pool if i not in own and is_market_wide(i)][:MAX_MACRO_LINES]
    return own, macro


# ------------------------------------------------------------------ rendering


def _stamp(item: StoredItem) -> str:
    return item.published_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


def _ref(item: StoredItem) -> str:
    return f"[{_SOURCE_LABEL.get(item.source, item.source)} {item.source_id}]"


def _line(item: StoredItem, ticker: str, *, market_wide: bool) -> str:
    parts: list[str] = []
    if market_wide:
        parts.append("market-wide")
    others = [t for t in item.tickers if t != ticker]
    if not market_wide and others:
        parts.append("also on: " + ", ".join(others))
    if item.macro_topics:
        parts.append("topics: " + ", ".join(item.macro_topics))
    if market_wide:
        parts.append(f"broad-market stance: {item.stance.get(MARKET_KEY, 'unstated')}")
    else:
        parts.append(f"stance on {ticker}: {item.stance.get(ticker, 'unstated')}")
    if item.claim_en:
        parts.append(f"paraphrase: {item.claim_en}")
    return f"- {_stamp(item)} {_ref(item)} " + "; ".join(parts)


def render(
    ticker: str,
    ticker_items: Sequence[StoredItem],
    macro_items: Sequence[StoredItem],
    *,
    start_date: str,
    end_date: str,
) -> str:
    sym = ticker.upper()
    lines = [_line(i, sym, market_wide=sym == MARKET_KEY and sym not in i.tickers)
             for i in ticker_items]
    lines += [_line(i, sym, market_wide=True) for i in macro_items]
    body = "\n".join(lines) if lines else (
        f"No commentary in window ({start_date} to {end_date}) for {sym} or the broad market."
    )
    return _wrap(body)


def _wrap(body: str) -> str:
    return (
        f"### Commentator feed — {config.COMMENTATOR_NAME} ({config.COMMENTATOR_DESCRIPTION})\n"
        f"{_GUIDANCE}\n\n"
        f"<start_of_commentator>\n{body}\n<end_of_commentator>"
    )


def unavailable(reason: str) -> str:
    # Said as unavailable, not as silence: "no commentary" would claim an
    # absence nobody observed — the distinction the Reddit placeholder makes.
    return _wrap(
        f"<Commentator feed unavailable: {reason}; this is not an absence of commentary>"
    )


def insert(system_message: str, block: str) -> str:
    """The block before MARKER, or appended when the vendor no longer has MARKER."""
    at = system_message.find(MARKER)
    if at < 0:
        return f"{system_message.rstrip()}\n\n{block}\n"
    return f"{system_message[:at]}{block}\n\n{system_message[at:]}"


# ------------------------------------------------------------------ data access


@lru_cache(maxsize=1)
def _repo() -> TradeLogRepository:
    """One repository per process, as `sector_map` does: this runs once per ticker."""
    from sqlalchemy import create_engine

    url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
    return TradeLogRepository(engine=create_engine(url, future=True))


def _load_items(start: datetime, end: datetime) -> list[StoredItem]:
    with _repo().session() as s:
        return store.extracted_items_between(s, start, end)


def build_block(
    ticker: str,
    start_date: str,
    end_date: str,
    *,
    run_start: datetime | None = None,
    now: datetime | None = None,
    load: Callable[[datetime, datetime], list[StoredItem]] | None = None,
) -> tuple[str, list[StoredItem]]:
    """The rendered section and the items in it."""
    now = now or datetime.now(UTC)
    loader = load or _load_items
    items = loader(_day(start_date), _day(end_date) + timedelta(days=1))
    own, macro = select(items, ticker, start_date, end_date, run_start=run_start, now=now)
    return render(ticker, own, macro, start_date=start_date, end_date=end_date), own + macro


# ------------------------------------------------------------------ install


def install() -> bool:
    """Wrap `_build_system_message` inside the sentiment analyst. Idempotent."""
    try:
        from tradingagents.agents.analysts import sentiment_analyst as mod
    except ImportError:
        return False

    original: Callable[..., str] | None = getattr(mod, "_build_system_message", None)
    if original is None:
        return False
    if getattr(original, "_supplemented", False):
        return True

    def supplemented(*args: Any, **kwargs: Any) -> str:
        text = original(*args, **kwargs)
        if not config.is_enabled():
            return text
        ticker = kwargs.get("ticker")
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        # The vendor passes these by keyword; anything else is a signature this
        # was not written against, and the vendor's prompt goes out untouched.
        if not (
            isinstance(ticker, str) and isinstance(start_date, str) and isinstance(end_date, str)
        ):
            return text
        try:
            block, used = build_block(
                ticker, start_date, end_date, run_start=_run_start(ticker, end_date)
            )
        except Exception as exc:  # noqa: BLE001 — a supplement must never cost the report
            log.warning("commentator feed unavailable for %s", ticker, exc_info=True)
            block, used = unavailable(type(exc).__name__), []
        _record_consumed(ticker, end_date, used)
        return insert(text, block)

    supplemented._supplemented = True  # type: ignore[attr-defined]
    supplemented._wraps = original  # type: ignore[attr-defined]
    mod._build_system_message = supplemented  # type: ignore[attr-defined]
    return True
