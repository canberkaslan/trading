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

No transaction is open across a network or model call. Each step reads what
it needs in one short session, fetches and extracts with none open, and
writes in another short session: on SQLite a write transaction holds the
database's only write lock, and a paid extraction (seconds each, bounded by
`extract.timeout_s()`) must not make the decision save or the API wait.
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
from .failures import describe, traceback_of
from .items import YOUTUBE, RawItem, X
from .x_source import XClient, fetch_new, to_raw_item
from .youtube_source import YouTubeClient, fetch_window

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


#: After this many extraction failures in a row, the rest of the step's items
#: are stored unextracted (and retried next run) without a call: an outage or
#: a timeout that hits every call would otherwise cost one timeout per item.
MAX_CONSECUTIVE_EXTRACTION_FAILURES = 3


@dataclass(frozen=True)
class _Extracted:
    """One item and what the extractor made of it (None: failed or not attempted)."""

    item: RawItem
    extraction: Extraction | None


def _extract_pending(
    sessions: SessionFactory,
    items: list[RawItem],
    *,
    extractor: Extractor,
    report: IngestReport,
) -> list[_Extracted]:
    """Extract what is new or still unextracted. No transaction is open during a call.

    One short read to see what is stored, then the paid calls with no session
    at all: on SQLite an open write transaction would hold the database's
    write lock for as long as the model takes, and every other writer (the
    decision save, the API) would wait on it. The writes happen afterwards,
    in `_write_extracted`'s own short transaction.
    """
    keys = [store.item_key(i.source, i.source_id) for i in items]
    with sessions() as s:
        rows = store.get_rows(s, keys)
        # Plain values: the rows do not outlive the session.
        done = {k: r.content_sha256 for k, r in rows.items() if r.extracted_at_utc is not None}
    pending: list[_Extracted] = []
    edited = 0
    failures_in_a_row = 0
    skipped = 0
    for item, key in zip(items, keys, strict=True):
        if key in done:
            report.cached += 1
            if done[key] != item.content_sha256:
                # Kept as first read: the stored fields are what was knowable at
                # first fetch, and re-extracting an edit would date a later view
                # to the original publish time.
                edited += 1
            continue
        extraction: Extraction | None = None
        if failures_in_a_row >= MAX_CONSECUTIVE_EXTRACTION_FAILURES:
            skipped += 1
        else:
            try:
                extraction = extractor(item.text)
            except Exception as exc:  # noqa: BLE001 — stored unextracted, retried next run
                log.warning("commentator extractor raised (%s)", describe(exc))
            failures_in_a_row = 0 if extraction is not None else failures_in_a_row + 1
        pending.append(_Extracted(item, extraction))
    if skipped:
        report.notes.append(
            f"extraction stopped after {MAX_CONSECUTIVE_EXTRACTION_FAILURES} failures in a "
            f"row; {skipped} item(s) stored unextracted without a call, retried next run"
        )
    if edited:
        # A count, not ids: the log outlives the items (see `failures`).
        log.info("%d item(s) edited since first fetch; keeping the first extraction", edited)
    return pending


def _write_extracted(
    session: Session,
    results: list[_Extracted],
    *,
    model: str,
    now: datetime,
    expires_at: datetime,
    report: IngestReport,
    verified_at: datetime | None = None,
    insert_new: bool = True,
) -> None:
    """Store what `_extract_pending` produced, inside one short transaction.

    Rows are re-read here: another pass may have extracted an item while this
    one was calling the model, and its extraction is kept, not overwritten.
    With `insert_new` False (a retry of stored rows) an item whose row is gone
    — purged meanwhile — is dropped, never re-inserted.
    """
    rows = store.get_rows(
        session, (store.item_key(r.item.source, r.item.source_id) for r in results)
    )
    for result in results:
        item, extraction = result.item, result.extraction
        row = rows.get(store.item_key(item.source, item.source_id))
        if row is None and not insert_new:
            continue
        if row is not None and row.extracted_at_utc is not None:
            report.cached += 1
            continue
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
            verified_at=verified_at,
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
    items, covered_since = fetch_window(
        client, config.YOUTUBE_CHANNEL_ID, since=now - lookback, max_pages=max_pages
    )
    report.fetched[YOUTUBE] = len(items)
    results = _extract_pending(sessions, items, extractor=extractor, report=report)
    with sessions() as s:
        _write_extracted(
            s, results, model=model, now=now,
            expires_at=now + config.YOUTUBE_RETENTION, report=report,
        )
        # Committed with the items, so a read is recorded only if it landed.
        # It proves what was fetched, not what was extracted: an item the
        # extractor failed on stays unextracted, and while it is in a window
        # the prompt calls that window unavailable, not empty
        # (commentator_supplement.unread).
        store.record_read(s, YOUTUBE, at=now, covered_since=covered_since or now)


def _x_check_deletions(
    sessions: SessionFactory, client: XClient, *, now: datetime, report: IngestReport
) -> dict[str, dict[str, Any]]:
    """Purge stored posts X no longer serves; stamp and return the ones it still does.

    The lookup runs between two short transactions, not inside one. A post
    stored in between is not in `stored`, so it is neither purged nor stamped.
    """
    with sessions() as s:
        stored = store.source_ids(s, X)
    if not stored:
        return {}
    alive = client.lookup_alive(stored)
    with sessions() as s:
        gone = [store.item_key(X, pid) for pid in stored if pid not in alive]
        report.purged_deleted += store.purge(s, gone)
        store.mark_verified(s, [store.item_key(X, pid) for pid in stored if pid in alive], now)
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
    if not retry:
        return
    results = _extract_pending(sessions, retry, extractor=extractor, report=report)
    with sessions() as s:
        # These rows existed, so `upsert_item` keeps their first-fetch expiry.
        # One purged while the model ran stays purged: a retry never re-inserts.
        _write_extracted(
            s, results, model=model, now=now,
            expires_at=now + config.x_retention(), report=report, insert_new=False,
        )


def _retain(
    sessions: SessionFactory, x: XClient | None, *, now: datetime, report: IngestReport
) -> dict[str, dict[str, Any]] | None:
    """The retention half of a pass: expiry, then X deletions.

    Returns the stored posts X still serves, or None when X was not read.
    """
    with sessions() as s:
        report.purged_expired += store.purge_expired(s, now)
    # Without a deletion check there is no way to see a deletion, so nothing
    # from X may stay: keeping it would be keeping posts that might be gone.
    # That holds for a failed check (revoked token, spend cap, 429, outage)
    # exactly as for a missing token.
    if x is None:
        report.notes.append("X skipped: X_BEARER_TOKEN is not set")
    else:
        try:
            return _x_check_deletions(sessions, x, now=now, report=report)
        except Exception as exc:  # noqa: BLE001
            log.warning("X deletion reconcile failed (%s); purging stored X posts",
                        describe(exc), exc_info=traceback_of(exc))
            report.notes.append(f"X reconcile failed: {describe(exc)}; stored X posts purged")
    with sessions() as s:
        report.purged_deleted += store.purge_source(s, X)
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
        since_row = store.get_rows(s, [store.item_key(X, since_id)]) if since_id else {}
        since_published = next(
            (store.aware(r.published_at_utc) for r in since_row.values()), None
        )
    items = fetch_new(
        client, user_id, since_id=since_id,
        first_run_max=config.X_FIRST_RUN_MAX,
        expected_username=config.X_EXPECTED_USERNAME,
    )
    report.fetched[X] = len(items)
    results = _extract_pending(sessions, items, extractor=extractor, report=report)
    # What this read proves it saw: everything after the since_id post (the
    # earlier reads saw that one), or on a first run the newest posts back to
    # the oldest returned. A since_id is at most a retention window old, so
    # the page cap (300 posts) is never what ends a read of this account.
    if since_id is not None:
        covered_since = since_published or now
    else:
        covered_since = min((i.published_at for i in items), default=now)
    with sessions() as s:
        _write_extracted(
            s, results, model=model, now=now,
            expires_at=now + config.x_retention(), report=report,
            verified_at=now,  # X served it just now
        )
        # As for YouTube: a fetched post whose extraction failed is not an
        # absence; the prompt reads it as unavailable until a retry lands.
        store.record_read(s, X, at=now, covered_since=covered_since)


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
        report.notes.append(f"YouTube skipped: {config.youtube_skip_reason()}")
    else:
        try:
            _youtube_step(
                sessions, youtube, extractor=extractor, model=model, now=now,
                lookback=lookback, max_pages=youtube_max_pages, report=report,
            )
        except Exception as exc:  # noqa: BLE001 — one source must not cost the other
            log.warning("YouTube fetch failed (%s)", describe(exc), exc_info=traceback_of(exc))
            report.notes.append(f"YouTube failed: {describe(exc)}")

    if x is None:
        return report
    if alive:
        try:
            _x_retry_extraction(
                sessions, alive, extractor=extractor, model=model, now=now, report=report
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("X extraction retry failed (%s)", describe(exc),
                        exc_info=traceback_of(exc))
            report.notes.append(f"X extraction retry failed: {describe(exc)}")
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
        log.warning("X fetch failed (%s)", describe(exc), exc_info=traceback_of(exc))
        report.notes.append(f"X failed: {describe(exc)}")
    return report
