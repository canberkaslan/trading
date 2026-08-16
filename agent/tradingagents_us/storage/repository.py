"""TradeLogRepository — persist + query decisions, orders, updates.

Engine selection from DATABASE_URL env var. Default 'sqlite:///./local.db'
makes dev frictionless. Production points at Aurora.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Iterator

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


class TradeLogRepository:
    def __init__(self, engine: Engine | None = None) -> None:
        self.engine = engine or make_engine()
        Base.metadata.create_all(self.engine)
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
        from datetime import datetime, timezone

        with self.session() as s:
            s.add(KillSwitchEventRow(
                state=state, actor=actor, source=source, detail=detail,
                timestamp_utc=datetime.now(timezone.utc),
            ))

    def upsert_closed_trades(self, trades: list["ClosedTrade"]) -> int:
        """Persist reconciled round trips; returns the number of NEW rows.

        `merge` on the deterministic trade_id makes a replay idempotent: the
        reconciler re-derives the whole ledger from the fill feed each run, so
        an already-known round trip is updated in place rather than appended.
        The new-row count is what the caller reports — "wrote 40 rows" every
        hour would hide the fact that nothing actually closed.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        new_rows = 0
        with self.session() as s:
            for t in trades:
                if s.get(ClosedTradeRow, t.trade_id) is None:
                    new_rows += 1
                s.merge(ClosedTradeRow(
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

    def list_recent_decisions(self, limit: int = 50, ticker: str | None = None) -> list[AgentDecisionRow]:
        with self.session() as s:
            stmt = select(AgentDecisionRow).order_by(AgentDecisionRow.timestamp_utc.desc()).limit(limit)
            if ticker:
                stmt = stmt.where(AgentDecisionRow.ticker == ticker)
            return list(s.execute(stmt).scalars().all())

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
