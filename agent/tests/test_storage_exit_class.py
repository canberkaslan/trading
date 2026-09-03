"""Persisting the exit class onto an ALREADY-DEPLOYED closed_trades table.

Two things can go wrong here and neither shows up in a test that starts from an
empty database, which is why these build the "before" state by hand:

  1. `create_all` creates missing TABLES, not missing COLUMNS. The box has had a
     `closed_trades` table since 2026-08-09, so shipping a new mapped column
     reaches it as `no such column` on the first SELECT — the model asks for
     something the file does not have. A fresh-database test passes happily.
  2. `upsert_closed_trades` merges every column it is given. Attribution comes
     from the broker's ORDER history, which is pruned and can be unreadable
     when the fill feed is fine, so an unguarded pass-through would let one bad
     run write NULL over the whole ledger's classes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from tradingagents_us.execution.reconcile import ClosedTrade
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.repository import ensure_additive_columns

T0 = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)

#: `closed_trades` exactly as it exists on the box today — every column the
#: model had before attribution was added, and not one more.
_LEGACY_CLOSED_TRADES = """
CREATE TABLE closed_trades (
    trade_id VARCHAR(64) NOT NULL PRIMARY KEY,
    symbol VARCHAR(16),
    direction VARCHAR(8),
    quantity FLOAT,
    entry_price FLOAT,
    exit_price FLOAT,
    realized_pnl FLOAT,
    realized_pnl_pct FLOAT,
    holding_days FLOAT,
    opened_at_utc DATETIME,
    closed_at_utc DATETIME,
    open_activity_id VARCHAR(64),
    close_activity_id VARCHAR(64),
    reconciled_at_utc DATETIME
)
"""


def _trade(tid: str, pnl: float = 100.0, day: int = 0) -> ClosedTrade:
    return ClosedTrade(
        trade_id=tid,
        symbol="AAPL",
        direction="LONG",
        quantity=10.0,
        entry_price=100.0,
        exit_price=100.0 + pnl / 10.0,
        opened_at_utc=T0 + timedelta(days=day),
        closed_at_utc=T0 + timedelta(days=day + 2),
        realized_pnl=pnl,
        realized_pnl_pct=pnl / 1000.0,
        holding_days=2.0,
        open_activity_id=f"o-{tid}",
        close_activity_id=f"c-{tid}",
    )


def _legacy_db(tmp_path: Path):
    """An engine whose closed_trades table predates the exit_class column."""
    engine = create_engine(f"sqlite:///{tmp_path/'legacy.db'}", future=True)
    with engine.begin() as conn:
        conn.execute(text(_LEGACY_CLOSED_TRADES))
    return engine


class TestAdditiveColumns:
    def test_an_existing_table_gains_the_new_column(self, tmp_path: Path) -> None:
        engine = _legacy_db(tmp_path)
        before = {c["name"] for c in inspect(engine).get_columns("closed_trades")}
        assert "exit_class" not in before

        added = ensure_additive_columns(engine)

        assert added == ["closed_trades.exit_class"]
        after = {c["name"] for c in inspect(engine).get_columns("closed_trades")}
        assert "exit_class" in after

    def test_the_repository_opens_a_legacy_database_and_can_read_it(
        self, tmp_path: Path
    ) -> None:
        # The real failure mode, end to end: without the guard this raises
        # OperationalError("no such column: closed_trades.exit_class") the
        # moment anything selects a mapped row.
        engine = _legacy_db(tmp_path)

        repo = TradeLogRepository(engine=engine)
        repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": "stop"})

        rows = repo.list_closed_trades()
        assert [r.exit_class for r in rows] == ["stop"]

    def test_running_twice_adds_nothing_the_second_time(self, tmp_path: Path) -> None:
        engine = _legacy_db(tmp_path)
        ensure_additive_columns(engine)
        assert ensure_additive_columns(engine) == []

    def test_a_fresh_database_needs_no_alter(self, tmp_path: Path) -> None:
        # create_all already builds the table complete from the model.
        engine = create_engine(f"sqlite:///{tmp_path/'fresh.db'}", future=True)
        TradeLogRepository(engine=engine)
        assert ensure_additive_columns(engine) == []


class TestUpsertExitClass:
    def _repo(self, tmp_path: Path) -> TradeLogRepository:
        return TradeLogRepository(
            engine=create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True)
        )

    def test_stores_the_class_it_is_given(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        repo.upsert_closed_trades(
            [_trade("t1"), _trade("t2", day=1)],
            exit_classes={"t1": "take_profit", "t2": "flatten"},
        )
        stored = {r.trade_id: r.exit_class for r in repo.list_closed_trades()}
        assert stored == {"t1": "take_profit", "t2": "flatten"}

    def test_a_row_reconciled_without_classes_is_left_unattributed(
        self, tmp_path: Path
    ) -> None:
        repo = self._repo(tmp_path)
        repo.upsert_closed_trades([_trade("t1")])
        assert repo.list_closed_trades()[0].exit_class is None

    def test_a_later_run_can_fill_in_a_class(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        repo.upsert_closed_trades([_trade("t1")])
        repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": "stop"})
        assert repo.list_closed_trades()[0].exit_class == "stop"

    def test_a_run_with_no_order_history_does_not_erase_what_is_stored(
        self, tmp_path: Path
    ) -> None:
        # The guard that matters: reconcile still runs (fills are readable) but
        # the order feed failed, so it passes no classes. The ledger must keep
        # the attribution it already had rather than silently downgrading to
        # "never attributed".
        repo = self._repo(tmp_path)
        repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": "stop"})

        repo.upsert_closed_trades([_trade("t1")], exit_classes={})

        assert repo.list_closed_trades()[0].exit_class == "stop"

    def test_an_explicit_none_also_leaves_the_stored_class_alone(
        self, tmp_path: Path
    ) -> None:
        repo = self._repo(tmp_path)
        repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": "stop"})
        repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": None})
        assert repo.list_closed_trades()[0].exit_class == "stop"

    def test_a_reclassification_still_wins(self, tmp_path: Path) -> None:
        # Preserving must not become freezing: if the broker's record now says
        # something different, the newer verdict is the one to keep.
        repo = self._repo(tmp_path)
        repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": "unknown"})
        repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": "stop"})
        assert repo.list_closed_trades()[0].exit_class == "stop"

    def test_the_new_row_count_still_only_counts_new_rows(self, tmp_path: Path) -> None:
        repo = self._repo(tmp_path)
        assert repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": "stop"}) == 1
        assert repo.upsert_closed_trades([_trade("t1")], exit_classes={"t1": "stop"}) == 0
