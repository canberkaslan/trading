#!/usr/bin/env python3
"""Reconcile broker fills into the realized P&L ledger.

Pulls every FILL activity from Alpaca, replays it through the FIFO matcher,
and upserts the resulting round trips into `closed_trades`. Read-only against
the broker — it submits nothing, cancels nothing, and touches no decision path.

    python scripts/reconcile.py                 # replay + persist, print stats
    python scripts/reconcile.py --dry-run       # print only, write nothing
    python scripts/reconcile.py --json          # machine-readable summary

Full replay, not incremental, and deliberately so: FIFO matching needs the
entire position history to know which lot an exit closes, so a "fills since
last run" read would mis-pair every trade whose entry predates the window.
Deterministic trade ids make the replay converge instead of duplicating. At the
current volume (a handful of fills a day) this is a couple of pages of API; the
day it is not, the fix is a persisted lot-inventory checkpoint, not a narrower
fill window.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient, FillActivity
from tradingagents_us.execution.reconcile import (
    ClosedTrade,
    Fill,
    TradeStats,
    compute_stats,
    reconcile_fills,
)
from tradingagents_us.storage import TradeLogRepository


def to_fills(activities: list[FillActivity]) -> list[Fill]:
    """Broker DTO → matcher input. Split out so the matcher never imports httpx."""
    return [
        Fill(
            id=a.id,
            symbol=a.symbol,
            side=a.side,
            qty=a.qty,
            price=a.price,
            transaction_time=a.transaction_time,
        )
        for a in activities
    ]


def summary_payload(
    trades: list[ClosedTrade], stats: TradeStats, n_fills: int, new_rows: int
) -> dict:
    return {
        "reconciled_at_utc": datetime.now(timezone.utc).isoformat(),
        "fills_read": n_fills,
        "closed_trades": stats.trades,
        "new_rows": new_rows,
        "wins": stats.wins,
        "losses": stats.losses,
        "scratches": stats.scratches,
        "win_rate": round(stats.win_rate, 4),
        "net_realized_pnl": round(stats.net_pnl, 2),
        "gross_profit": round(stats.gross_profit, 2),
        "gross_loss": round(stats.gross_loss, 2),
        "avg_win": round(stats.avg_win, 2),
        "avg_loss": round(stats.avg_loss, 2),
        "expectancy": round(stats.expectancy, 2),
        "profit_factor": (
            round(stats.profit_factor, 2) if stats.profit_factor is not None else None
        ),
        "avg_holding_days": round(stats.avg_holding_days, 2),
        "best_trade": round(stats.best_trade, 2),
        "worst_trade": round(stats.worst_trade, 2),
    }


def format_summary(payload: dict) -> str:
    pf = payload["profit_factor"]
    return (
        f"reconcile: {payload['fills_read']} fills -> {payload['closed_trades']} "
        f"closed trades ({payload['new_rows']} new)\n"
        f"  win rate  {payload['win_rate'] * 100:.1f}%  "
        f"({payload['wins']}W / {payload['losses']}L / {payload['scratches']}S)\n"
        f"  net P&L   ${payload['net_realized_pnl']:,.2f}  "
        f"(gross +${payload['gross_profit']:,.2f} / -${payload['gross_loss']:,.2f})\n"
        f"  avg win   ${payload['avg_win']:,.2f}   avg loss ${payload['avg_loss']:,.2f}\n"
        f"  expectancy ${payload['expectancy']:,.2f}/trade  "
        f"profit factor {pf if pf is not None else 'n/a (no losses yet)'}\n"
        f"  avg hold  {payload['avg_holding_days']:.1f}d   "
        f"best ${payload['best_trade']:,.2f}  worst ${payload['worst_trade']:,.2f}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile fills into realized P&L")
    ap.add_argument("--dry-run", action="store_true", help="compute but do not persist")
    ap.add_argument("--json", action="store_true", help="emit JSON summary")
    args = ap.parse_args()

    with AlpacaClient() as ac:
        activities = ac.list_fill_activities()

    result = reconcile_fills(to_fills(activities))
    stats = compute_stats(result.closed)

    new_rows = 0
    if not args.dry_run:
        new_rows = TradeLogRepository().upsert_closed_trades(result.closed)

    payload = summary_payload(result.closed, stats, len(activities), new_rows)
    if args.dry_run:
        payload["dry_run"] = True

    print(json.dumps(payload, indent=2) if args.json else format_summary(payload))
    if not args.json and result.open_lots:
        held = ", ".join(
            f"{lot.symbol} {lot.quantity:g}@${lot.price:,.2f}" for lot in result.open_lots
        )
        print(f"  still open: {held}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
