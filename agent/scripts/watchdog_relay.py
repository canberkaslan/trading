#!/usr/bin/env python3
"""Carry the watchdog's cadence on a chain of self-dispatching runs.

The watchdog runs off the box so a dead host cannot silence it. That worked —
and then GitHub's scheduler silenced it anyway: across the outage that began
2026-08-24 the `*/30` cron delivered 22, 18, 3, 2, 6, 6 runs a day against 48
asked for, with a 13.9-hour hole in the middle of a dark box. An alert that can
be half a day late is not an alert, it is a record.

Cron cannot be repaired with more cron. `workflow_dispatch`, though, fires when
asked — and it is one of the two events GitHub exempts from the rule that a
`GITHUB_TOKEN` cannot start a workflow, so a run can start its own successor
with no PAT and no third-party service. This script is that loop:

    --run            probe every 30 min for ~5h, then dispatch the next link
    --check-chain    is a chain alive? (used by the cron backstop to restart one)

`--run` is deliberately hard to kill. Every probe is wrapped: a transient DNS
failure or a GitHub 502 costs one reading, never the chain. The hand-off is the
one step whose failure actually matters, so it is retried, and if it still fails
the script says so loudly on stderr — the cron backstop is what recovers from
there, which is exactly the rare-event role cron is fit for.

It exits 0 even when the box is dark, for the same reason `watchdog.py` does:
the incident issue is the signal, and a red run beside it is a second
notification for one piece of news.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from scripts.watchdog import _github_request, check_once  # noqa: E402
from tradingagents_us.monitoring.relay import (  # noqa: E402
    DEFAULT_BUDGET_S,
    DEFAULT_INTERVAL_S,
    MAX_BUDGET_S,
    RunSummary,
    chain_state,
    next_tick,
    parse_seconds,
    planned_probes,
)

DEFAULT_ISSUE_REPO = "canberkaslan/trading"
RELAY_WORKFLOW = "watchdog-relay.yml"
DISPATCH_ATTEMPTS = 3
DISPATCH_BACKOFF_S = 5.0


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", DEFAULT_ISSUE_REPO)


def _own_run_ids() -> list[int]:
    """This run's id, so an aliveness check cannot be satisfied by itself.

    Empty off a runner, or when `GITHUB_RUN_ID` is not an integer. Excluding
    nothing is the safe failure here — it can only make the check conclude a
    chain is alive when it is not, and the scheduled backstop still recovers
    from that; inventing an id could exclude a real link.
    """
    raw = os.environ.get("GITHUB_RUN_ID")
    if raw is None or not raw.strip().isdigit():
        return []
    return [int(raw.strip())]


def _parse_run_time(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def list_relay_runs(token: str | None) -> list[RunSummary]:
    """Every relay run GitHub currently considers unfinished.

    Asking the API for `status=in_progress` alone would miss a run that is
    queued behind the concurrency group — which is the normal state during a
    hand-off, and reading it as "chain is dead" would dispatch a third link
    every time the chain worked correctly.
    """
    runs: dict[int, RunSummary] = {}
    base = f"https://api.github.com/repos/{_repo()}/actions/workflows/{RELAY_WORKFLOW}/runs"
    for status in ("in_progress", "queued", "waiting"):
        try:
            raw = _github_request("GET", f"{base}?status={status}&per_page=50", token=token)
        except Exception as exc:  # a partial answer beats refusing to answer
            print(f"watchdog-relay: could not list {status} runs ({exc})", file=sys.stderr)
            continue
        if not isinstance(raw, dict):
            continue
        for entry in raw.get("workflow_runs") or []:
            if not isinstance(entry, dict):
                continue
            run_id = entry.get("id")
            if not isinstance(run_id, int):
                continue
            runs[run_id] = RunSummary(
                run_id=run_id,
                status=str(entry.get("status") or "unknown"),
                started_at=_parse_run_time(entry.get("run_started_at") or entry.get("created_at")),
            )
    return list(runs.values())


def dispatch_next(token: str | None, ref: str) -> bool:
    """Ask GitHub to start the next link. True if it accepted.

    `chain=true` tells the successor's guard job not to look for a live chain
    before starting: this run is still in progress at the moment it asks, so an
    honest aliveness check would find itself and decline — ending the chain on
    every single hand-off.
    """
    url = f"https://api.github.com/repos/{_repo()}/actions/workflows/{RELAY_WORKFLOW}/dispatches"
    payload = {"ref": ref, "inputs": {"chain": "true"}}
    for attempt in range(1, DISPATCH_ATTEMPTS + 1):
        try:
            _github_request("POST", url, token=token, payload=payload)
        except Exception as exc:
            print(
                f"watchdog-relay: hand-off attempt {attempt}/{DISPATCH_ATTEMPTS} failed ({exc})",
                file=sys.stderr,
            )
            if attempt < DISPATCH_ATTEMPTS:
                time.sleep(DISPATCH_BACKOFF_S * attempt)
            continue
        print(f"watchdog-relay: dispatched the next link on {ref}")
        return True
    print(
        "watchdog-relay: HAND-OFF FAILED — the chain has ended here. The scheduled "
        "backstop in watchdog-relay.yml is now the only thing that will restart it.",
        file=sys.stderr,
    )
    return False


def _emit_output(name: str, value: str) -> None:
    """Publish a value to the workflow's later steps, if we are in one."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    except OSError as exc:
        print(f"watchdog-relay: could not write GITHUB_OUTPUT ({exc})", file=sys.stderr)


def check_chain(token: str | None, now: datetime, budget_s: float) -> bool:
    """Decide whether this trigger should start a link. Returns that decision.

    The guard runs *inside* a `watchdog-relay` run, so the asking run is itself
    `in_progress` in the listing. Left in, it is the only thing the check ever
    finds: the first live dispatch reported "relay run 33364317803 is in_progress
    — chain is carrying the cadence" about itself and skipped the relay job. Its
    own id is excluded, and only its own.

    If the listing itself failed, `list_relay_runs` hands back what it has —
    possibly nothing — and this reads as "no chain, start one". That bias is
    deliberate: the two ways to be wrong are a redundant link (which the
    `watchdog-relay` concurrency group queues and the next dispatch cancels) and
    no watchdog at all. Only one of those is a problem.
    """
    state = chain_state(
        list_relay_runs(token), now, budget_s=budget_s, exclude_ids=_own_run_ids()
    )
    print(json.dumps({"alive": state.alive, "stalled": state.stalled, "detail": state.detail}))
    _emit_output("start", "true" if state.needs_start else "false")
    return state.needs_start


def run_link(token: str | None, ref: str, interval_s: float, budget_s: float) -> None:
    """Probe on the interval for the length of the window, then hand off.

    The window is measured from the moment this link started, not from the last
    probe, so the cadence cannot drift across a five-hour run — and a probe that
    overruns its slot costs that one reading instead of pushing every later one
    late.
    """
    started_at = datetime.now(UTC)
    print(
        f"watchdog-relay: link started {started_at.isoformat()} — "
        f"{planned_probes(interval_s, budget_s)} probes, one every {interval_s / 60:.0f} min, "
        f"handing off after {budget_s / 3600:.1f}h"
    )

    index = 0
    while True:
        try:
            check_once(datetime.now(UTC))
        except Exception as exc:  # one bad reading must not end the cadence
            print(f"watchdog-relay: probe {index} failed ({exc})", file=sys.stderr)

        tick = next_tick(started_at, datetime.now(UTC), interval_s, budget_s)
        if tick is None:
            break
        index = tick.index
        print(f"watchdog-relay: probe {index} due {tick.due_at.isoformat()} "
              f"(sleeping {tick.sleep_s / 60:.1f} min)")
        time.sleep(tick.sleep_s)

    dispatch_next(token, ref)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="probe on a loop, then hand off")
    mode.add_argument(
        "--check-chain", action="store_true", help="report whether a relay chain is alive"
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    interval_s = parse_seconds(os.environ.get("WATCHDOG_RELAY_INTERVAL_S"), DEFAULT_INTERVAL_S)
    budget_s = parse_seconds(
        os.environ.get("WATCHDOG_RELAY_BUDGET_S"), DEFAULT_BUDGET_S, maximum=MAX_BUDGET_S
    )

    if args.check_chain:
        check_chain(token, datetime.now(UTC), budget_s)
        return 0

    run_link(token, ref, interval_s, budget_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
