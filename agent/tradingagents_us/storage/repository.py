"""TradeLogRepository — persist + query decisions, orders, updates.

Engine selection from DATABASE_URL env var. Default 'sqlite:///./local.db'
makes dev frictionless. Production points at Aurora.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..schemas import AgentDecision, AgentReasoning, OrderUpdate, TradeOrder
from .models import (
    AgentDecisionRow,
    Base,
    ClosedTradeRow,
    KillSwitchEventRow,
    OrderUpdateRow,
    TradeOrderRow,
)

if TYPE_CHECKING:  # avoids a storage → execution import at runtime
    from ..execution.reconcile import ClosedTrade


def make_engine(database_url: str | None = None) -> Engine:
    url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./local.db")
    return create_engine(url, future=True)


#: Columns added to a table that already exists in a deployed database.
#:
#: `create_all` creates missing TABLES and nothing else — it will not touch a
#: table it already sees. So a new column on an existing model ships fine to a
#: fresh database and, on the box, produces `no such column` on the next SELECT:
#: the mapper asks for a column the file does not have. There is no Alembic
#: here, and adding it for one nullable column would be the larger change; this
#: is the narrow substitute, and it is deliberately narrow — additive, nullable,
#: no defaults, no type changes, no drops. Anything beyond that needs a real
#: migration tool, not another entry in this list.
_ADDITIVE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # (table, column, SQL type) — the type is spelled out because this runs as
    # raw DDL on both SQLite (the box) and Postgres (Aurora).
    ("closed_trades", "exit_class", "VARCHAR(16)"),
)


def ensure_additive_columns(engine: Engine) -> list[str]:
    """Add any declared column missing from an existing table. Returns what it added.

    Idempotent: a column already present is skipped, so this is safe on every
    startup. A table that does not exist yet is skipped too — `create_all` will
    have built it complete from the model.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added: list[str] = []
    for table, column, sql_type in _ADDITIVE_COLUMNS:
        if table not in existing_tables:
            continue
        if column in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))
        added.append(f"{table}.{column}")
    return added


class TradeLogRepository:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or make_engine()
        Base.metadata.create_all(self.engine)
        ensure_additive_columns(self.engine)
        # expire_on_commit=False so detached objects retain their column
        # values after the session closes (we return rows by value).
        self._SessionLocal = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ------------------------- writes -------------------------

    def save_decision(self, decision: AgentDecision) -> None:
        with self.session() as s:
            row = AgentDecisionRow(
                decision_id=decision.decision_id,
                ticker=decision.ticker,
                market=decision.market,
                quote_currency=decision.quote_currency,
                rating=decision.rating,
                entry_price=decision.entry_price,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                price_target=decision.price_target,
                time_horizon=decision.time_horizon,
                suggested_size_pct=decision.suggested_size_pct,
                reasoning_json=[r.model_dump() for r in decision.reasoning],
                final_decision_text=decision.final_decision_text,
                timestamp_utc=decision.timestamp_utc,
            )
            s.merge(row)  # idempotent

    def save_order(self, order: TradeOrder, broker_order_id: str | None = None) -> None:
        with self.session() as s:
            row = TradeOrderRow(
                order_id=order.order_id,
                decision_id=order.decision_id,
                ticker=order.ticker,
                market=order.market,
                side=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                limit_price=order.limit_price,
                stop_loss=order.stop_loss,
                risk_approved=order.risk_approved,
                rejection_reasons_json=order.rejection_reasons,
                broker_order_id=broker_order_id,
                submitted_at_utc=order.submitted_at_utc,
            )
            s.merge(row)

    def append_update(self, update: OrderUpdate) -> None:
        with self.session() as s:
            row = OrderUpdateRow(
                order_id=update.order_id,
                status=update.status,
                filled_qty=update.filled_qty,
                avg_fill_price=update.avg_fill_price,
                slippage_bps=update.slippage_bps,
                error_message=update.error_message,
                timestamp_utc=update.timestamp_utc,
            )
            s.add(row)

    def append_kill_event(
        self, state: str, actor: str, source: str, detail: str | None = None
    ) -> None:
        from datetime import datetime

        with self.session() as s:
            s.add(KillSwitchEventRow(
                state=state, actor=actor, source=source, detail=detail,
                timestamp_utc=datetime.now(UTC),
            ))

    def upsert_closed_trades(
        self,
        trades: list[ClosedTrade],
        exit_classes: Mapping[str, str | None] | None = None,
    ) -> int:
        """Persist reconciled round trips; returns the number of NEW rows.

        `merge` on the deterministic trade_id makes a replay idempotent: the
        reconciler re-derives the whole ledger from the fill feed each run, so
        an already-known round trip is updated in place rather than appended.
        The new-row count is what the caller reports — "wrote 40 rows" every
        hour would hide the fact that nothing actually closed.

        `exit_classes` (trade_id → class) is optional because it depends on the
        broker's ORDER history, which is pruned and can be unavailable when the
        fill feed is not. A missing or `None` entry therefore leaves whatever is
        already stored alone rather than writing NULL over it: `merge` sets
        every column it is given, so passing the attribute through unguarded
        would let one order-history failure erase attribution for the whole
        ledger — a silent downgrade that looks exactly like "we never asked".
        """
        from datetime import datetime

        classes = exit_classes or {}
        now = datetime.now(UTC)
        new_rows = 0
        with self.session() as s:
            for t in trades:
                existing = s.get(ClosedTradeRow, t.trade_id)
                if existing is None:
                    new_rows += 1
                exit_class = classes.get(t.trade_id) or (
                    existing.exit_class if existing is not None else None
                )
                s.merge(ClosedTradeRow(
                    exit_class=exit_class,
                    trade_id=t.trade_id,
                    symbol=t.symbol,
                    direction=t.direction,
                    quantity=t.quantity,
                    entry_price=t.entry_price,
                    exit_price=t.exit_price,
                    realized_pnl=t.realized_pnl,
                    realized_pnl_pct=t.realized_pnl_pct,
                    holding_days=t.holding_days,
                    opened_at_utc=t.opened_at_utc,
                    closed_at_utc=t.closed_at_utc,
                    open_activity_id=t.open_activity_id,
                    close_activity_id=t.close_activity_id,
                    reconciled_at_utc=now,
                ))
        return new_rows

    # ------------------------- reads -------------------------

    def list_closed_trades(
        self,
        limit: int = 200,
        ticker: str | None = None,
        opened_since: datetime | None = None,
    ) -> list[ClosedTradeRow]:
        """Most recently closed round trips first.

        `opened_since` filters on the ENTRY, not the exit: a round trip whose
        entry predates the eval window was chosen and sized by the pre-cutoff
        agent, so judging the current book's exit discipline by it is wrong
        even when the exit itself landed after the cutoff. Filtering happens in
        SQL so `limit` counts rows the caller will actually see.
        """
        with self.session() as s:
            stmt = (
                select(ClosedTradeRow)
                .order_by(ClosedTradeRow.closed_at_utc.desc())
                .limit(limit)
            )
            if ticker:
                stmt = stmt.where(ClosedTradeRow.symbol == ticker.upper())
            if opened_since is not None:
                stmt = stmt.where(ClosedTradeRow.opened_at_utc >= opened_since)
            return list(s.execute(stmt).scalars().all())

    def count_closed_trades_opened_before(
        self, cutoff: datetime, ticker: str | None = None
    ) -> int:
        """How many round trips the eval-window cutoff hides.

        Rows dropped from a money screen have to stay countable — "30 trades,
        −$532" quietly becoming "4 trades, +$131" with no explanation reads as
        a bug or, worse, as cherry-picking.
        """
        with self.session() as s:
            stmt = (
                select(func.count())
                .select_from(ClosedTradeRow)
                .where(ClosedTradeRow.opened_at_utc < cutoff)
            )
            if ticker:
                stmt = stmt.where(ClosedTradeRow.symbol == ticker.upper())
            return int(s.execute(stmt).scalar_one())

    def list_recent_decisions(
        self, limit: int = 50, ticker: str | None = None
    ) -> list[AgentDecisionRow]:
        with self.session() as s:
            stmt = (
                select(AgentDecisionRow)
                .order_by(AgentDecisionRow.timestamp_utc.desc())
                .limit(limit)
            )
            if ticker:
                stmt = stmt.where(AgentDecisionRow.ticker == ticker)
            return list(s.execute(stmt).scalars().all())

    def list_orders_since(
        self, since: datetime | None = None, limit: int = 2000
    ) -> list[TradeOrderRow]:
        """Every order row in the window — submitted AND refused, oldest first.

        Deliberately not `list_open_orders`: the refused rows are the point.
        The default limit is generous because the daily run writes one row per
        ticker per day (~11/day), so a month is ~250 rows; a limit that cut in
        mid-window would silently understate the refusal counts computed on top.
        """
        with self.session() as s:
            stmt = (
                select(TradeOrderRow)
                .order_by(TradeOrderRow.submitted_at_utc.desc())
                .limit(limit)
            )
            if since is not None:
                stmt = stmt.where(TradeOrderRow.submitted_at_utc >= since)
            rows = list(s.execute(stmt).scalars().all())
            return sorted(rows, key=lambda r: r.submitted_at_utc)

    def list_open_orders(self) -> list[TradeOrderRow]:
        with self.session() as s:
            # "Open" = no FILLED or REJECTED update yet. Conservative join.
            stmt = select(TradeOrderRow).order_by(TradeOrderRow.submitted_at_utc.desc())
            return list(s.execute(stmt).scalars().all())

    def get_decision(self, decision_id: str) -> AgentDecisionRow | None:
        with self.session() as s:
            return s.get(AgentDecisionRow, decision_id)


def row_to_decision(row: AgentDecisionRow) -> AgentDecision:
    """Convert a stored row back to an AgentDecision pydantic model."""
    return AgentDecision(
        ticker=row.ticker,
        market=row.market,
        quote_currency=row.quote_currency,
        rating=row.rating,
        entry_price=row.entry_price,
        stop_loss=row.stop_loss,
        take_profit=row.take_profit,
        price_target=row.price_target,
        time_horizon=row.time_horizon,
        suggested_size_pct=row.suggested_size_pct,
        reasoning=[AgentReasoning(**r) for r in (row.reasoning_json or [])],
        final_decision_text=row.final_decision_text,
        timestamp_utc=row.timestamp_utc,
        decision_id=row.decision_id,
    )
