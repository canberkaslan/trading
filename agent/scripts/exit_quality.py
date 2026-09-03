#!/usr/bin/env python3
"""Split the realized ledger by what actually closed each position.

    python scripts/exit_quality.py            # table
    python scripts/exit_quality.py --json     # machine-readable
    python scripts/exit_quality.py --trades   # add the per-trade rows

Read-only against the broker and it touches no database: it submits nothing,
cancels nothing, persists nothing, and runs anywhere the Alpaca keys are — which
is the point, because the box has been dark since 2026-08-24 and the question it
answers is the open one behind the honest NO-GO.

`reconcile.py` reports the ledger as a single expectancy. That number blends the
agent's own exits with the 2026-06-24 flatten that cleaned up the accumulation
bug, and the two say completely different things about the system. This walks
each closing fill back to the broker order behind it and reports each path
separately. See `tradingagents_us/execution/exit_quality.py` for how a class is
decided (provenance from the order record, never inferred from the P&L).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from scripts.reconcile import ORDER_PAGE, to_fills  # noqa: E402
from tradingagents_us.dataflows.alpaca_broker import AlpacaClient  # noqa: E402
from tradingagents_us.execution.exit_quality import (  # noqa: E402
    AttributedTrade,
    ExitBucket,
    attribute,
    bucket_by_exit,
    exits_by_fill,
    index_orders,
    strategy_bucket,
)
from tradingagents_us.execution.reconcile import reconcile_fills  # noqa: E402

# `index_orders` / `exits_by_fill` moved next to the classifier they feed so
# `reconcile.py` can attribute exits at write time without importing this CLI.
# Re-exported here because they are still part of this script's surface.
__all__ = ["exits_by_fill", "index_orders", "main"]


def bucket_payload(bucket: ExitBucket) -> dict:
    return {
        "exit_class": bucket.exit_class,
        "label": bucket.label,
        "trades": bucket.trades,
        "wins": bucket.wins,
        "losses": bucket.losses,
        "win_rate": round(bucket.win_rate, 4),
        "net_pnl": round(bucket.net_pnl, 2),
        "gross_profit": round(bucket.gross_profit, 2),
        "gross_loss": round(bucket.gross_loss, 2),
        "avg_pnl": round(bucket.avg_pnl, 2),
        "avg_holding_days": round(bucket.avg_holding_days, 2),
    }


def trade_payload(row: AttributedTrade) -> dict:
    t = row.trade
    return {
        "trade_id": t.trade_id,
        "symbol": t.symbol,
        "exit_class": row.exit_class,
        "quantity": t.quantity,
        "entry_price": round(t.entry_price, 2),
        "exit_price": round(t.exit_price, 2),
        "realized_pnl": round(t.realized_pnl, 2),
        "realized_pnl_pct": round(t.realized_pnl_pct, 4),
        "holding_days": round(t.holding_days, 2),
        "closed_at_utc": t.closed_at_utc.isoformat(),
        "client_order_id": row.order.client_order_id if row.order else None,
    }


def format_report(payload: dict) -> str:
    lines = [
        f"exit quality: {payload['closed_trades']} closed trades "
        f"from {payload['fills_read']} fills "
        f"({payload['orders_read']} orders retained at the broker)",
        "",
        f"  {'exit path':<30}{'n':>4}{'win%':>7}{'net P&L':>12}{'per trade':>11}{'hold':>7}",
        f"  {'-' * 71}",
    ]
    for b in payload["by_exit"]:
        lines.append(
            f"  {b['label']:<30}{b['trades']:>4}{b['win_rate'] * 100:>6.0f}%"
            f"{b['net_pnl']:>12,.2f}{b['avg_pnl']:>11,.2f}{b['avg_holding_days']:>6.1f}d"
        )
    s = payload["strategy_only"]
    lines += [
        f"  {'-' * 71}",
        f"  {s['label']:<30}{s['trades']:>4}{s['win_rate'] * 100:>6.0f}%"
        f"{s['net_pnl']:>12,.2f}{s['avg_pnl']:>11,.2f}{s['avg_holding_days']:>6.1f}d",
        "",
        "  'strategy exits only' drops flattens and unattributable exits — it is the",
        "  agent's own exit record, on a smaller sample. Quote it with n attached.",
    ]
    if payload.get("trades"):
        lines += ["", f"  {'symbol':<7}{'exit path':<16}{'qty':>6}{'entry':>10}"
                      f"{'exit':>10}{'P&L':>10}{'%':>8}{'hold':>7}  closed"]
        for t in payload["trades"]:
            lines.append(
                f"  {t['symbol']:<7}{t['exit_class']:<16}{t['quantity']:>6g}"
                f"{t['entry_price']:>10,.2f}{t['exit_price']:>10,.2f}"
                f"{t['realized_pnl']:>10,.2f}{t['realized_pnl_pct'] * 100:>7.2f}%"
                f"{t['holding_days']:>6.1f}d  {t['closed_at_utc'][:10]}"
            )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Realized P&L split by exit path")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--trades", action="store_true", help="include per-trade rows")
    args = ap.parse_args()

    with AlpacaClient() as ac:
        fills = ac.list_fill_activities()
        orders = ac.list_orders(status="all", limit=ORDER_PAGE, nested=True)

    closed = reconcile_fills(to_fills(fills)).closed
    attributed = attribute(closed, exits_by_fill(fills, index_orders(orders)))

    payload = {
        "fills_read": len(fills),
        "orders_read": len(orders),
        "closed_trades": len(closed),
        "by_exit": [bucket_payload(b) for b in bucket_by_exit(attributed)],
        "strategy_only": bucket_payload(strategy_bucket(attributed)),
    }
    if args.trades:
        payload["trades"] = [
            trade_payload(r)
            for r in sorted(attributed, key=lambda r: r.trade.closed_at_utc)
        ]

    print(json.dumps(payload, indent=2) if args.json else format_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
