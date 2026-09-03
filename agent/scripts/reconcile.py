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
from datetime import UTC, datetime

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient, FillActivity
from tradingagents_us.execution.exit_quality import (
    attribute,
    exits_by_fill,
    index_orders,
)
from tradingagents_us.execution.reconcile import (
    ClosedTrade,
    Fill,
    TradeStats,
    compute_stats,
    reconcile_fills,
)
from tradingagents_us.storage import TradeLogRepository

#: Alpaca prunes order history; the activities feed does not. Ask for far more
#: orders than the account has ever placed so an exit that comes back
#: unattributed means "the broker no longer has it", not "we did not ask".
ORDER_PAGE = 500


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


def attribute_exits(
    closed: list[ClosedTrade], fills: list[FillActivity], orders: list
) -> dict[str, str]:
    """trade_id → exit class, decided against the broker's own order record.

    Done here, at reconcile time, rather than on read: classification needs
    order history, and `/v1/trades` deliberately makes no broker call — a money
    screen whose page load depends on Alpaca renders an outage as "no trades".
    Storing the verdict moves that dependency to a job that already has it.

    Trades whose closing order is gone are simply absent from the result. The
    caller must not turn that absence into a stored `"unknown"`: the row keeps
    whatever it had, and a never-attributed row stays NULL.
    """
    resolved = exits_by_fill(fills, index_orders(orders))
    return {
        row.trade.trade_id: row.exit_class
        for row in attribute(closed, resolved)
        if row.order is not None
    }


def summary_payload(
    trades: list[ClosedTrade], stats: TradeStats, n_fills: int, new_rows: int
) -> dict:
    return {
        "reconciled_at_utc": datetime.now(UTC).isoformat(),
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
        + (
            f"\n  exits     {payload['attributed_exits']} attributed, "
            f"{payload['unattributed_exits']} not (order pruned or unreadable)"
            if "attributed_exits" in payload
            else ""
        )
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile fills into realized P&L")
    ap.add_argument("--dry-run", action="store_true", help="compute but do not persist")
    ap.add_argument("--json", action="store_true", help="emit JSON summary")
    args = ap.parse_args()

    with AlpacaClient() as ac:
        activities = ac.list_fill_activities()
        # Nested, so bracket children come back: a protective stop is never a
        # top-level order, and a flat listing attributes none of them.
        # Failing to read orders must not fail the reconcile — the ledger
        # itself comes from fills, and attribution is an enrichment on top.
        try:
            orders = ac.list_orders(status="all", limit=ORDER_PAGE, nested=True)
        except Exception as exc:  # noqa: BLE001 - degrade, never lose the ledger
            print(f"warning: order history unreadable, exits left unattributed: {exc}")
            orders = []

    result = reconcile_fills(to_fills(activities))
    stats = compute_stats(result.closed)
    exit_classes = attribute_exits(result.closed, activities, orders)

    new_rows = 0
    if not args.dry_run:
        new_rows = TradeLogRepository().upsert_closed_trades(
            result.closed, exit_classes=exit_classes
        )

    payload = summary_payload(result.closed, stats, len(activities), new_rows)
    payload["attributed_exits"] = len(exit_classes)
    payload["unattributed_exits"] = len(result.closed) - len(exit_classes)
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
