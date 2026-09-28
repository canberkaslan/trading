"""Fetch, extract once, store, purge — once per run, never per ticker.

`scripts/commentator_fetch.py` calls `run()` once before the ticker loop. The
sentiment analysts that follow only read `commentator_items`; nothing on the
decision path touches the network for this feed.

Retention is its own entry point, `enforce_retention()`, and `run()` starts
with the same pass. The daily timer calls it whatever COMMENTATOR_FEED says,
weekends and kill-switch days included: data an evaluation stored with the
flag off, or a feed later switched off, must still leave on time, and an X
deletion must be seen within a day, not at the next weekday run.

Each source is its own step with its own session, so a YouTube quota error
does not roll back what X stored, and neither failure stops the other. A
source with no credentials is skipped and says so; it is not an error.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from tradingagents_us.storage import commentator as store
from tradingagents_us.storage.commentator import Extraction

from . import config
from .items import YOUTUBE, RawItem, X
from .x_source import XClient, fetch_new, to_raw_item
from .youtube_source import YouTubeClient, fetch_recent

log = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]
Extractor = Callable[[str], Extraction | None]


@dataclass
class IngestReport:
    fetched: dict[str, int] = field(default_factory=dict)
    new_items: int = 0
    extracted: int = 0
    extraction_failed: int = 0
    cached: int = 0
    purged_expired: int = 0
    purged_deleted: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        fetched = ", ".join(f"{k}={v}" for k, v in sorted(self.fetched.items())) or "none"
        line = (
            f"commentator ingest: fetched[{fetched}] new={self.new_items} "
            f"extracted={self.extracted} extraction_failed={self.extraction_failed} "
            f"cached={self.cached} purged_expired={self.purged_expired} "
            f"purged_deleted={self.purged_deleted}"
        )
        return "\n".join([line, *(f"  - {n}" for n in self.notes)])

    def retention_summary(self) -> str:
        line = (
            f"commentator retention: purged_expired={self.purged_expired} "
            f"purged_deleted={self.purged_deleted}"
        )
        return "\n".join([line, *(f"  - {n}" for n in self.notes)])


def _store(
    session: Session,
    items: list[RawItem],
    *,
    extractor: Extractor,
    model: str,
    now: datetime,
    expires_at: datetime,
    report: IngestReport,
) -> None:
    """Extract what is new or still unextracted; leave extracted items alone."""
    rows = store.get_rows(session, (store.item_key(i.source, i.source_id) for i in items))
    for item in items:
        row = rows.get(store.item_key(item.source, item.source_id))
        if row is not None and row.extracted_at_utc is not None:
            report.cached += 1
            if row.content_sha256 != item.content_sha256:
                # Kept as first read: the stored fields are what was knowable at
                # first fetch, and re-extracting an edit would date a later view
                # to the original publish time.
                log.info("%s edited since first fetch; keeping the first extraction", row.item_id)
            continue
        extraction = extractor(item.text)
        store.upsert_item(
            session,
            source=item.source,
            source_id=item.source_id,
            channel_id=item.channel_id,
            url=item.url,
            published_at=item.published_at,
            content_sha256=item.content_sha256,
            now=now,
            expires_at=expires_at,
            extraction=extraction,
            extraction_model=model if extraction is not None else None,
        )
        if row is None:
            report.new_items += 1
        if extraction is None:
            report.extraction_failed += 1
        else:
            report.extracted += 1


def _youtube_step(
    sessions: SessionFactory,
    client: YouTubeClient,
    *,
    extractor: Extractor,
    model: str,
    now: datetime,
    lookback: timedelta,
    max_pages: int,
    report: IngestReport,
) -> None:
    items = fetch_recent(
        client, config.YOUTUBE_CHANNEL_ID, since=now - lookback, max_pages=max_pages
    )
    report.fetched[YOUTUBE] = len(items)
    with sessions() as s:
        _store(
            s, items, extractor=extractor, model=model, now=now,
            expires_at=now + config.YOUTUBE_RETENTION, report=report,
        )


def _x_check_deletions(
    sessions: SessionFactory, client: XClient, *, report: IngestReport
) -> dict[str, dict[str, Any]]:
    """Purge stored posts X no longer serves; return the ones it still does."""
    with sessions() as s:
        stored = store.source_ids(s, X)
        if not stored:
            return {}
        alive = client.lookup_alive(stored)
        gone = [store.item_key(X, pid) for pid in stored if pid not in alive]
        report.purged_deleted += store.purge(s, gone)
        return alive


def _x_retry_extraction(
    sessions: SessionFactory,
    alive: dict[str, dict[str, Any]],
    *,
    extractor: Extractor,
    model: str,
    now: datetime,
    report: IngestReport,
) -> None:
    """Extract stored posts whose extraction failed, from the text the deletion check read."""
    with sessions() as s:
        rows = store.get_rows(s, (store.item_key(X, pid) for pid in alive))
        retry = [
            item
            for row in rows.values()
            if row.extracted_at_utc is None
            and (item := to_raw_item(alive[row.source_id], row.channel_id)) is not None
        ]
        if retry:
            # These rows exist, so `upsert_item` keeps their first-fetch expiry.
            _store(
                s, retry, extractor=extractor, model=model, now=now,
                expires_at=now + config.x_retention(), report=report,
            )


def _retain(
    sessions: SessionFactory, x: XClient | None, *, now: datetime, report: IngestReport
) -> dict[str, dict[str, Any]] | None:
    """The retention half of a pass: expiry, then X deletions.

    Returns the stored posts X still serves, or None when X was not read.
    """
    with sessions() as s:
        report.purged_expired += store.purge_expired(s, now)
    if x is None:
        report.notes.append("X skipped: X_BEARER_TOKEN is not set")
        # Without the API there is no way to see a deletion, so nothing from X
        # may stay: keeping it would be keeping posts that might be gone.
        with sessions() as s:
            report.purged_deleted += store.purge_source(s, X)
        return None
    try:
        return _x_check_deletions(sessions, x, report=report)
    except Exception as exc:  # noqa: BLE001
        log.warning("X deletion reconcile failed", exc_info=True)
        report.notes.append(f"X reconcile failed: {type(exc).__name__}")
        return None


def enforce_retention(
    sessions: SessionFactory, *, x: XClient | None, now: datetime | None = None
) -> IngestReport:
    """The retention pass alone: no YouTube call, no extraction, no fetch.

    Deletes expired items and checks X deletions (with a token; without one,
    every stored X post goes). Then records that it ran, which is what lets
    the X fetch store new posts. Raises only when the store itself fails, so
    the timer that runs it pages.
    """
    now = now or datetime.now(UTC)
    report = IngestReport()
    _retain(sessions, x, now=now, report=report)
    with sessions() as s:
        store.record_status(s, store.RETENTION_PASS, now)
    return report


def _x_fetch(
    sessions: SessionFactory,
    client: XClient,
    user_id: str,
    *,
    extractor: Extractor,
    model: str,
    now: datetime,
    report: IngestReport,
) -> None:
    with sessions() as s:
        since_id = store.newest_numeric_id(s, X)
    items = fetch_new(
        client, user_id, since_id=since_id,
        first_run_max=config.X_FIRST_RUN_MAX,
        expected_username=config.X_EXPECTED_USERNAME,
    )
    report.fetched[X] = len(items)
    with sessions() as s:
        _store(
            s, items, extractor=extractor, model=model, now=now,
            expires_at=now + config.x_retention(), report=report,
        )


def run(
    sessions: SessionFactory,
    *,
    youtube: YouTubeClient | None,
    x: XClient | None,
    x_user_id: str | None,
    extractor: Extractor,
    model: str,
    now: datetime | None = None,
    lookback: timedelta | None = None,
    youtube_max_pages: int = 1,
) -> IngestReport:
    """One ingest pass. Never raises for a source failure; the report says what happened."""
    now = now or datetime.now(UTC)
    lookback = lookback or config.ingest_lookback()
    report = IngestReport()

    alive = _retain(sessions, x, now=now, report=report)

    if youtube is None:
        report.notes.append("YouTube skipped: YOUTUBE_API_KEY is not set")
    else:
        try:
            _youtube_step(
                sessions, youtube, extractor=extractor, model=model, now=now,
                lookback=lookback, max_pages=youtube_max_pages, report=report,
            )
        except Exception as exc:  # noqa: BLE001 — one source must not cost the other
            log.warning("YouTube fetch failed", exc_info=True)
            report.notes.append(f"YouTube failed: {type(exc).__name__}")

    if x is None:
        return report
    if alive:
        try:
            _x_retry_extraction(
                sessions, alive, extractor=extractor, model=model, now=now, report=report
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("X extraction retry failed", exc_info=True)
            report.notes.append(f"X extraction retry failed: {type(exc).__name__}")
    if x_user_id is None:
        report.notes.append("X fetch skipped: no numeric user id pinned (see config.X_USER_ID)")
        return report
    with sessions() as s:
        last_pass = store.status_at(s, store.RETENTION_PASS)
    if last_pass is None or now - last_pass > config.RETENTION_PASS_MAX_AGE:
        # This run checks deletions on weekdays only; the daily pass is what
        # covers weekends and kill-switch days. No pass, no new posts stored.
        report.notes.append(
            "X fetch skipped: the daily retention pass has not run in the last "
            f"{config.RETENTION_PASS_MAX_AGE.total_seconds() / 3600:.0f}h "
            "(enable ai-trader-commentator-retention.timer)"
        )
        return report
    try:
        _x_fetch(
            sessions, x, x_user_id, extractor=extractor, model=model, now=now, report=report
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("X fetch failed", exc_info=True)
        report.notes.append(f"X failed: {type(exc).__name__}")
    return report
