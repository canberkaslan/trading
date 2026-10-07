"""A council's decision, held between the two passes of a parallel daily run.

With COUNCIL_PARALLELISM above 1, daily_run.sh councils several tickers at
once and none of them places an order: each records its decision here
(`scripts/trade.py --plan-dir`), and one process then goes through the records
in universe order and sends what the book, read again right then, still allows
(`scripts/submit_plans.py`).

A record is a file, `<TICKER>.plan.json`, in a directory the run creates and
removes. It is deliberately not a trade_orders row: the app's approval queue
(/v1/orders/pending) lists every order row with no broker id, and /approve would
send one, stale, whenever someone tapped it.

A record is sent at most once, and only by the run that wrote it. The submit
pass takes it by renaming it (atomic within a directory), so a second pass over
the same directory finds nothing to take, and it refuses a record that names
another run, date or ticker. The order the council's own read of the book would
have placed is kept for the log only: the submit pass sizes the decision again.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, date, datetime
from pathlib import Path

from ..schemas import AgentDecision, TradeOrder

PLAN_VERSION = 1
_PLANNED = ".plan.json"
_CLAIMED = ".plan.claimed.json"
_TICKER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*")


class PlanRefusedError(ValueError):
    """A record the submit pass must not act on."""


def _checked(ticker: str) -> str:
    # The ticker names a file: nothing that could leave the directory.
    if not _TICKER.fullmatch(ticker):
        raise PlanRefusedError(f"not a ticker: {ticker!r}")
    return ticker


def plan_path(plan_dir: str | Path, ticker: str) -> Path:
    return Path(plan_dir) / f"{_checked(ticker)}{_PLANNED}"


def write_plan(
    plan_dir: str | Path,
    *,
    ticker: str,
    run_id: str,
    run_date: date,
    decision: AgentDecision,
    order: TradeOrder,
) -> Path:
    """Record `decision` for the submit pass. Written whole or not at all.

    Filed under the ticker the run asked about, the name daily_run.sh looks
    for: a decision for any other name would be a record nobody sends, so it
    is refused here, loudly, rather than left to look like a skipped council.
    """
    if decision.ticker != ticker:
        raise PlanRefusedError(f"decision is for {decision.ticker!r}, the council for {ticker!r}")
    path = plan_path(plan_dir, ticker)
    record = {
        "version": PLAN_VERSION,
        "run_id": run_id,
        "date": run_date.isoformat(),
        "ticker": ticker,
        "created_utc": datetime.now(UTC).isoformat(),
        "decision": decision.model_dump(mode="json"),
        "planned_order": {
            "side": order.side,
            "quantity": order.quantity,
            "risk_approved": order.risk_approved,
            "rejection_reasons": order.rejection_reasons,
        },
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def claim_plan(plan_dir: str | Path, ticker: str) -> Path | None:
    """Take the ticker's record so no one else can; None when there is none."""
    planned = plan_path(plan_dir, ticker)
    claimed = planned.with_name(f"{ticker}{_CLAIMED}")
    try:
        os.rename(planned, claimed)
    except FileNotFoundError:
        return None
    return claimed


def load_claimed(path: Path, *, run_id: str, run_date: str, ticker: str) -> AgentDecision:
    """The decision in a claimed record, if it is this run's record for this ticker."""
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PlanRefusedError(f"unreadable record {path.name}: {exc}") from exc
    if not isinstance(record, dict):
        raise PlanRefusedError(f"unreadable record {path.name}: not an object")
    expected = {"version": PLAN_VERSION, "run_id": run_id, "date": run_date, "ticker": ticker}
    for key, want in expected.items():
        if record.get(key) != want:
            raise PlanRefusedError(
                f"record {key} is {record.get(key)!r}, this pass is for {want!r}"
            )
    try:
        decision = AgentDecision.model_validate(record["decision"])
    except (KeyError, ValueError) as exc:
        raise PlanRefusedError(f"record holds no valid decision: {exc}") from exc
    if decision.ticker != ticker:
        raise PlanRefusedError(f"record's decision is for {decision.ticker!r}, not {ticker!r}")
    return decision
