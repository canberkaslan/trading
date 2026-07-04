#!/usr/bin/env python3
"""Ops push notification — CLI for daily_run.sh / systemd OnFailure / cron.

Sends a push to every registered device. Best-effort by design: exits 0
even when the push fails so an alerting failure never masks (or replaces)
the original failure's exit code in a shell `||` chain.

    python -m scripts.notify_ops --title "Daily run FAILED" --body "3 tickers errored"
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="Push an ops alert to registered devices")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    args = ap.parse_args()

    try:
        from sqlalchemy import create_engine

        from tradingagents_us.notifications import send_expo_push
        from tradingagents_us.notifications.sender import PushMessage
        from tradingagents_us.storage import TradeLogRepository
        from tradingagents_us.storage.device_tokens import list_all_tokens

        url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
        repo = TradeLogRepository(engine=create_engine(url, future=True))
        with repo.session() as s:
            tokens = list_all_tokens(s)
        if not tokens:
            print("notify_ops: no registered devices", file=sys.stderr)
            return 0
        send_expo_push([
            PushMessage(
                to=t,
                title=args.title,
                body=args.body[:200],
                data={"type": "ops_alert"},
            )
            for t in tokens
        ])
        print(f"notify_ops: sent to {len(tokens)} device(s)")
    except Exception as exc:  # alerting must never crash the caller
        print(f"notify_ops failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
