"""Commentator items: the per-item cache, the retention rules, the decision link.

ADR-009. Every other news and social source in this system is fetched fresh per
ticker per run, so the same page is downloaded eleven times a night and nothing
of what an analyst read is kept. The commentator feed must not repeat that: an
item is fetched once, extracted once (a paid LLM call), and read from here by
every ticker's sentiment analyst after that.

Retention is enforced here and nowhere else, so there is one place to audit:

- YouTube: at most thirty days after the fetch (API Developer Policies
  III.E.4), then deleted. Nothing re-dates `fetched_at_utc`, and the ingest
  horizon is shorter than the retention, so an expired video is not quietly
  re-ingested the next morning.
- X: a short retention window, and a purge the moment a reconcile finds the
  post deleted or no longer visible (Developer Agreement: honour deletions).
- Reads never return an item past its deadline, so a retention pass that runs
  late cannot put expired data in front of an analyst.

The purge runs every day from its own timer, whatever COMMENTATOR_FEED says
(`ingest.enforce_retention`): data an evaluation stored while the flag was off
must still leave on time.

The decision link is written by `TradeLogRepository.save_decision` from a
small in-process stash (`stash_decision_refs`), because the sentiment analyst
that reads the items runs inside `propagate()`, which has no repository, and
the two persistence paths (the daily run and the on-demand API) both go
through `save_decision`. With the feed off nothing is ever stashed, so the
save writes exactly what it wrote before.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .models import CommentatorItemRow, CommentatorStatusRow, DecisionCommentatorRefRow

#: How many decisions' refs may wait in the stash before the oldest is dropped.
#: A decision that is never saved (a backtest point, a failed run) would
#: otherwise leak its entry for the life of the process.
_STASH_LIMIT = 256

#: `commentator_status` name of the flag-independent daily retention pass.
RETENTION_PASS = "retention"


def item_key(source: str, source_id: str) -> str:
    """The primary key for an item: stable, and readable in a ref row."""
    return f"{source}:{source_id}"


def aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything here is UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@dataclass(frozen=True)
class Extraction:
    """What the extraction pass reads out of one item, in English.

    `stance` maps a ticker to bullish / bearish / neutral / unstated; the broad
    US market is keyed as SPY. `claim_en` is a paraphrase, never a quotation.
    """

    tickers: tuple[str, ...]
    macro_topics: tuple[str, ...]
    stance: Mapping[str, str]
    claim_en: str
    is_promo: bool
    is_market_content: bool


@dataclass(frozen=True)
class StoredItem:
    """What the sentiment supplement reads: derived fields, never source text."""

    item_id: str
    source: str
    source_id: str
    published_at: datetime
    tickers: tuple[str, ...]
    macro_topics: tuple[str, ...]
    stance: Mapping[str, str] = field(default_factory=dict)
    claim_en: str = ""
    is_promo: bool = False
    is_market_content: bool = False


def _to_stored(row: CommentatorItemRow) -> StoredItem:
    return StoredItem(
        item_id=row.item_id,
        source=row.source,
        source_id=row.source_id,
        published_at=aware(row.published_at_utc),
        tickers=tuple(str(t) for t in (row.tickers_json or [])),
        macro_topics=tuple(str(t) for t in (row.macro_topics_json or [])),
        stance={str(k): str(v) for k, v in (row.stance_json or {}).items()},
        claim_en=row.claim_en or "",
        is_promo=bool(row.is_promo),
        is_market_content=bool(row.is_market_content),
    )


# ------------------------------------------------------------------ reads


def get_rows(session: Session, item_ids: Iterable[str]) -> dict[str, CommentatorItemRow]:
    ids = list(item_ids)
    if not ids:
        return {}
    rows = session.scalars(
        select(CommentatorItemRow).where(CommentatorItemRow.item_id.in_(ids))
    )
    return {r.item_id: r for r in rows}


def source_ids(session: Session, source: str) -> list[str]:
    return list(
        session.scalars(
            select(CommentatorItemRow.source_id).where(CommentatorItemRow.source == source)
        )
    )


def newest_numeric_id(session: Session, source: str) -> str | None:
    """Largest stored id for a source whose ids are numeric (X snowflakes).

    Compared as integers: as strings "999" sorts after "1000", and a `since_id`
    that went backwards would re-buy posts already paid for.
    """
    ids = [i for i in source_ids(session, source) if i.isdigit()]
    return max(ids, key=int) if ids else None


def extracted_items_between(
    session: Session, start: datetime, end: datetime, *, now: datetime
) -> list[StoredItem]:
    """Extracted items published in `[start, end)` and not yet expired at `now`, newest first.

    `now` is the wall clock even on a backtest: the deadline is a retention
    rule about the real world, not a point-in-time filter.
    """
    rows = session.scalars(
        select(CommentatorItemRow)
        .where(
            CommentatorItemRow.extracted_at_utc.is_not(None),
            CommentatorItemRow.published_at_utc >= start,
            CommentatorItemRow.published_at_utc < end,
            CommentatorItemRow.expires_at_utc > aware(now),
        )
        .order_by(CommentatorItemRow.published_at_utc.desc())
    )
    return [_to_stored(r) for r in rows]


def status_at(session: Session, name: str) -> datetime | None:
    """When the job `name` last recorded itself, or None if it never has."""
    row = session.get(CommentatorStatusRow, name)
    return aware(row.at_utc) if row is not None else None


# ------------------------------------------------------------------ writes


def upsert_item(
    session: Session,
    *,
    source: str,
    source_id: str,
    channel_id: str,
    url: str,
    published_at: datetime,
    content_sha256: str,
    now: datetime,
    expires_at: datetime,
    extraction: Extraction | None,
    extraction_model: str | None,
) -> CommentatorItemRow:
    """Insert an item, or fill in the extraction of one stored without it.

    An existing row keeps its `fetched_at_utc` (first seen) and its expiry: a
    retry is not a new fetch, and extending the deadline on every retry would
    let a stubborn item outlive the retention rule.
    """
    key = item_key(source, source_id)
    # UTC throughout: SQLite stores no offset, so a non-UTC stamp would compare wrong.
    published_at, now, expires_at = aware(published_at), aware(now), aware(expires_at)
    row = session.get(CommentatorItemRow, key)
    if row is None:
        row = CommentatorItemRow(
            item_id=key,
            source=source,
            source_id=source_id,
            channel_id=channel_id,
            url=url,
            published_at_utc=published_at,
            fetched_at_utc=now,
            expires_at_utc=expires_at,
            content_sha256=content_sha256,
        )
        session.add(row)
    if extraction is not None:
        row.extracted_at_utc = now
        row.extraction_model = extraction_model
        row.tickers_json = list(extraction.tickers)
        row.macro_topics_json = list(extraction.macro_topics)
        row.stance_json = dict(extraction.stance)
        row.claim_en = extraction.claim_en
        row.is_promo = extraction.is_promo
        row.is_market_content = extraction.is_market_content
    return row


def purge(session: Session, item_ids: Sequence[str]) -> int:
    """Delete items and scrub their ids from decision refs. Returns rows deleted."""
    ids = list(item_ids)
    if not ids:
        return 0
    session.execute(
        update(DecisionCommentatorRefRow)
        .where(DecisionCommentatorRefRow.item_id.in_(ids))
        .values(item_id=None)
    )
    result = session.execute(
        delete(CommentatorItemRow).where(CommentatorItemRow.item_id.in_(ids))
    )
    return int(result.rowcount or 0)


def purge_expired(session: Session, now: datetime) -> int:
    """Enforce retention: every item past its deadline goes, whatever its source."""
    expired = list(
        session.scalars(
            select(CommentatorItemRow.item_id).where(CommentatorItemRow.expires_at_utc < now)
        )
    )
    return purge(session, expired)


def record_status(session: Session, name: str, at: datetime) -> None:
    row = session.get(CommentatorStatusRow, name)
    if row is None:
        session.add(CommentatorStatusRow(name=name, at_utc=aware(at)))
    else:
        row.at_utc = aware(at)


def purge_source(session: Session, source: str) -> int:
    """Delete every item from one source (used when its deletions can no longer be checked)."""
    return purge(
        session,
        list(
            session.scalars(
                select(CommentatorItemRow.item_id).where(CommentatorItemRow.source == source)
            )
        ),
    )


# ------------------------------------------------------------------ decision link

_stash_lock = threading.Lock()
_stash: OrderedDict[str, list[tuple[str, str]]] = OrderedDict()


def stash_decision_refs(decision_id: str, refs: Sequence[tuple[str, str]]) -> None:
    """Remember `(source, item_id)` pairs until the decision is saved."""
    if not refs:
        return
    with _stash_lock:
        _stash[decision_id] = list(refs)
        while len(_stash) > _STASH_LIMIT:
            _stash.popitem(last=False)


def pop_decision_refs(decision_id: str) -> list[tuple[str, str]]:
    with _stash_lock:
        return _stash.pop(decision_id, [])


def write_decision_refs(session: Session, decision_id: str) -> int:
    """Persist the stashed refs for a decision being saved in `session`.

    Popped, so a re-save of the same decision does not write them twice. The
    decision row is flushed first because the ref carries a foreign key to it.
    """
    refs = pop_decision_refs(decision_id)
    if not refs:
        return 0
    session.flush()
    now = datetime.now(UTC)
    for source, key in refs:
        session.add(
            DecisionCommentatorRefRow(
                decision_id=decision_id, source=source, item_id=key, created_at_utc=now
            )
        )
    return len(refs)
