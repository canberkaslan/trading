#!/usr/bin/env python3
"""Report how much of the book has a protective stop behind it.

Read-only against the broker: it submits nothing, cancels nothing, replaces
nothing, and touches no decision path. The whole point is to produce the number
that a stop backfill would act on, so that the number can be reviewed by a human
before anything places an order.

    python scripts/stop_coverage.py             # table
    python scripts/stop_coverage.py --json      # machine-readable

Runs from anywhere with Alpaca credentials — it does not need the trading box,
which is why it is usable while the box is down and the book is unattended.

Why `status="all"` and `nested=True` rather than the obvious `status="open"`:
the resting leg of a bracket sits in status `held`, which Alpaca's "open" filter
excludes, and it is returned as a child of its parent rather than at the top
level. Asking the obvious way reports a fully bracketed book as having zero
stops. See `risk.stop_coverage` for what that error costs.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.risk.stop_coverage import (
    CoverageReport,
    OrderView,
    PositionView,
    coverage,
    flatten_orders,
)

# Enough history to reach the bracket parents of long-held lots. Protective legs
# of a position opened months ago are only reachable through their parent order,
# and the parent is old.
ORDER_PAGE_LIMIT = 500


def to_views(orders: list) -> list[OrderView]:
    """Broker DTO → accounting input, children included.

    Keeps httpx out of the pure module, same split as `scripts/reconcile.py`.
    """
    return [
        OrderView(
            symbol=o.symbol,
            side=o.side.lower(),
            order_type=o.order_type.lower(),
            status=o.status.lower(),
            remaining_qty=max(0.0, o.qty - o.filled_qty),
            stop_price=o.stop_price,
        )
        for o in flatten_orders(orders)
    ]


def to_positions(positions: list) -> list[PositionView]:
    return [
        PositionView(symbol=p.symbol, qty=abs(p.qty), side=p.side.lower())
        for p in positions
    ]


def payload(report: CoverageReport) -> dict:
    return {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "total_qty": report.total_qty,
        "protected_qty": report.protected_qty,
        "naked_qty": report.naked_qty,
        "indeterminate_qty": report.indeterminate_qty,
        "naked_pct": round(report.naked_pct, 2),
        "has_indeterminate": report.has_indeterminate,
        "orphan_stop_symbols": list(report.orphan_stop_symbols),
        "symbols": [
            {
                "symbol": s.symbol,
                "side": s.position_side,
                "position_qty": s.position_qty,
                "protected_qty": s.protected_qty,
                "naked_qty": s.naked_qty,
                "indeterminate_qty": s.indeterminate_qty,
                "excess_qty": s.excess_qty,
                "stop_prices": list(s.stop_prices),
                "stop_types": list(s.stop_types),
                "actionable": s.is_actionable,
            }
            for s in report.symbols
        ],
    }


def format_report(report: CoverageReport) -> str:
    lines = [
        f"{'SYM':<7}{'SIDE':<6}{'QTY':>7}{'PROT':>7}{'NAKED':>7}{'INDET':>7}"
        f"{'EXCESS':>8}  STOPS",
        "-" * 76,
    ]
    for s in report.symbols:
        stops = (
            ", ".join(f"{p:,.2f}" for p in sorted(s.stop_prices)) if s.stop_prices else "—"
        )
        if s.stop_types:
            stops += f" ({'/'.join(s.stop_types)})"
        lines.append(
            f"{s.symbol:<7}{s.position_side:<6}{s.position_qty:>7,.0f}"
            f"{s.protected_qty:>7,.0f}{s.naked_qty:>7,.0f}{s.indeterminate_qty:>7,.0f}"
            f"{s.excess_qty:>8,.0f}  {stops}"
        )
    lines.append("-" * 76)
    lines.append(
        f"{'TOTAL':<13}{report.total_qty:>7,.0f}{report.protected_qty:>7,.0f}"
        f"{report.naked_qty:>7,.0f}{report.indeterminate_qty:>7,.0f}"
        f"{'':>8}  {report.naked_pct:.1f}% naked"
    )

    # Hazards last, so they are the thing left on screen.
    excess = [s for s in report.symbols if s.excess_qty > 0]
    if excess:
        lines.append("")
        lines.append(
            "WARNING: more protective quantity than shares held on "
            + ", ".join(f"{s.symbol} (+{s.excess_qty:,.0f})" for s in excess)
            + " — a stop for more shares than are held opens a short when it triggers."
        )
    if report.orphan_stop_symbols:
        lines.append("")
        lines.append(
            "WARNING: protective orders with no position under them: "
            + ", ".join(report.orphan_stop_symbols)
        )
    if report.has_indeterminate:
        lines.append("")
        lines.append(
            "NOTE: some orders are in a status that is neither clearly live nor "
            "clearly finished, so their shares are counted as indeterminate rather "
            "than guessed either way. Do not backfill those names until they settle."
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Protective-stop coverage of the book")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    with AlpacaClient() as ac:
        positions = to_positions(ac.list_positions())
        orders = to_views(ac.list_orders(status="all", limit=ORDER_PAGE_LIMIT, nested=True))

    report = coverage(positions, orders)
    print(json.dumps(payload(report), indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
