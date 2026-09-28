#!/usr/bin/env python3
"""Fetch and extract the commentator feed once per run — daily_run.sh, before the tickers.

ADR-009. Every ticker's sentiment analyst reads what this stores; none of them
fetches. Run once, it costs three YouTube quota units, a handful of X posts at
pay-per-use rates when X is configured, and one cheap-tier extraction per NEW
item. An item already extracted is never extracted again.

YouTube needs COMMENTATOR_YOUTUBE_CLEARED=1 beside YOUTUBE_API_KEY, whatever
the flag or `--ignore-flag` says: a key alone is refused (config.py, ADR-009
"Enabling YouTube — preconditions").

Best-effort by design: exits 0 on every failure, like the other steps
daily_run.sh appends, because a missing commentator block must never cost a
trading day. A source without credentials is skipped and logged.

Retention does not wait for the flag. With COMMENTATOR_FEED off nothing is
fetched, but the retention pass still runs, and `--retention-only` is that
pass alone — what ai-trader-commentator-retention.timer runs every day,
weekends included. It exits 1 when the pass itself fails, so the timer pages.

    python -m scripts.commentator_fetch                   # needs COMMENTATOR_FEED=1
    python -m scripts.commentator_fetch --dry-run         # list uploads; no LLM, no X, no writes
    python -m scripts.commentator_fetch --backfill-days 90 --youtube-pages 4
    python -m scripts.commentator_fetch --retention-only  # purge + X deletion check; any flag
    python -m scripts.commentator_fetch --resolve-x-id BoraOzkentNSDQ   # one-off, prints the id

`--resolve-x-id` is the one step that looks an account up by handle, and it only
prints: a person checks the id against the profile and pins it in
`tradingagents_us/dataflows/commentator/config.py` or COMMENTATOR_X_USER_ID.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

# Make package + vendor importable when running as a script
_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from tradingagents_us.dataflows.commentator import config  # noqa: E402
from tradingagents_us.dataflows.commentator.failures import (  # noqa: E402
    describe,
    traceback_of,
)

if TYPE_CHECKING:
    from tradingagents_us.storage import TradeLogRepository

log = logging.getLogger("commentator_fetch")

#: Hard ceiling on backfill pages (50 uploads each): one quota unit per page
#: plus one per videos.list, and one paid extraction per video.
_MAX_YOUTUBE_PAGES = 20


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fetch + extract the commentator feed (ADR-009)")
    p.add_argument("--dry-run", action="store_true",
                   help="List the YouTube uploads that would be ingested; no extraction, "
                        "no DB writes, no X call")
    p.add_argument("--ignore-flag", action="store_true",
                   help="Run even when COMMENTATOR_FEED is not 1 (to prepare an evaluation). "
                        "YouTube still needs COMMENTATOR_YOUTUBE_CLEARED=1 beside its key")
    p.add_argument("--backfill-days", type=int, default=None,
                   help="Ingest YouTube uploads this many days back (default: the routine "
                        "lookback). Items still expire 30 days after this fetch.")
    p.add_argument("--youtube-pages", type=int, default=1,
                   help=f"Playlist pages of 50 to read (max {_MAX_YOUTUBE_PAGES})")
    p.add_argument("--retention-only", action="store_true",
                   help="Only enforce retention: delete expired items and check X "
                        "deletions. No YouTube call, no extraction. Runs whatever "
                        "COMMENTATOR_FEED says; exits 1 if the pass fails")
    p.add_argument("--resolve-x-id", metavar="USERNAME", default=None,
                   help="Print the numeric X id for a handle, to be checked and pinned. "
                        "Makes one paid API call; nothing is stored.")
    return p


def _resolve(username: str) -> int:
    from tradingagents_us.dataflows.commentator.x_source import XClient

    token = config.x_bearer_token()
    if token is None:
        print("X_BEARER_TOKEN is not set; cannot resolve", file=sys.stderr)
        return 0
    try:
        with XClient(token) as client:
            uid = client.resolve_user_id(username)
    except Exception as exc:  # noqa: BLE001
        print(f"could not resolve @{username.lstrip('@')}: {type(exc).__name__}", file=sys.stderr)
        return 0
    print(f"@{username.lstrip('@')} -> {uid}")
    print("Check it against the profile, then pin it in config.X_USER_ID "
          "or COMMENTATOR_X_USER_ID. Never pin a handle.")
    return 0


def _dry_run(lookback: timedelta, pages: int) -> int:
    from datetime import UTC, datetime

    from tradingagents_us.dataflows.commentator.youtube_source import (
        YouTubeClient,
        fetch_recent,
    )

    now = datetime.now(UTC)
    key = config.youtube_api_key()
    if key is None:
        print(f"YouTube: skipped ({config.youtube_skip_reason()})")
    else:
        with YouTubeClient(key) as yt:
            items = fetch_recent(
                yt, config.YOUTUBE_CHANNEL_ID, since=now - lookback, max_pages=pages
            )
        print(f"YouTube: {len(items)} upload(s) since {(now - lookback):%Y-%m-%d}")
        for i in items:  # no video id: this output may land in a kept log
            print(f"  {i.published_at:%Y-%m-%dT%H:%MZ} ({len(i.text)} chars)")
    # X bills per post read, so a dry run only reports whether it would run.
    token, uid = config.x_bearer_token(), config.x_user_id()
    if token is None:
        print("X: would be skipped (no X_BEARER_TOKEN)")
    elif uid is None:
        print("X: would be skipped (no numeric user id pinned)")
    else:
        print(f"X: would fetch user {uid} (not called in a dry run: it is billed per post)")
    return 0


def _repo() -> TradeLogRepository:
    from sqlalchemy import create_engine

    from tradingagents_us.storage import TradeLogRepository

    url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
    # A failed statement's message would otherwise carry its parameters, the
    # item ids, into the traceback this script logs.
    return TradeLogRepository(engine=create_engine(url, future=True, hide_parameters=True))


def _retain() -> int:
    """The retention pass alone. Raises on a store failure; the caller decides the exit code."""
    from tradingagents_us.dataflows.commentator import ingest
    from tradingagents_us.dataflows.commentator.x_source import XClient

    token = config.x_bearer_token()
    x = XClient(token) if token else None
    try:
        report = ingest.enforce_retention(_repo().session, x=x)
    finally:
        if x is not None:
            x.close()
    print(report.retention_summary())
    return 0


def _retention_pass(*, fatal: bool) -> int:
    """Run `_retain`. A failure exits 1 when `fatal` (the timer must page), else 0."""
    try:
        return _retain()
    except Exception as exc:  # noqa: BLE001
        log.warning("commentator retention pass failed (%s)", describe(exc),
                    exc_info=traceback_of(exc))
        if fatal:
            print(f"commentator retention pass FAILED: {describe(exc)}")
            return 1
        print(f"commentator retention pass failed (non-fatal): {describe(exc)}")
        return 0


def _ingest(lookback: timedelta, pages: int) -> int:
    from tradingagents_us.dataflows.commentator import ingest
    from tradingagents_us.dataflows.commentator.extract import extract, model_name
    from tradingagents_us.dataflows.commentator.x_source import XClient
    from tradingagents_us.dataflows.commentator.youtube_source import YouTubeClient
    from tradingagents_us.llm.usage import UsageCollector

    repo = _repo()
    usage = UsageCollector()

    key, token = config.youtube_api_key(), config.x_bearer_token()
    youtube = YouTubeClient(key) if key else None
    x = XClient(token) if token else None
    try:
        report = ingest.run(
            repo.session,
            youtube=youtube,
            x=x,
            x_user_id=config.x_user_id(),
            extractor=lambda text: extract(text, callbacks=[usage]),
            model=model_name(),
            lookback=lookback,
            youtube_max_pages=pages,
        )
    finally:
        for client in (youtube, x):
            if client is not None:
                client.close()
    print(report.summary())
    u = usage.usage
    print(f"commentator extraction usage: {u.calls} call(s), in={u.input_tokens} "
          f"out={u.output_tokens} cost=${u.cost_usd:.4f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s"
    )
    # httpx logs every request URL at INFO, and this feed's query strings carry
    # the ids of stored content (the X deletion check, videos.list). This output
    # goes to a daily log nothing rotates and to journald, where an id would
    # outlive its purge. Failures are logged by type and status (`failures`).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Keys stay in headers here; the filter is the backstop for any that ever
    # lands in a query string.
    from tradingagents_us.log_redaction import install as install_log_redaction

    install_log_redaction()
    args = build_parser().parse_args(argv)

    if args.resolve_x_id:
        return _resolve(args.resolve_x_id)
    if args.retention_only:
        return _retention_pass(fatal=True)
    if not config.is_enabled() and not args.ignore_flag:
        print("commentator feed off (COMMENTATOR_FEED != 1); nothing fetched")
        # Retention does not wait for the flag; a dry run writes nothing.
        return 0 if args.dry_run else _retention_pass(fatal=False)

    pages = max(1, min(args.youtube_pages, _MAX_YOUTUBE_PAGES))
    lookback = (
        timedelta(days=args.backfill_days)
        if args.backfill_days and args.backfill_days > 0
        else config.ingest_lookback()
    )
    try:
        return _dry_run(lookback, pages) if args.dry_run else _ingest(lookback, pages)
    except Exception as exc:  # noqa: BLE001 — never fail the run this is appended to
        log.warning("commentator fetch failed (%s)", describe(exc), exc_info=traceback_of(exc))
        print(f"commentator fetch failed (non-fatal): {describe(exc)}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
