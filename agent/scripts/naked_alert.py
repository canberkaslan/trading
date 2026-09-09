#!/usr/bin/env python3
"""Push an alert when the book loses its protective stops — daily_run.sh tail.

`risk.stop_coverage` could compute this from the day it was written and nothing
ever called it. The first run of the position pass found 75.5% of held shares
naked, on an account whose go-live checklist assumes every entry ships a
bracket. Nobody was told, because nothing was asking.

Read-only against the broker: it submits nothing and touches no decision path.
Best-effort like `inert_alert` — exits 0 on every failure, because a broken
alerter must never fail the daily run it is appended to.

    python -m scripts.naked_alert            # send if warranted
    python -m scripts.naked_alert --dry-run  # print the decision, send nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient  # noqa: E402
from tradingagents_us.notifications.naked_alert import (  # noqa: E402
    CoverageFacts,
    NakedAlertState,
    decide,
)
from tradingagents_us.risk.kill_switch import (  # noqa: E402
    FileKillSwitchReader,
    default_kill_switch_path,
)
from tradingagents_us.risk.stop_coverage import (  # noqa: E402
    OrderView,
    PositionView,
    coverage,
    flatten_orders,
)


def state_path() -> Path:
    """Anchor to the agent root, never the CWD — same rule as inert_alert.

    daily_run.sh cds into agent/, but a systemd unit or a hand-run from anywhere
    else must read the same file. A CWD-relative default would start a fresh
    alert history and re-page about exposure already reported.
    """
    env = os.environ.get("NAKED_ALERT_STATE_PATH")
    return Path(env) if env else _AGENT_ROOT / "naked_alert.state.json"


def load_state(path: Path) -> NakedAlertState:
    try:
        return NakedAlertState.from_dict(json.loads(path.read_text()))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        # A missing or corrupt state file must not silence the alerter. Starting
        # from empty means at worst one duplicate page; refusing to run means
        # the exposure goes unreported, which is the failure that matters.
        return NakedAlertState()


def collect_facts() -> CoverageFacts:
    """Ask the broker what is actually protected right now.

    `status="all"` and `nested=True`: the resting leg of a bracket sits in
    `held`, which Alpaca's "open" filter excludes, and it is returned under its
    parent. Asking the obvious way reports a fully bracketed book as having zero
    stops — which would page every single day about nothing.
    """
    with AlpacaClient() as client:
        positions = client.list_positions()
        orders = flatten_orders(client.list_orders(status="all", limit=500, nested=True))

    views = [
        OrderView(
            symbol=o.symbol,
            side=o.side.lower(),
            order_type=o.order_type.lower(),
            status=o.status.lower(),
            remaining_qty=max(0.0, o.qty - o.filled_qty),
            stop_price=o.stop_price,
        )
        for o in orders
    ]
    report = coverage([PositionView(p.symbol, p.qty, "long") for p in positions], views)

    return CoverageFacts(
        total_qty=report.total_qty,
        naked_qty=report.naked_qty,
        indeterminate_qty=report.indeterminate_qty,
        naked_symbols=tuple(s.symbol for s in report.symbols if s.naked_qty > 0),
        run_date=datetime.now(UTC).date().isoformat(),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print the decision, send nothing")
    args = ap.parse_args()

    try:
        facts = collect_facts()
        ks = FileKillSwitchReader(
            os.environ.get("KILL_SWITCH_FILE", default_kill_switch_path())
        ).read()
        path = state_path()
        alert = decide(facts, load_state(path), kill_switch=ks)

        print(
            f"stop coverage: {facts.naked_qty:.0f}/{facts.total_qty:.0f} naked "
            f"({facts.naked_pct:.1f}%), indeterminate {facts.indeterminate_qty:.0f}, ks={ks}"
        )
        if alert is None:
            print("no alert warranted")
            return 0

        print(f"ALERT [{alert.kind}] {alert.title} — {alert.body}")
        if args.dry_run:
            return 0

        subprocess.run(
            [
                sys.executable, "-m", "scripts.notify_ops",
                "--title", alert.title, "--body", alert.body,
            ],
            cwd=str(_AGENT_ROOT),
            check=False,
        )
        # State is written only after the push is attempted. Writing it first
        # would mean a crashed push permanently suppresses the alert it never
        # sent — the exposure would then be reported exactly zero times.
        path.write_text(json.dumps(alert.next_state.as_dict(), indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 — an alerter must never fail the run
        print(f"naked_alert failed (non-fatal): {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
