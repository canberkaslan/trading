"""/v1/trades — realized round trips + win rate / expectancy.

Reads the `closed_trades` ledger that `scripts/reconcile.py` derives from the
broker's fill feed. Read-only: no broker call, no decision path. The endpoint
does NOT reconcile on request — a money screen that silently triggers a full
fill replay would couple page loads to Alpaca's availability and latency, and
an unreachable broker would render as "no trades" instead of stale-but-true
numbers. Freshness is surfaced via `reconciled_at_utc` instead.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from tradingagents_us.execution.reconcile import ClosedTrade, compute_stats
from tradingagents_us.storage import TradeLogRepository

from ..deps import get_repo, require_token

router = APIRouter()


class ClosedTradeItem(BaseModel):
    trade_id: str
    ticker: str
    direction: str
    quantity: float
    entry_price: float
    exit_price: float
    realized_pnl: float
    realized_pnl_pct: float
    holding_days: float
    opened_at_utc: datetime
    closed_at_utc: datetime


class TradeStatsItem(BaseModel):
    trades: int
    wins: int
    losses: int
    scratches: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    avg_win: float
    avg_loss: float
    # None (not 0, not "infinity") while nothing has lost yet — an undefined
    # ratio on a small sample must not render as proof of an edge.
    profit_factor: float | None
    expectancy: float
    avg_holding_days: float
    best_trade: float
    worst_trade: float


class TradesResponse(BaseModel):
    trades: list[ClosedTradeItem]
    stats: TradeStatsItem
    # When the ledger last ran. None = reconcile has never run, which the app
    # must show as "not yet reconciled" rather than as a flat, honest zero.
    reconciled_at_utc: datetime | None


@router.get("", response_model=TradesResponse)
async def list_trades(
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
    limit: int = Query(200, ge=1, le=1000),
    ticker: str | None = Query(None, description="filter to one symbol"),
) -> TradesResponse:
    """Closed round trips, newest first, with stats over the returned set.

    Stats are computed over exactly the rows returned, so a `ticker` filter or
    a small `limit` yields that slice's win rate — never the whole-account
    figure attached to a filtered list, which is how per-name stats end up
    quietly reporting the portfolio's.
    """
    rows = repo.list_closed_trades(limit=limit, ticker=ticker)

    trades = [
        ClosedTrade(
            trade_id=r.trade_id,
            symbol=r.symbol,
            direction=r.direction,
            quantity=r.quantity,
            entry_price=r.entry_price,
            exit_price=r.exit_price,
            opened_at_utc=r.opened_at_utc,
            closed_at_utc=r.closed_at_utc,
            realized_pnl=r.realized_pnl,
            realized_pnl_pct=r.realized_pnl_pct,
            holding_days=r.holding_days,
            open_activity_id=r.open_activity_id,
            close_activity_id=r.close_activity_id,
        )
        for r in rows
    ]
    stats = compute_stats(trades)

    return TradesResponse(
        trades=[
            ClosedTradeItem(
                trade_id=r.trade_id,
                ticker=r.symbol,
                direction=r.direction,
                quantity=r.quantity,
                entry_price=r.entry_price,
                exit_price=r.exit_price,
                realized_pnl=r.realized_pnl,
                realized_pnl_pct=r.realized_pnl_pct,
                holding_days=r.holding_days,
                opened_at_utc=r.opened_at_utc,
                closed_at_utc=r.closed_at_utc,
            )
            for r in rows
        ],
        stats=TradeStatsItem(**vars(stats)),
        reconciled_at_utc=max((r.reconciled_at_utc for r in rows), default=None),
    )
