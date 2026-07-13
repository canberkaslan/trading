#!/usr/bin/env python3
"""Pre-run kill-switch check — the enforcement half of the mobile switch.

daily_run.sh calls this BEFORE burning any LLM tokens. Exit codes:

    0   RUN          — proceed with the daily run
    75  PAUSE_NEW    — skip the run (no new entries; broker-side GTC stops
                       keep managing existing positions)
    76  FLATTEN_ALL  — all positions closed + open orders cancelled here;
                       skip the run
    1   unexpected error — caller should fail safe (skip the run + alert)

FLATTEN_ALL is executed exactly as the mobile app promises: cancel all open
orders, then liquidate every position at market via Alpaca's
close_all_positions. The state file is left as-is — flipping back to RUN is
a deliberate human action in the app, and re-running flatten on an already
flat book is a no-op.

Every action is recorded in the kill_switch_events audit table and pushed
to registered devices (best-effort).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make package + vendor importable when running as a script
_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from tradingagents_us.risk.kill_switch import FileKillSwitchReader  # noqa: E402

EXIT_RUN = 0
EXIT_PAUSE = 75
EXIT_FLATTENED = 76


def _repo():
    from sqlalchemy import create_engine

    from tradingagents_us.storage import TradeLogRepository

    url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
    return TradeLogRepository(engine=create_engine(url, future=True))


def _audit(state: str, detail: str) -> None:
    try:
        _repo().append_kill_event(state=state, actor="system", source="daily_run", detail=detail)
    except Exception as exc:  # audit must never block enforcement
        print(f"kill_check: audit write failed: {exc}", file=sys.stderr)


def _notify(title: str, body: str) -> None:
    try:
        from tradingagents_us.notifications import send_expo_push
        from tradingagents_us.notifications.sender import PushMessage
        from tradingagents_us.storage.device_tokens import list_all_tokens

        with _repo().session() as s:
            tokens = list_all_tokens(s)
        send_expo_push([
            PushMessage(to=t, title=title, body=body[:200], data={"type": "ops_alert", "kind": "kill_switch"})
            for t in tokens
        ])
    except Exception as exc:
        print(f"kill_check: notify failed: {exc}", file=sys.stderr)


def _flatten() -> str:
    """Cancel open orders, liquidate all positions. Returns a summary string."""
    from tradingagents_us.dataflows.alpaca_broker import AlpacaClient

    with AlpacaClient() as cli:
        positions = cli.list_positions()
        if not positions:
            cli.cancel_all_orders()
            return "book already flat; open orders cancelled"
        tickers = ", ".join(f"{p.symbol}:{p.qty:g}" for p in positions)
        results = cli.close_all_positions(cancel_orders=True)
        return f"closed {len(positions)} position(s) [{tickers}]; broker responses: {len(results)}"


def main() -> int:
    state = FileKillSwitchReader().read()
    print(f"kill_check: state={state}")

    if state == "RUN":
        return EXIT_RUN

    if state == "PAUSE_NEW":
        _audit("PAUSE_NEW", "daily run skipped (no new entries)")
        return EXIT_PAUSE

    # FLATTEN_ALL
    try:
        summary = _flatten()
    except Exception as exc:
        detail = f"flatten FAILED: {exc}"
        print(f"kill_check: {detail}", file=sys.stderr)
        _audit("FLATTEN_ALL", detail)
        _notify("🛑 FLATTEN_ALL failed", detail)
        return 1

    print(f"kill_check: FLATTEN_ALL executed — {summary}")
    _audit("FLATTEN_ALL", f"executed: {summary}")
    _notify("🛑 FLATTEN_ALL executed", summary)
    return EXIT_FLATTENED


if __name__ == "__main__":
    raise SystemExit(main())
