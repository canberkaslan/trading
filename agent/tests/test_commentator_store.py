"""Commentator storage: new tables, retention, purge, and the decision link (ADR-009)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.pool import StaticPool

from tradingagents_us.schemas import AgentDecision
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage import commentator as store
from tradingagents_us.storage.commentator import Extraction
from tradingagents_us.storage.models import CommentatorItemRow, DecisionCommentatorRefRow

NOW = datetime(2026, 9, 28, 22, 30, tzinfo=UTC)
EXTRACTION = Extraction(
    tickers=("META",), macro_topics=(), stance={"META": "bullish"},
    claim_en="META model launch is a catalyst.", is_promo=False, is_market_content=True,
)


@pytest.fixture
def repo() -> TradeLogRepository:
    return TradeLogRepository(engine=create_engine("sqlite://", future=True))


def _put(repo: TradeLogRepository, source: str, sid: str, *, expires: datetime,
         extraction: Extraction | None = EXTRACTION) -> None:
    with repo.session() as s:
        store.upsert_item(
            s, source=source, source_id=sid, channel_id="c", url=f"https://e/{sid}",
            published_at=NOW - timedelta(days=1), content_sha256="h", now=NOW,
            expires_at=expires, extraction=extraction, extraction_model="m",
        )


def _decision(dec_id: str) -> AgentDecision:
    return AgentDecision(
        ticker="META", market="US", quote_currency="USD", rating="Hold",
        reasoning=[], timestamp_utc=NOW, decision_id=dec_id,
    )


class TestTables:
    def test_create_all_builds_both(self, repo: TradeLogRepository) -> None:
        names = set(inspect(repo.engine).get_table_names())
        assert {"commentator_items", "decision_commentator_refs", "commentator_status"} <= names

    def test_a_legacy_database_gets_them_without_touching_agent_decisions(
        self, tmp_path
    ) -> None:
        # The box's DB predates these tables. create_all adds missing TABLES,
        # which is all this needs: no column is added to an existing table, so
        # no _ADDITIVE_COLUMNS entry and no migration.
        engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", future=True)
        TradeLogRepository(engine=engine)
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE commentator_items"))
            conn.execute(text("DROP TABLE decision_commentator_refs"))
        before = [c["name"] for c in inspect(engine).get_columns("agent_decisions")]
        TradeLogRepository(engine=engine)
        insp = inspect(engine)
        assert {"commentator_items", "decision_commentator_refs"} <= set(insp.get_table_names())
        assert [c["name"] for c in insp.get_columns("agent_decisions")] == before


class TestRetention:
    def test_expired_items_go_and_fresh_ones_stay(self, repo: TradeLogRepository) -> None:
        _put(repo, "youtube", "old", expires=NOW - timedelta(seconds=1))
        _put(repo, "youtube", "new", expires=NOW + timedelta(days=29))
        with repo.session() as s:
            assert store.purge_expired(s, NOW) == 1
        with repo.session() as s:
            assert set(s.scalars(select(CommentatorItemRow.source_id))) == {"new"}

    def test_a_retry_does_not_extend_the_deadline(self, repo: TradeLogRepository) -> None:
        _put(repo, "youtube", "v", expires=NOW + timedelta(days=30), extraction=None)
        with repo.session() as s:
            store.upsert_item(
                s, source="youtube", source_id="v", channel_id="c", url="u",
                published_at=NOW, content_sha256="h", now=NOW + timedelta(days=5),
                expires_at=NOW + timedelta(days=35), extraction=EXTRACTION,
                extraction_model="m",
            )
        with repo.session() as s:
            row = s.get(CommentatorItemRow, "youtube:v")
            assert row is not None
            assert store.aware(row.expires_at_utc) == NOW + timedelta(days=30)
            assert store.aware(row.fetched_at_utc) == NOW  # first seen, never re-dated
            assert row.extracted_at_utc is not None

    def test_only_extracted_items_are_readable(self, repo: TradeLogRepository) -> None:
        _put(repo, "youtube", "done", expires=NOW + timedelta(days=1))
        _put(repo, "youtube", "pending", expires=NOW + timedelta(days=1), extraction=None)
        with repo.session() as s:
            got = store.extracted_items_between(s, NOW - timedelta(days=7), NOW, now=NOW)
        assert [i.source_id for i in got] == ["done"]

    def test_an_expired_item_is_never_read_even_before_the_purge(
        self, repo: TradeLogRepository
    ) -> None:
        # The purge runs once a day; between its runs an expired row still
        # exists and must not reach a prompt.
        _put(repo, "youtube", "expired", expires=NOW - timedelta(seconds=1))
        _put(repo, "youtube", "live", expires=NOW + timedelta(days=1))
        with repo.session() as s:
            got = store.extracted_items_between(s, NOW - timedelta(days=7), NOW, now=NOW)
        assert [i.source_id for i in got] == ["live"]

    def test_a_deletion_checked_source_is_read_only_while_its_check_is_fresh(
        self, repo: TradeLogRepository
    ) -> None:
        _put(repo, "x", "fresh", expires=NOW + timedelta(days=8))
        _put(repo, "x", "stale", expires=NOW + timedelta(days=8))
        _put(repo, "x", "never", expires=NOW + timedelta(days=8))
        _put(repo, "youtube", "yt", expires=NOW + timedelta(days=8))
        with repo.session() as s:
            store.mark_verified(s, ["x:fresh"], NOW - timedelta(hours=1))
            store.mark_verified(s, ["x:stale"], NOW - timedelta(hours=25))
        with repo.session() as s:
            got = store.extracted_items_between(s, NOW - timedelta(days=7), NOW, now=NOW)
        assert sorted(i.item_id for i in got) == ["x:fresh", "youtube:yt"]

    def test_x_is_the_deletion_checked_source(self) -> None:
        from tradingagents_us.dataflows.commentator.items import X

        assert frozenset({X}) == store.DELETION_CHECKED_SOURCES

    def test_reads_chain_while_each_reaches_back_to_the_last(
        self, repo: TradeLogRepository
    ) -> None:
        with repo.session() as s:
            store.record_read(s, "youtube", at=NOW, covered_since=NOW - timedelta(days=120))
        with repo.session() as s:  # the next day's routine read overlaps: the backfill holds
            store.record_read(s, "youtube", at=NOW + timedelta(days=1),
                              covered_since=NOW - timedelta(days=13))
        with repo.session() as s:
            (r,) = store.source_reads(s)
        assert r.source == "youtube" and r.read_at == NOW + timedelta(days=1)
        assert r.covered_since == NOW - timedelta(days=120)

        with repo.session() as s:  # a read that starts after the last one: a gap
            store.record_read(s, "youtube", at=NOW + timedelta(days=30),
                              covered_since=NOW + timedelta(days=16))
        with repo.session() as s:
            (r,) = store.source_reads(s)
        assert r.covered_since == NOW + timedelta(days=16)

    def test_purging_a_source_forgets_its_reads(self, repo: TradeLogRepository) -> None:
        with repo.session() as s:
            store.record_read(s, "x", at=NOW, covered_since=NOW - timedelta(days=5))
            store.record_read(s, "youtube", at=NOW, covered_since=NOW - timedelta(days=5))
        with repo.session() as s:
            store.purge_source(s, "x")
        with repo.session() as s:
            assert [r.source for r in store.source_reads(s)] == ["youtube"]

    def test_a_status_is_recorded_and_overwritten(self, repo: TradeLogRepository) -> None:
        with repo.session() as s:
            assert store.status_at(s, store.RETENTION_PASS) is None
            store.record_status(s, store.RETENTION_PASS, NOW)
        with repo.session() as s:
            store.record_status(s, store.RETENTION_PASS, NOW + timedelta(days=1))
        with repo.session() as s:
            assert store.status_at(s, store.RETENTION_PASS) == NOW + timedelta(days=1)


class TestPurgeAndRefs:
    def test_a_purged_item_is_scrubbed_from_refs_not_the_decision_record(
        self, repo: TradeLogRepository
    ) -> None:
        _put(repo, "x", "1001", expires=NOW + timedelta(days=8))
        store.stash_decision_refs("dec-1", [("x", "x:1001")])
        repo.save_decision(_decision("dec-1"))
        with repo.session() as s:
            assert store.purge(s, ["x:1001"]) == 1
        with repo.session() as s:
            (ref,) = s.scalars(select(DecisionCommentatorRefRow)).all()
            assert ref.decision_id == "dec-1"
            assert ref.source == "x"
            assert ref.item_id is None  # the post id is gone, the fact of the input stays
            assert s.get(CommentatorItemRow, "x:1001") is None

    def test_save_decision_writes_stashed_refs_once(self, repo: TradeLogRepository) -> None:
        store.stash_decision_refs("dec-2", [("youtube", "youtube:a"), ("youtube", "youtube:b")])
        repo.save_decision(_decision("dec-2"))
        repo.save_decision(_decision("dec-2"))  # a re-save must not duplicate them
        with repo.session() as s:
            refs = s.scalars(select(DecisionCommentatorRefRow.item_id)).all()
        assert sorted(refs) == ["youtube:a", "youtube:b"]

    def test_with_nothing_stashed_save_decision_writes_no_refs(
        self, repo: TradeLogRepository
    ) -> None:
        repo.save_decision(_decision("dec-3"))
        with repo.session() as s:
            assert s.scalars(select(DecisionCommentatorRefRow)).all() == []

    def test_the_stash_is_bounded(self) -> None:
        for i in range(store._STASH_LIMIT + 10):
            store.stash_decision_refs(f"bt-{i}", [("youtube", f"youtube:{i}")])
        assert store.pop_decision_refs("bt-0") == []  # the oldest fell off
        last = store._STASH_LIMIT + 9
        assert store.pop_decision_refs(f"bt-{last}") == [("youtube", f"youtube:{last}")]
        with store._stash_lock:
            store._stash.clear()

    def test_since_id_is_the_numerically_newest(self, repo: TradeLogRepository) -> None:
        for sid in ("999", "1000", "99"):
            _put(repo, "x", sid, expires=NOW + timedelta(days=8))
        with repo.session() as s:
            assert store.newest_numeric_id(s, "x") == "1000"  # not "999"


class TestPurgedBytes:
    """A purge removes the bytes from local.db, not just the row (ADR-009).

    Each step opens its own engine, as the fetch, the daily run and the
    retention timer each run in their own process: a setting one connection
    turned on must not be what makes another step's write safe.
    """

    MARK = "PARAPHRASE-OF-A-PURGED-ITEM-3c9d"
    ID_PREFIX = "77009900"
    N = 120

    @contextmanager
    def _process(self, db: Path) -> Iterator[TradeLogRepository]:
        repo = TradeLogRepository(engine=create_engine(f"sqlite:///{db}", future=True))
        try:
            yield repo
        finally:
            repo.engine.dispose()

    def _live_cycle(self, db: Path) -> None:
        """Store, extract later, re-verify and cite X items, as a live feed would."""
        ids = [f"{self.ID_PREFIX}{i:04d}" for i in range(self.N)]
        for extracted in (False, True):  # the first extraction failed; a retry filled it in
            with self._process(db) as repo, repo.session() as s:
                for i, sid in enumerate(ids):
                    extraction = Extraction(
                        tickers=("META",), macro_topics=("Fed rates",),
                        stance={"META": "bullish"}, claim_en=f"{self.MARK} {i} " + "m" * 120,
                        is_promo=False, is_market_content=True,
                    )
                    store.upsert_item(
                        s, source="x", source_id=sid, channel_id="c",
                        url=f"https://x.com/i/{sid}", published_at=NOW, content_sha256="h",
                        now=NOW, expires_at=NOW + timedelta(days=8),
                        extraction=extraction if extracted else None,
                        extraction_model="m", verified_at=NOW,
                    )
        keys = [store.item_key("x", sid) for sid in ids]
        for hours in (1, 2, 3):  # the deletion check re-verifies them
            with self._process(db) as repo, repo.session() as s:
                store.mark_verified(s, keys, NOW + timedelta(hours=hours, microseconds=hours))
        with self._process(db) as repo:  # the daily run cites them
            for d in range(10):
                store.stash_decision_refs(f"dec-{d}", [("x", k) for k in keys[d::10]])
                repo.save_decision(_decision(f"dec-{d}"))

    @pytest.mark.parametrize("purge", ["deleted", "expired"])
    def test_nothing_of_a_purged_item_is_left_in_the_file(
        self, tmp_path: Path, purge: str
    ) -> None:
        db = tmp_path / "local.db"
        self._live_cycle(db)
        with self._process(db) as repo:
            with repo.session() as s:
                if purge == "deleted":
                    assert store.purge_source(s, "x") == self.N
                else:
                    assert store.purge_expired(s, NOW + timedelta(days=9)) == self.N
            with repo.session() as s:
                refs = s.scalars(select(DecisionCommentatorRefRow.item_id)).all()
        assert len(refs) == self.N and set(refs) == {None}
        raw = db.read_bytes()
        assert self.MARK.encode() not in raw  # the paraphrase
        assert self.ID_PREFIX.encode() not in raw  # the post ids, in items and in refs

    def test_the_setting_is_left_alone_while_nothing_touches_the_feed(self) -> None:
        # Flag off: nothing is stashed and the retention pass finds nothing, so
        # the trade log's connection is exactly what it was before ADR-009.
        repo = TradeLogRepository(
            engine=create_engine("sqlite://", future=True, poolclass=StaticPool)
        )
        with repo.engine.connect() as c:
            before = c.exec_driver_sql("PRAGMA secure_delete").scalar()
        repo.save_decision(_decision("dec-off"))
        with repo.session() as s:
            assert store.purge_expired(s, NOW) == 0
        with repo.engine.connect() as c:
            assert c.exec_driver_sql("PRAGMA secure_delete").scalar() == before == 0
