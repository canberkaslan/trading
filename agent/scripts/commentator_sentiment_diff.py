#!/usr/bin/env python3
"""ADR-009 gate, step A: does the commentator block move the sentiment score?

The cheap measurement before any paired backtest. For each (ticker, date) it
runs ONLY the sentiment analyst node, twice, on identical inputs — once with
the commentator block and once without — and compares the score in the
report header (`**Overall Sentiment:** **band** (Score: n/10)`).

Identical inputs matter more than they look. The node fetches news,
StockTwits, Reddit and Finnhub itself, and two fetches minutes apart can
differ (a 429 on one and not the other), which would be read as the feed's
effect. So every fetcher the node calls is memoised for the run: the second
call gets the first call's text. The model runs at temperature 0; `--noise`
adds a second feed-off call per point to measure what is left of run-to-run
variation, which is the floor any feed effect has to clear.

How to read the result (plan §5, step A):
  - points whose prompt lines all show stance `unstated` should show Δ ≈ 0;
    if they do not, the prompt is leaking tone into the score — fix the
    prompt. "Shows" is per line, as rendered: a META line shows the META
    stance only, even when the item also carries one for SPY;
  - if Δ ≈ 0 everywhere, the feed adds nothing the analyst uses — stop there;
  - otherwise, and only then, run step B.

Costs model money (one sentiment call per point per arm, plus the node's own
data fetches). `--dry-run` spends nothing: it lists the points and the block
each would add.

    python -m scripts.commentator_sentiment_diff --tickers SPY META NVDA AMZN --max-dates 25
    python -m scripts.commentator_sentiment_diff --points META:2026-09-22 NVDA:2026-09-18
    python -m scripts.commentator_sentiment_diff --tickers META --dry-run

Step B (paired ablation, ~$1-2 per decision) is `backtest/llm_backtest.py` run
twice over the SAME points, once per arm, compared on rating flips, hits among
the flips against the 21-day forward return, mean rating shift and cost:

    COMMENTATOR_FEED=0 python -m backtest.llm_backtest --points META:2026-09-22 ... > off.txt
    COMMENTATOR_FEED=1 python -m backtest.llm_backtest --points META:2026-09-22 ... > on.txt

Items must be in `commentator_items` first (`scripts.commentator_fetch
--ignore-flag --backfill-days N`), and both steps must finish inside the
thirty days YouTube items are kept. That fetch reads YouTube only with
COMMENTATOR_YOUTUBE_CLEARED=1 beside the key: until the ADR-009 YouTube
preconditions are met there are no YouTube items, and this gate is blocked.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import statistics
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any

_AGENT_ROOT = Path(__file__).resolve().parent.parent
for _p in (_AGENT_ROOT, _AGENT_ROOT / "vendor" / "tradingagents"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tradingagents_us.dataflows import commentator_supplement as cs  # noqa: E402
from tradingagents_us.storage.commentator import StoredItem  # noqa: E402

_HEADER = re.compile(
    r"\*\*Overall Sentiment:\*\*\s*\*\*(?P<band>[^*]+)\*\*"
    r"\s*\(Score:\s*(?P<score>\d+(?:\.\d+)?)/10\)"
)

#: A point whose |Δ| is at or under this is "no movement" (half a band step).
_NOISE_BAND = 0.5

#: The node's inputs that are fetched live and must be frozen between arms.
_FETCHERS = ("fetch_stocktwits_messages", "fetch_reddit_posts", "finnhub_block")


def parse_header(report: str) -> tuple[str, float] | None:
    """(band, score) from the structured report header, or None (free-text fallback)."""
    m = _HEADER.search(report or "")
    if m is None:
        return None
    return m.group("band").strip(), float(m.group("score"))


@dataclass
class PointDiff:
    ticker: str
    date: str
    items: int
    stance_stated: bool
    off_score: float | None
    on_score: float | None
    off_band: str | None = None
    on_band: str | None = None
    noise_score: float | None = None

    @property
    def delta(self) -> float | None:
        if self.off_score is None or self.on_score is None:
            return None
        return self.on_score - self.off_score

    @property
    def noise(self) -> float | None:
        if self.off_score is None or self.noise_score is None:
            return None
        return self.noise_score - self.off_score


def _stats(deltas: Sequence[float]) -> dict[str, Any]:
    if not deltas:
        return {"n": 0, "mean_delta": None, "mean_abs_delta": None, "moved": 0}
    return {
        "n": len(deltas),
        "mean_delta": round(statistics.fmean(deltas), 3),
        "mean_abs_delta": round(statistics.fmean(abs(d) for d in deltas), 3),
        "moved": sum(1 for d in deltas if abs(d) > _NOISE_BAND),
    }


def summarize(diffs: Sequence[PointDiff]) -> dict[str, Any]:
    scored = [d for d in diffs if d.delta is not None]
    stated = [d.delta for d in scored if d.stance_stated and d.delta is not None]
    unstated = [d.delta for d in scored if not d.stance_stated and d.delta is not None]
    noise = [d.noise for d in diffs if d.noise is not None]
    all_deltas = [d.delta for d in scored if d.delta is not None]
    out: dict[str, Any] = {
        "points": len(diffs),
        "scored": len(scored),
        "all": _stats(all_deltas),
        "stance_stated": _stats(stated),
        "stance_unstated": _stats(unstated),
        "noise_floor": _stats(noise),
    }
    if not scored:
        verdict = "no scored points — nothing to conclude"
    elif out["stance_unstated"]["moved"]:
        verdict = "PROMPT BUG: unstated-stance points moved the score; fix before step B"
    elif out["all"]["moved"] == 0:
        verdict = "no movement: the feed adds nothing the analyst uses — stop, keep it off"
    else:
        verdict = "movement on stated-stance points: step B (paired llm_backtest) is justified"
    out["verdict"] = verdict
    return out


def _weekdays(start: date, end: date) -> Iterator[date]:
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def candidate_points(
    items: Sequence[StoredItem],
    tickers: Sequence[str],
    *,
    max_dates: int,
    today: date,
) -> list[tuple[str, str]]:
    """Up to `max_dates` recent past weekdays per ticker on which the block is non-empty.

    Uses the supplement's own selection with the backtest cutoff, so a point
    only qualifies on items its prompt would really show.
    """
    dated = [i.published_at for i in items if i.published_at is not None]
    if not dated:
        return []
    first = min(dated).date() + timedelta(days=1)
    days = sorted(_weekdays(first, today - timedelta(days=1)), reverse=True)
    now = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
    points: list[tuple[str, str]] = []
    for ticker in tickers:
        found = 0
        for d in days:
            if found >= max_dates:
                break
            start = (d - timedelta(days=7)).isoformat()
            own, macro = cs.select(items, ticker, start, d.isoformat(), run_start=None, now=now)
            if own or macro:
                points.append((ticker.upper(), d.isoformat()))
                found += 1
    return points


def stance_stated(lines: Sequence[tuple[StoredItem, bool]], ticker: str) -> bool:
    """Whether any line the prompt showed states a stance, judged by what the line shows.

    Per line, as `cs.build_block_lines` returns them: a META item that also
    names SPY is shown as "stance on META: unstated", and its SPY stance is
    not in the prompt. Counting it would file a moving unstated point under
    `stance_stated`, where the PROMPT BUG check never looks.
    """
    return any(
        cs.shown_stance(item, ticker, market_wide=wide) != "unstated" for item, wide in lines
    )


@contextlib.contextmanager
def frozen_inputs(mod: Any) -> Iterator[None]:
    """Memoise every live fetcher the node calls, so both arms read the same text."""
    saved: dict[str, Any] = {}

    def memo(fn: Callable[..., Any]) -> Callable[..., Any]:
        cache: dict[str, Any] = {}

        @wraps(fn)
        def inner(*args: Any, **kwargs: Any) -> Any:
            key = repr((args, sorted(kwargs.items())))
            if key not in cache:
                cache[key] = fn(*args, **kwargs)
            return cache[key]

        return inner

    class _FrozenTool:
        """`get_news` is a LangChain tool; the node calls its `.func`."""

        def __init__(self, tool: Any) -> None:
            self._tool = tool
            self.func = memo(tool.func)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._tool, name)

    try:
        for name in _FETCHERS:
            if hasattr(mod, name):
                saved[name] = getattr(mod, name)
                setattr(mod, name, memo(saved[name]))
        if hasattr(mod, "get_news"):
            saved["get_news"] = mod.get_news
            mod.get_news = _FrozenTool(saved["get_news"])
        yield
    finally:
        for name, value in saved.items():
            setattr(mod, name, value)


@contextlib.contextmanager
def _feed(on: bool) -> Iterator[None]:
    before = os.environ.get("COMMENTATOR_FEED")
    if on:
        os.environ["COMMENTATOR_FEED"] = "1"
    else:
        os.environ.pop("COMMENTATOR_FEED", None)
    try:
        yield
    finally:
        if before is None:
            os.environ.pop("COMMENTATOR_FEED", None)
        else:
            os.environ["COMMENTATOR_FEED"] = before


def _state(ticker: str, trade_date: str) -> dict[str, Any]:
    return {
        "messages": [("human", ticker)],
        "company_of_interest": ticker,
        "asset_type": "stock",
        "instrument_context": "",
        "trade_date": trade_date,
    }


def run_point(
    node: Callable[[dict[str, Any]], dict[str, Any]],
    ticker: str,
    trade_date: str,
    *,
    noise: bool,
) -> PointDiff:
    """Both arms of one point. The node must come from a module under `frozen_inputs`."""
    _, lines = cs.build_block_lines(ticker, _start(trade_date), trade_date)
    def score() -> tuple[str, float] | None:
        return parse_header(node(_state(ticker, trade_date))["sentiment_report"])

    with _feed(on=False):
        off = score()
        again = score() if noise else None
    with _feed(on=True):
        on = score()
    return PointDiff(
        ticker=ticker,
        date=trade_date,
        items=len(lines),
        stance_stated=stance_stated(lines, ticker),
        off_score=off[1] if off else None,
        on_score=on[1] if on else None,
        off_band=off[0] if off else None,
        on_band=on[0] if on else None,
        noise_score=again[1] if again else None,
    )


def _start(trade_date: str) -> str:
    return (date.fromisoformat(trade_date) - timedelta(days=7)).isoformat()


def _all_items() -> list[StoredItem]:
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    return cs._load_items(epoch, datetime.now(UTC) + timedelta(days=1))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Sentiment-node-only commentator diff (ADR-009 A)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--points", nargs="+", help="TICKER:YYYY-MM-DD pairs")
    src.add_argument("--tickers", nargs="+", help="Pick dates from the stored feed")
    p.add_argument("--max-dates", type=int, default=25, help="Per ticker, with --tickers")
    p.add_argument("--model", default=os.environ.get("TRADINGAGENTS_QUICK_THINK_LLM",
                                                     "claude-sonnet-4-6"))
    p.add_argument("--noise", action="store_true", help="A second feed-off call per point")
    p.add_argument("--dry-run", action="store_true", help="List points and blocks; no LLM")
    p.add_argument("--out", type=Path, default=None, help="Write per-point JSON lines here")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.points:
        points = [(p.split(":")[0].upper(), p.split(":")[1]) for p in args.points]
    else:
        points = candidate_points(
            _all_items(), args.tickers, max_dates=args.max_dates,
            today=datetime.now(UTC).date(),
        )
    if not points:
        print("no points: is commentator_items populated? (scripts.commentator_fetch)")
        return 1

    if args.dry_run:
        for ticker, d in points:
            block, lines = cs.build_block_lines(ticker, _start(d), d)
            print(f"== {ticker} @ {d}: {len(lines)} item(s), +{len(block)} chars, "
                  f"stance stated: {stance_stated(lines, ticker)}")
            print(block)
        return 0

    from langchain_anthropic import ChatAnthropic
    from tradingagents.agents.analysts import sentiment_analyst as mod

    if not cs.install():
        print("could not install the commentator block into the sentiment analyst")
        return 1
    llm = ChatAnthropic(model=args.model, temperature=0, max_tokens=8000)
    print(f"⚠️  {len(points)} point(s) x {3 if args.noise else 2} sentiment calls on {args.model}")
    diffs: list[PointDiff] = []
    for ticker, d in points:
        with frozen_inputs(mod):
            node = mod.create_sentiment_analyst(llm)
            diff = run_point(node, ticker, d, noise=args.noise)
        diffs.append(diff)
        print(f"  {ticker} @ {d}: off={diff.off_score} on={diff.on_score} "
              f"Δ={diff.delta} items={diff.items} stated={diff.stance_stated}")
        if args.out:
            with args.out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({**asdict(diff), "delta": diff.delta}) + "\n")
    print(json.dumps(summarize(diffs), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
