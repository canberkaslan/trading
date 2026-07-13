"""SQLAlchemy declarative models for the trade log.

Three append-only tables, joined on decision_id / order_id:

  agent_decisions  → one row per LLM-driven decision (rating, PT, stop, etc.)
       ↓
  trade_orders     → one row per risk-sized TradeOrder built from a decision
       ↓
  order_updates    → many rows per order, lifecycle events (PENDING → FILLED)

JSONB used for the freeform fields (reasoning blobs, full PM markdown) so
we can later query inside them with PostgreSQL's `->`/`->>`/`@>` operators
without schema migrations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class — also where migrations introspect metadata."""


class AgentDecisionRow(Base):
    __tablename__ = "agent_decisions"

    decision_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    market: Mapped[str] = mapped_column(String(8), default="US")
    quote_currency: Mapped[str] = mapped_column(String(8), default="USD")
    rating: Mapped[str] = mapped_column(String(16))
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_horizon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_size_pct: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning_json: Mapped[Any] = mapped_column(JSON, default=list)
    final_decision_text: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    orders: Mapped[list["TradeOrderRow"]] = relationship(back_populates="decision", cascade="all, delete-orphan")


class TradeOrderRow(Base):
    __tablename__ = "trade_orders"

    order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision_id: Mapped[str] = mapped_column(String(64), ForeignKey("agent_decisions.decision_id"), index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    market: Mapped[str] = mapped_column(String(8), default="US")
    side: Mapped[str] = mapped_column(String(8))             # "BUY" | "SELL"
    quantity: Mapped[int] = mapped_column(Integer)
    order_type: Mapped[str] = mapped_column(String(16))      # "MARKET" | "LIMIT" | ...
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Float)
    risk_approved: Mapped[bool] = mapped_column()
    rejection_reasons_json: Mapped[Any] = mapped_column(JSON, default=list)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    submitted_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    decision: Mapped[AgentDecisionRow] = relationship(back_populates="orders")
    updates: Mapped[list["OrderUpdateRow"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class KillSwitchEventRow(Base):
    """Append-only audit trail for kill-switch changes and executions.

    `source` = who acted (api = mobile POST, daily_run = pre-run check),
    `state` = requested/observed state, `detail` = freeform context (e.g.
    flatten results). New table — created automatically by create_all().
    """

    __tablename__ = "kill_switch_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    state: Mapped[str] = mapped_column(String(16))
    actor: Mapped[str] = mapped_column(String(64), default="unknown")
    source: Mapped[str] = mapped_column(String(32), default="api")
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OrderUpdateRow(Base):
    __tablename__ = "order_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(64), ForeignKey("trade_orders.order_id"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_bps: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    order: Mapped[TradeOrderRow] = relationship(back_populates="updates")
