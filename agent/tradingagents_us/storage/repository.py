"""TradeLogRepository — persist + query decisions, orders, updates.

Engine selection from DATABASE_URL env var. Default 'sqlite:///./local.db'
makes dev frictionless. Production points at Aurora.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..schemas import AgentDecision, AgentReasoning, OrderUpdate, TradeOrder
from .models import AgentDecisionRow, Base, OrderUpdateRow, TradeOrderRow


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

    # ------------------------- reads -------------------------

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
