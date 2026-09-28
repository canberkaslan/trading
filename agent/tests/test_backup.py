"""The off-box backup of local.db carries no commentator feed table (ADR-009).

The backup is kept for good (git history, dated S3 keys), while YouTube API
data must be gone 30 days after its fetch and a deleted X post within a day.
The checks below read the artifact's raw bytes, not just its rows: the sqlite
backup API copies free pages too, so a row the live DB already purged can
still be in the file.

The decision in the fixture carries a sentiment report written the way the
block's guidance asks, citing the item it was shown. That column is where the
feed ends up once a decision is saved: the report is kept whole, so it must
never hold a platform id, and what the analyst wrote about a claim is kept by
decision (ADR-009, "Reports keep what the analyst wrote").
"""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from scripts import backup
from tradingagents_us.dataflows import commentator_supplement as cs
from tradingagents_us.schemas import AgentDecision, AgentReasoning
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage import commentator as store
from tradingagents_us.storage.commentator import Extraction

NOW = datetime(2026, 9, 28, 22, 30, tzinfo=UTC)
LIVE_MARK = "LIVE-PARAPHRASE-5d1c"
PURGED_MARK = "PURGED-PARAPHRASE-9b7e"
DECISION_TEXT = "HOLD META: the decision text the backup exists to keep."
ANALYST_MARK = "ANALYST-OWN-WORDS-3f2a"


def _extraction(claim: str) -> Extraction:
    return Extraction(
        tickers=("META",), macro_topics=(), stance={"META": "bullish"},
        claim_en=claim, is_promo=False, is_market_content=True,
    )


def _put(repo: TradeLogRepository, source: str, sid: str, claim: str) -> None:
    with repo.session() as s:
        store.upsert_item(
            s, source=source, source_id=sid, channel_id="c", url=f"https://e/{sid}",
            published_at=NOW - timedelta(days=1), content_sha256="h", now=NOW,
            expires_at=NOW + timedelta(days=8), extraction=_extraction(claim),
            extraction_model="m", verified_at=NOW,
        )


def _sentiment_report(repo: TradeLogRepository) -> str:
    """The analyst's report, citing the item by the ref its real prompt showed."""
    with repo.session() as s:
        items = store.extracted_items_between(
            s, NOW - timedelta(days=7), NOW + timedelta(days=1), now=NOW
        )
    block = cs.render("META", items, [], start_date="2026-09-21", end_date="2026-09-28")
    line = next(ln for ln in block.splitlines() if ln.startswith("- 2026-"))
    ref = line[line.index("["): line.index("]") + 1]
    return (
        "**Overall Sentiment:** **Mildly Bullish** (Score: 6.0/10)\n"
        "**Confidence:** Low\n\nNews flow is mixed; Reddit is quiet.\n"
        f"Commentator view: {ref} {ANALYST_MARK}, one opinion, weighed lightly.\n"
    )


def _restore(tmp_path: Path, artifact: bytes) -> sqlite3.Connection:
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(artifact))
    return sqlite3.connect(restored)


@pytest.fixture
def live_db(tmp_path: Path) -> Path:
    """A box DB with a decision, a live feed item it read, and a purged one."""
    db = tmp_path / "local.db"
    repo = TradeLogRepository(engine=create_engine(f"sqlite:///{db}", future=True))
    _put(repo, "youtube", "iIVDlDLd9yk", LIVE_MARK)
    with repo.session() as s:
        store.record_read(s, "youtube", at=NOW, covered_since=NOW - timedelta(days=14))
    store.stash_decision_refs("dec-1", [("youtube", "youtube:iIVDlDLd9yk")])
    report = AgentReasoning(
        agent="sentiment_analyst", model="m", summary=_sentiment_report(repo),
        tokens_in=0, tokens_out=0, latency_ms=0,
    )
    repo.save_decision(AgentDecision(
        ticker="META", market="US", quote_currency="USD", rating="Hold", reasoning=[report],
        timestamp_utc=NOW, decision_id="dec-1", final_decision_text=DECISION_TEXT,
    ))
    repo.engine.dispose()
    # A row deleted the plain way, as any DB written before secure_delete was
    # on holds: unlinked, its bytes left in free space.
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA secure_delete = OFF")
    stamp = NOW.isoformat(sep=" ")
    with conn:
        for i in range(40):
            conn.execute(
                "INSERT INTO commentator_items (item_id, source, source_id, channel_id, url,"
                " published_at_utc, fetched_at_utc, expires_at_utc, content_sha256, claim_en,"
                " tickers_json, macro_topics_json, stance_json)"
                " VALUES (?, 'x', ?, 'c', 'u', ?, ?, ?, 'h', ?, '[]', '[]', '{}')",
                (f"x:{2000 + i}", str(2000 + i), stamp, stamp, stamp, f"{PURGED_MARK} {i}"),
            )
    with conn:
        conn.execute("DELETE FROM commentator_items WHERE source = 'x'")
    conn.close()
    assert PURGED_MARK.encode() in db.read_bytes()  # the residue this guards against
    return db


class TestFeedStaysOnTheBox:
    def test_no_feed_table_bytes_live_or_purged_reach_the_artifact(
        self, live_db: Path
    ) -> None:
        raw = gzip.decompress(backup._dump_sqlite_gz(live_db))
        assert LIVE_MARK.encode() not in raw
        assert PURGED_MARK.encode() not in raw

    def test_no_platform_id_reaches_the_artifact(self, live_db: Path) -> None:
        # Not in items, not in refs, and not in the stored report either: the
        # report cites the per-block label its prompt showed, never the id.
        assert b"iIVDlDLd9yk" in live_db.read_bytes()  # live, the item still has it
        raw = gzip.decompress(backup._dump_sqlite_gz(live_db))
        assert b"iIVDlDLd9yk" not in raw

    def test_reports_keep_what_the_analyst_wrote(self, live_db: Path, tmp_path: Path) -> None:
        # A decision, recorded in ADR-009: the report is the record and is kept
        # whole, including the analyst's own words about a claim.
        conn = _restore(tmp_path, backup._dump_sqlite_gz(live_db))
        (reasoning,) = conn.execute("SELECT reasoning_json FROM agent_decisions").fetchone()
        assert f"Commentator view: [C1, YouTube] {ANALYST_MARK}" in reasoning

    def test_the_feed_tables_are_empty_and_the_ref_keeps_only_its_source(
        self, live_db: Path, tmp_path: Path
    ) -> None:
        conn = _restore(tmp_path, backup._dump_sqlite_gz(live_db))
        assert conn.execute("SELECT count(*) FROM commentator_items").fetchone() == (0,)
        # A read record left behind would vouch for reads whose items are gone.
        assert conn.execute("SELECT count(*) FROM commentator_status").fetchone() == (0,)
        assert conn.execute(
            "SELECT decision_id, source, item_id FROM decision_commentator_refs"
        ).fetchall() == [("dec-1", "youtube", None)]

    def test_the_rest_of_the_audit_trail_is_kept(self, live_db: Path, tmp_path: Path) -> None:
        conn = _restore(tmp_path, backup._dump_sqlite_gz(live_db))
        assert conn.execute(
            "SELECT decision_id, final_decision_text FROM agent_decisions"
        ).fetchall() == [("dec-1", DECISION_TEXT)]

    def test_the_live_db_is_only_read(self, live_db: Path) -> None:
        backup._dump_sqlite_gz(live_db)
        conn = sqlite3.connect(live_db)
        assert conn.execute("SELECT item_id FROM commentator_items").fetchall() == [
            ("youtube:iIVDlDLd9yk",)
        ]
        assert conn.execute("SELECT item_id FROM decision_commentator_refs").fetchall() == [
            ("youtube:iIVDlDLd9yk",)
        ]

    def test_a_db_that_predates_the_feed_backs_up_whole(self, tmp_path: Path) -> None:
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        with conn:
            conn.execute("CREATE TABLE agent_decisions (decision_id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO agent_decisions VALUES ('dec-0')")
        conn.close()
        restored = _restore(tmp_path, backup._dump_sqlite_gz(db))
        assert restored.execute("SELECT decision_id FROM agent_decisions").fetchall() == [
            ("dec-0",)
        ]
