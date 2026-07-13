#!/usr/bin/env python3
"""Pre-run kill-switch check — the daily-run backstop of the mobile switch.

The IMMEDIATE FLATTEN_ALL execution lives in the API handler (POST
/v1/orders/kill-switch runs the flatten at flip time). This script is the
backstop: daily_run.sh calls it BEFORE burning any LLM tokens, so an armed
switch is always enforced even if the API-side attempt failed. Exit codes:

    0   RUN          — proceed with the daily run
    75  PAUSE_NEW    — skip the run (no new entries)
    76  FLATTEN_ALL  — close orders submitted (or book already flat); skip
    1   flatten failed / partial — caller fail-safes (skip run + alert)

Per-position failures inside Alpaca's 207 multi-status response count as
FAILURE: cancel_orders=true has already cancelled protective stop legs, so
a partially-flattened book reported as flat would hide an unprotected
position. The state file is left as-is; on days the book is already flat
the audit row is written but no push is sent (no alert fatigue).
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


def main() -> int:
    state = FileKillSwitchReader().read()
    print(f"kill_check: state={state}")

    if state == "RUN":
        return EXIT_RUN

    if state == "PAUSE_NEW":
        _audit("PAUSE_NEW", "daily run skipped (no new entries)")
        return EXIT_PAUSE

    # FLATTEN_ALL — backstop execution (API already tried at flip time)
    from tradingagents_us.execution.flatten import flatten_all

    try:
        result = flatten_all()
    except Exception as exc:
        detail = f"flatten FAILED: {exc}"
        print(f"kill_check: {detail}", file=sys.stderr)
        _audit("FLATTEN_ALL", detail)
        _notify("🛑 FLATTEN_ALL failed", detail)
        return 1

    if not result.ok:
        # Partial flatten: failed symbols may be open WITHOUT stop legs.
        print(f"kill_check: {result.summary}", file=sys.stderr)
        _audit("FLATTEN_ALL", f"partial: {result.summary}")
        _notify("🛑 FLATTEN_ALL PARTIAL — positions may be unprotected", result.summary)
        return 1

    print(f"kill_check: FLATTEN_ALL — {result.summary}")
    _audit("FLATTEN_ALL", ("noop: " if result.noop else "executed: ") + result.summary)
    if not result.noop:
        # Push only when something actually got closed — a still-armed
        # switch on an already-flat book must not page every day.
        _notify("🛑 FLATTEN_ALL executed", result.summary)
    return EXIT_FLATTENED


if __name__ == "__main__":
    raise SystemExit(main())
