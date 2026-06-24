#!/usr/bin/env python3
"""Append a daily portfolio snapshot to a JSONL log for eval enrichment.

Alpaca's portfolio_history gives the equity curve, but not the per-position
detail or how concentrated the book was each day. This logs one JSON line per
run — equity, cash, and every position — so `eval_report.py` can report
concentration / position-count trends alongside Sharpe/DD.

    python scripts/snapshot.py
    EVAL_SNAPSHOT_FILE=/opt/ai-trader/data/eval_snapshots.jsonl python scripts/snapshot.py

Read-only against the broker; only writes the local JSONL file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tradingagents_us.dataflows.alpaca_broker import Account, AlpacaClient, Position

DEFAULT_FILE = "./data/eval_snapshots.jsonl"


def build_snapshot(account: Account, positions: list[Position], now_iso: str) -> dict[str, Any]:
    """Pure: assemble the snapshot record from broker objects."""
    equity = account.portfolio_value
    pos = [
        {
            "ticker": p.symbol,
            "qty": p.qty,
            "avg_entry": p.avg_entry_price,
            "market_value": p.market_value,
            "unrealized_pl": p.unrealized_pl,
            "unrealized_plpc": p.unrealized_plpc,
            "weight_pct": (abs(p.market_value) / equity * 100.0) if equity else 0.0,
        }
        for p in positions
    ]
    top_weight = max((p["weight_pct"] for p in pos), default=0.0)
    return {
        "ts": now_iso,
        "equity": equity,
        "cash": account.cash,
        "buying_power": account.buying_power,
        "n_positions": len(pos),
        "top_weight_pct": round(top_weight, 2),
        "positions": pos,
    }


def append_snapshot(record: dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def main() -> int:
    path = os.environ.get("EVAL_SNAPSHOT_FILE", DEFAULT_FILE)
    now_iso = datetime.now(timezone.utc).isoformat()
    with AlpacaClient() as ac:
        record = build_snapshot(ac.account(), ac.list_positions(), now_iso)
    append_snapshot(record, path)
    print(
        f"snapshot {now_iso}: equity=${record['equity']:,.0f} "
        f"positions={record['n_positions']} top={record['top_weight_pct']}% -> {path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
