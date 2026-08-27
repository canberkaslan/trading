#!/usr/bin/env python3
"""Push an alert when the agent stops reaching the broker — daily_run.sh tail.

Reads the same order rows as /v1/diagnostics/actionability, applies the alert
policy in `tradingagents_us.notifications.inert_alert`, and pushes at most one
message per run day. Read-only and off the decision path: it never touches the
broker, never re-derives a decision, and never changes what gets traded.

Best-effort by design — exits 0 on every failure so a broken alerter can never
fail the daily run it is appended to (the run's own exit code is the signal
that matters, and masking it with an alerting error would be a strict downgrade).

    python -m scripts.inert_alert            # send if warranted
    python -m scripts.inert_alert --dry-run  # print the decision, send nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Make package + vendor importable when running as a script
_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

# Same threshold /v1/diagnostics/actionability reports, so the push and the
# app's badge never disagree about what counts as inert.
from tradingagents_us.execution.actionability import (  # noqa: E402
    INERT_THRESHOLD_RUN_DAYS,
    OrderRecord,
    build_report,
)
from tradingagents_us.notifications.inert_alert import AlertState, decide  # noqa: E402


def state_path() -> Path:
    """Anchor to the agent root, never the CWD.

    daily_run.sh cds into agent/ but a systemd unit or a hand-run from anywhere
    else must read the same file — a CWD-relative default would silently start
    a fresh alert history and re-page about a freeze already reported.
    """
    env = os.environ.get("INERT_ALERT_STATE_PATH")
    return Path(env) if env else _AGENT_ROOT / "inert_alert.state.json"


def load_state(path: Path) -> AlertState:
    try:
        return AlertState.from_dict(json.loads(path.read_text()))
    except FileNotFoundError:
        return AlertState()
    except Exception as exc:  # corrupt file -> one extra push, never silence
        print(f"inert_alert: unreadable state ({exc}) — treating as never-alerted", file=sys.stderr)
        return AlertState()


def save_state(path: Path, state: AlertState) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.as_dict(), indent=2))
    tmp.replace(path)  # atomic: a half-written state reads as never-alerted


def _report(days: int):
    from sqlalchemy import create_engine

    from tradingagents_us.storage import TradeLogRepository

    url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
    repo = TradeLogRepository(engine=create_engine(url, future=True))
    since = datetime.now(UTC) - timedelta(days=days)
    rows = repo.list_orders_since(since=since)
    return build_report(
        [
            OrderRecord(
                ticker=r.ticker,
                side=r.side,
                submitted_at_utc=r.submitted_at_utc,
                risk_approved=r.risk_approved,
                rejection_reasons=_as_reasons(r.rejection_reasons_json),
                broker_order_id=r.broker_order_id,
            )
            for r in rows
        ]
    )


def _as_reasons(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None]
    if isinstance(raw, str) and raw.strip():
        return [raw]
    return []


def _send(title: str, body: str, kind: str) -> tuple[bool, str]:
    """Push to every registered device. Returns (delivered, detail).

    `delivered` gates the state write, so every not-actually-sent case — no
    devices, PUSH_DISABLED, an Expo error that `send_expo_push` swallows into
    its return value — has to report False. Recording an undelivered alert as
    reported is the one bug that turns this script into silence.
    """
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
        return False, "no registered devices"

    resp = send_expo_push(
        [
            PushMessage(
                to=t,
                title=title,
                body=body[:200],
                data={"type": "ops_alert", "kind": kind},
            )
            for t in tokens
        ]
    )
    if resp.get("disabled"):
        return False, "PUSH_DISABLED=1"
    if resp.get("error"):
        return False, f"expo error: {resp['error']}"
    return True, f"sent to {len(tokens)} device(s)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Alert when order flow goes inert")
    ap.add_argument("--days", type=int, default=30, help="lookback window in calendar days")
    ap.add_argument("--dry-run", action="store_true", help="print the decision, send nothing")
    args = ap.parse_args(argv)

    try:
        report = _report(args.days)
        path = state_path()
        state = load_state(path)
        alert = decide(report, state, inert_threshold=INERT_THRESHOLD_RUN_DAYS)

        verdict = report.verdict(inert_threshold=INERT_THRESHOLD_RUN_DAYS)
        print(
            f"inert_alert: verdict={verdict} inert_run_days={report.inert_run_days} "
            f"submitted={report.submitted}/{report.orders} -> "
            f"{alert.kind if alert else 'no alert'}"
        )
        if alert is None:
            return 0
        print(f"  {alert.title}\n  {alert.body}")
        if args.dry_run:
            return 0

        delivered, detail = _send(alert.title, alert.body, alert.kind)
        print(f"inert_alert: {detail}")
        # Persist only after a real delivery: a state write on a failed push
        # would mark the freeze "already reported" and swallow tomorrow's too.
        if delivered:
            save_state(path, alert.next_state)
    except Exception as exc:  # alerting must never crash the caller
        print(f"inert_alert failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
