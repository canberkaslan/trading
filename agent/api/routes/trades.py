"""/v1/trades — realized round trips + win rate / expectancy.

Reads the `closed_trades` ledger that `scripts/reconcile.py` derives from the
broker's fill feed. Read-only: no broker call, no decision path. The endpoint
does NOT reconcile on request — a money screen that silently triggers a full
fill replay would couple page loads to Alpaca's availability and latency, and
an unreachable broker would render as "no trades" instead of stale-but-true
numbers. Freshness is surfaced via `reconciled_at_utc` instead.

Scoped to the eval window by default (`EVAL_START_DATE`, see
`tradingagents_us.eval_window`), because /v1/eval has always been: reporting a
win rate over bug-era trades next to a Sharpe that excludes them made the two
numbers describe different books. `window=all` returns the full history, and
`excluded_pre_eval` always reports how many rows the cutoff hides.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from tradingagents_us.eval_window import eval_start_utc
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
    # "eval" = only round trips ENTERED after the eval cutoff, matching what
    # /v1/eval measures. "all_time" = everything (either window=all, or no
    # EVAL_START_DATE is configured). The app labels the card from this — a
    # filtered ledger must never render as the whole record.
    window: Literal["eval", "all_time"] = "all_time"
    eval_start_utc: datetime | None = None
    excluded_pre_eval: int = 0


@router.get("", response_model=TradesResponse)
async def list_trades(
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
    limit: int = Query(200, ge=1, le=1000),
    ticker: str | None = Query(None, description="filter to one symbol"),
    window: Literal["eval", "all"] = Query(
        "eval", description="'eval' = entries after EVAL_START_DATE; 'all' = full history"
    ),
) -> TradesResponse:
    """Closed round trips, newest first, with stats over the returned set.

    Stats are computed over exactly the rows returned, so a `ticker` filter or
    a small `limit` yields that slice's win rate — never the whole-account
    figure attached to a filtered list, which is how per-name stats end up
    quietly reporting the portfolio's.
    """
    cutoff = eval_start_utc()
    scoped = window == "eval" and cutoff is not None
    rows = repo.list_closed_trades(
        limit=limit, ticker=ticker, opened_since=cutoff if scoped else None
    )
    excluded = (
        repo.count_closed_trades_opened_before(cutoff, ticker=ticker)
        if cutoff is not None
        else 0
    )

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
        window="eval" if scoped else "all_time",
        eval_start_utc=cutoff,
        # Reported even on window=all: the count is a property of the ledger,
        # not of this request, and the app uses it to explain the gap between
        # the two views.
        excluded_pre_eval=excluded,
    )
