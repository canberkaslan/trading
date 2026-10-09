#!/usr/bin/env python3
"""Check that every held share has a protective stop — daily_run.sh tail.

`risk.stop_coverage` could compute this from the day it was written and nothing
ever called it. The first run of the position pass found 75.5% of held shares
naked, on an account whose go-live checklist assumes every entry ships a
bracket. Nobody was told, because nothing was asking.

It then ran with `|| true` and always exited 0, so a naked book was a line in a
log nobody reads. The exit code is now the signal, and daily_run.sh turns it
into an ops alert on every run the book is naked:

    0  covered (or holding nothing, or a deliberate FLATTEN_ALL in progress)
    3  shares are held with no protective stop; the `NAKED:` line says which
    1  the check itself could not run, so coverage is unknown

Covered means a live stop, or our own time exit still waiting for the open
(`exiting`, see `execution.exit_cover`). The night a time exit goes out, the
lot has no stop: its stops were released for the exit, which reserves every
share and sells them at the open. Read as naked, that night paged "NAKED" over
GOOGL's 32 shares on 2026-10-05, and the night after, the lot sold, announced
a recovery. Exiting shares are counted and named on the `stop coverage:` line
instead. A sell that is not our exit, an exit for fewer shares than are held,
one an open has met, and a short with no buy stop are still naked, and still
exit 3.

The caller owns the page about a naked book, so this script never sends one
itself; sending it here too would page twice on the same run. It announces
only the all-clear, once, on the first covered run after a naked one, since
nothing else would ever say so. An all-clear no channel took is retried on
later runs, for `RECOVERY_RETRY_DAYS` after the exposure it clears, then
dropped: past that it is old news, and it names the exposure's date either way.

Read-only against the broker: it submits nothing and touches no decision path.

    python -m scripts.naked_alert            # check; announce a recovery
    python -m scripts.naked_alert --dry-run  # check only, send and record nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient  # noqa: E402
from tradingagents_us.execution.exit_cover import order_views, position_views  # noqa: E402
from tradingagents_us.execution.protected_close import opened_after  # noqa: E402
from tradingagents_us.notifications.naked_alert import (  # noqa: E402
    CoverageFacts,
    NakedAlertState,
    days_between,
    decide,
    recovery_expired,
)
from tradingagents_us.notifications.ops_channel import send_ops_alert  # noqa: E402
from tradingagents_us.risk.kill_switch import (  # noqa: E402
    FileKillSwitchReader,
    default_kill_switch_path,
)
from tradingagents_us.risk.stop_coverage import coverage, flatten_orders  # noqa: E402

EXIT_COVERED = 0
EXIT_CHECK_FAILED = 1
#: Distinct from 1 (the check could not run) and 2 (argparse), so the caller can
#: tell "the book is exposed" apart from "we do not know".
EXIT_NAKED = 3

#: Enough names to act on without the line wrapping off a lock screen.
_NAMED_SYMBOLS = 6


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

    Our own time exits are told apart by their stamp and by the broker's
    calendar (`execution.exit_cover`), read only when the book has one; a
    calendar that cannot be read vouches for none of them, and logs why.
    Positions keep their side: a short is protected by a buy stop only.
    """
    with AlpacaClient() as client:
        positions = position_views(client.list_positions())
        orders = flatten_orders(client.list_orders(status="all", limit=500, nested=True))
        views = order_views(orders, opened_after(client))

    report = coverage(positions, views)

    return CoverageFacts(
        total_qty=report.total_qty,
        naked_qty=report.naked_qty,
        indeterminate_qty=report.indeterminate_qty,
        naked_symbols=tuple(s.symbol for s in report.symbols if s.naked_qty > 0),
        run_date=datetime.now(UTC).date().isoformat(),
        exiting_qty=report.exiting_qty,
        exiting_symbols=tuple(s.symbol for s in report.symbols if s.exiting_qty > 0),
    )


def has_naked_exposure(facts: CoverageFacts, kill_switch: str) -> bool:
    """Any held share with neither a stop nor our own working exit, outside a deliberate flatten.

    No threshold. The daily run reaches this check after the close and after
    the position pass has back-filled stops, so there is no in-flight partial
    fill to excuse: a share still naked here is one the back-fill did not cover.
    """
    return facts.total_qty > 0 and facts.naked_qty > 0 and kill_switch != "FLATTEN_ALL"


def naked_summary(facts: CoverageFacts) -> str:
    names = ", ".join(facts.naked_symbols[:_NAMED_SYMBOLS])
    extra = len(facts.naked_symbols) - _NAMED_SYMBOLS
    more = f" +{extra} more" if extra > 0 else ""
    caveat = (
        f"; {facts.indeterminate_qty:.0f} more indeterminate" if facts.indeterminate_qty > 0 else ""
    )
    leaving = (
        f"; {facts.exiting_qty:.0f} exiting under our own time exit"
        if facts.exiting_qty > 0
        else ""
    )
    return (
        f"{facts.naked_qty:.0f} of {facts.total_qty:.0f} shares "
        f"({facts.naked_pct:.1f}%) have no protective stop"
        f"{': ' + names + more if names else ''}{caveat}{leaving}"
    )


def exiting_summary(facts: CoverageFacts) -> str:
    """'exiting 32 (GOOGL)': shares our own time exit sells at the open, by name."""
    names = ", ".join(facts.exiting_symbols[:_NAMED_SYMBOLS])
    extra = len(facts.exiting_symbols) - _NAMED_SYMBOLS
    more = f" +{extra} more" if extra > 0 else ""
    return f"exiting {facts.exiting_qty:.0f}" + (f" ({names}{more})" if names else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="check only; send and record nothing")
    args = ap.parse_args(argv)

    try:
        facts = collect_facts()
        ks = FileKillSwitchReader(
            os.environ.get("KILL_SWITCH_FILE", default_kill_switch_path())
        ).read()
    except Exception as exc:  # noqa: BLE001 — reported through the exit code
        print(f"naked_alert: stop-coverage check failed, coverage unknown: {exc}")
        return EXIT_CHECK_FAILED

    print(
        f"stop coverage: {facts.naked_qty:.0f}/{facts.total_qty:.0f} naked "
        f"({facts.naked_pct:.1f}%), {exiting_summary(facts)}, "
        f"indeterminate {facts.indeterminate_qty:.0f}, ks={ks}"
    )
    path = state_path()

    if has_naked_exposure(facts, ks):
        if not args.dry_run:
            # Remembered so the first covered run afterwards announces the
            # recovery. The page about the exposure itself is the caller's.
            _write_state(
                path,
                NakedAlertState(
                    last_kind="naked",
                    last_naked_pct=facts.naked_pct,
                    last_run_date=facts.run_date,
                ),
            )
        print(f"NAKED: {naked_summary(facts)}")
        return EXIT_NAKED

    state = load_state(path)
    alert = decide(facts, state, kill_switch=ks)
    if alert is None or alert.kind != "recovered":
        print("no alert warranted")
        return EXIT_COVERED

    print(f"ALERT [{alert.kind}] {alert.title} — {alert.body}")
    if args.dry_run:
        return EXIT_COVERED
    delivery = send_ops_alert(alert.title, alert.body, kind="naked_book")
    print(f"naked_alert: {delivery.describe()}")
    # Recorded only once it reached someone. Recording it first would mean a
    # failed send permanently suppresses the all-clear it never delivered.
    if delivery.delivered:
        _write_state(path, alert.next_state)
    elif recovery_expired(state, facts.run_date):
        age = days_between(state.last_run_date, facts.run_date)
        print(
            f"naked_alert: all-clear for the {state.last_run_date} exposure reached no "
            f"channel in {age} days; no longer retried"
        )
        _write_state(path, alert.next_state)
    return EXIT_COVERED


def _write_state(path: Path, state: NakedAlertState) -> None:
    try:
        path.write_text(json.dumps(state.as_dict(), indent=2))
    except OSError as exc:  # a lost all-clear is not worth failing the check over
        print(f"naked_alert: could not record state ({exc})")


if __name__ == "__main__":
    sys.exit(main())
