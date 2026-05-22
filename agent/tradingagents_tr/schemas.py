"""Pydantic contracts crossing layer boundaries.

These types are the canonical interface between:
  agent -> risk -> executor -> api -> mobile
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Market = Literal["US", "BIST"]
Side = Literal["BUY", "SELL"]
Currency = Literal["USD", "TRY"]
Rating = Literal["Buy", "Overweight", "Hold", "Underweight", "Sell"]
OrderType = Literal["MARKET", "LIMIT"]
OrderStatus = Literal["PENDING", "ACCEPTED", "PARTIAL", "FILLED", "REJECTED", "CANCELLED"]
KillSwitchState = Literal["RUN", "PAUSE_NEW", "FLATTEN_ALL"]


class AgentReasoning(BaseModel):
    """Per-agent reasoning blob attached to a decision."""

    agent: str
    model: str
    summary: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class AgentDecision(BaseModel):
    """Output of the 7-agent pipeline for a single ticker."""

    ticker: str
    market: Market
    quote_currency: Currency
    rating: Rating
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float | None = None
    suggested_size_pct: float = Field(ge=0.0, le=1.0)
    reasoning: list[AgentReasoning]
    debate_transcript: dict[str, str] = Field(default_factory=dict)
    timestamp_utc: datetime
    decision_id: str


class TradeOrder(BaseModel):
    """Risk-approved order ready for executor."""

    order_id: str
    decision_id: str
    ticker: str
    market: Market
    side: Side
    quantity: int = Field(gt=0)
    order_type: OrderType
    limit_price: float | None = None
    stop_loss: float = Field(gt=0)
    risk_approved: bool
    rejection_reasons: list[str] = Field(default_factory=list)
    submitted_at_utc: datetime


class OrderUpdate(BaseModel):
    """Lifecycle event for a placed order."""

    order_id: str
    status: OrderStatus
    filled_qty: int = 0
    avg_fill_price: float | None = None
    slippage_bps: float | None = None
    error_message: str | None = None
    timestamp_utc: datetime


class Position(BaseModel):
    """Open position snapshot."""

    ticker: str
    market: Market
    quantity: int
    avg_entry_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    stop_loss: float
    sector: str | None = None
    opened_at_utc: datetime


class PortfolioSnapshot(BaseModel):
    """Equity + positions for a user, point in time."""

    user_id: str
    cash_usd: float
    cash_try: float
    positions: list[Position]
    total_equity_usd: float
    daily_pnl_usd: float
    daily_pnl_pct: float
    max_drawdown_today: float
    timestamp_utc: datetime
