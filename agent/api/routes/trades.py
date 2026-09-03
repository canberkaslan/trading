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

`by_exit` / `strategy` split the same rows by what closed each position, using
the class stored at reconcile time (see `execution.exit_quality`). The blended
`stats` expectancy is not the agent's exit record: on this account most of the
realized ledger is the 2026-06-24 flatten that cleaned up the accumulation bug.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from tradingagents_us.eval_window import eval_start_utc
from tradingagents_us.execution.exit_quality import (
    ExitBucket,
    attribute_stored,
    bucket_by_exit,
    strategy_bucket,
)
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
    # What closed the position, from the broker's order record at reconcile
    # time. None = never attributed (pre-dates attribution, or that run could
    # not read order history) — NOT the same as "unknown", which means the
    # order was looked for and is gone. The app must not render them alike.
    exit_class: str | None = None


class ExitBucketItem(BaseModel):
    """One exit path's record. `avg_pnl` is that path's expectancy per trade."""

    exit_class: str
    label: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    net_pnl: float
    gross_profit: float
    gross_loss: float
    avg_pnl: float
    avg_holding_days: float


def _bucket_item(bucket: ExitBucket) -> ExitBucketItem:
    return ExitBucketItem(
        exit_class=bucket.exit_class,
        label=bucket.label,
        trades=bucket.trades,
        wins=bucket.wins,
        losses=bucket.losses,
        win_rate=round(bucket.win_rate, 4),
        net_pnl=round(bucket.net_pnl, 2),
        gross_profit=round(bucket.gross_profit, 2),
        gross_loss=round(bucket.gross_loss, 2),
        avg_pnl=round(bucket.avg_pnl, 2),
        avg_holding_days=round(bucket.avg_holding_days, 2),
    )


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

    # The same rows split by what actually closed each position. `stats` above
    # blends every exit path into one expectancy, and on this account that
    # number is dominated by the 2026-06-24 flatten of the accumulation bug —
    # an operator cleanup, not the strategy's exit discipline. Anything that
    # reads as "how well does the agent exit?" must come from `strategy`, with
    # its trade count attached: it is a narrower claim on a smaller sample.
    by_exit: list[ExitBucketItem] = []
    strategy: ExitBucketItem | None = None
    # Rows the split leaves out because they carry no stored class. Reported so
    # a partially-attributed ledger cannot render as a complete one; a non-zero
    # count here means `by_exit` does not add up to `stats`.
    unattributed: int = 0


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
    # Attribution is read from the ledger, never recomputed: classifying needs
    # the broker's order history, and this endpoint makes no broker call by
    # design. Buckets therefore cover exactly the rows `scripts/reconcile.py`
    # could attribute when it last ran.
    attributed, unattributed = attribute_stored(
        trades, {r.trade_id: r.exit_class for r in rows}
    )
    strategy = strategy_bucket(attributed) if attributed else None

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
                exit_class=r.exit_class,
            )
            for r in rows
        ],
        stats=TradeStatsItem(**vars(stats)),
        by_exit=[_bucket_item(b) for b in bucket_by_exit(attributed)],
        strategy=_bucket_item(strategy) if strategy is not None else None,
        unattributed=unattributed,
        reconciled_at_utc=max((r.reconciled_at_utc for r in rows), default=None),
        window="eval" if scoped else "all_time",
        eval_start_utc=cutoff,
        # Reported even on window=all: the count is a property of the ledger,
        # not of this request, and the app uses it to explain the gap between
        # the two views.
        excluded_pre_eval=excluded,
    )
