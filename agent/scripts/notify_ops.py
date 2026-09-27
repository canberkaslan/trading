#!/usr/bin/env python3
"""Ops alert CLI for daily_run.sh, systemd OnFailure= and cron.

Delivers through `tradingagents_us.notifications.ops_channel`: a push to every
registered device AND a GitHub issue, so an alert still reaches a human when the
app is uninstalled, being rebuilt, or has never registered a device. It used to
push only, and printed "no registered devices" and returned when there were
none, which is how every box alert could be sent and reach nobody.

Best-effort by design: exits 0 even when every channel fails, so an alerting
failure never masks (or replaces) the original failure's exit code in a shell
`||` chain.

    python -m scripts.notify_ops --kind daily_run --title "Daily run FAILED" --body "3 tickers"
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send an ops alert (push + GitHub issue)")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument(
        "--kind",
        default="ops",
        help="groups repeats: every alert of one kind lands on the same open issue",
    )
    args = ap.parse_args(argv)

    try:
        from tradingagents_us.notifications.ops_channel import send_ops_alert

        delivery = send_ops_alert(args.title, args.body, kind=args.kind)
        print(f"notify_ops: {delivery.describe()}")
        if not delivery.delivered:
            print("notify_ops: this alert reached NO channel", file=sys.stderr)
    except Exception as exc:  # alerting must never crash the caller
        print(f"notify_ops failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
