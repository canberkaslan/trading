"""When to page about a book that has lost its protective stops.

`risk.stop_coverage` has been able to compute this number since the day it was
written, and nothing ever called it. The first run of the new position pass
found 75.5% of held shares naked — 8 of 10 names, on an account whose go-live
checklist assumes every entry ships a bracket. Nobody was told, because nothing
was asking.

The policy mirrors `inert_alert`: pure, and quiet unless there is NEW
information. A book that is 40% naked today and 40% naked tomorrow is one
problem, not two, and paging daily about it trains the reader to swipe the
alert away — which is how the next real one gets missed.

Three things deliberately do NOT page:

  * An empty book. Holding nothing is not an exposure.
  * A FLATTEN_ALL kill switch. The book is being closed on purpose; its stops
    going away is the intended outcome, not a fault.
  * Indeterminate coverage on its own. An order in a status the accounting does
    not recognise is not evidence of naked exposure, and paging on it would be
    paging on a guess. It is carried in the BODY of an alert that fires for
    other reasons, so the reader knows the number has a soft edge.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: Naked share of the book, in percent, at which this starts paging. Not zero:
#: a single partial fill leaves a few shares briefly uncovered between the fill
#: and the bracket arming, and paging on that is noise.
DEFAULT_THRESHOLD_PCT = 10.0

#: Re-page once exposure has grown by this much since the last alert. Without
#: it, a book drifting from 12% to 80% naked would page once and then go quiet
#: for the part that actually matters.
ESCALATION_STEP_PCT = 20.0


@dataclass(frozen=True)
class NakedAlertState:
    """What the last alert said, so the next one only fires on new information."""

    last_kind: str | None = None       # "naked" | "recovered" | None
    last_naked_pct: float = 0.0
    last_run_date: str | None = None   # ISO date the alerting run covered

    def as_dict(self) -> dict[str, object]:
        return {
            "last_kind": self.last_kind,
            "last_naked_pct": self.last_naked_pct,
            "last_run_date": self.last_run_date,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NakedAlertState:
        return cls(
            last_kind=d.get("last_kind"),
            last_naked_pct=float(d.get("last_naked_pct") or 0.0),
            last_run_date=d.get("last_run_date"),
        )


@dataclass(frozen=True)
class Alert:
    kind: str
    title: str
    body: str
    next_state: NakedAlertState


@dataclass(frozen=True)
class CoverageFacts:
    """The subset of a CoverageReport this policy needs.

    Taken as plain numbers rather than the report object so the policy can be
    tested without building an order book, and so a change to the report's shape
    cannot quietly change when the system pages.
    """

    total_qty: float
    naked_qty: float
    indeterminate_qty: float
    naked_symbols: tuple[str, ...] = ()
    run_date: str | None = None

    @property
    def naked_pct(self) -> float:
        return (self.naked_qty / self.total_qty * 100.0) if self.total_qty > 0 else 0.0


def decide(
    facts: CoverageFacts,
    state: NakedAlertState,
    kill_switch: str = "RUN",
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    escalation_step_pct: float = ESCALATION_STEP_PCT,
) -> Alert | None:
    """Return the alert this coverage warrants, or None to stay quiet."""
    if facts.total_qty <= 0:
        return None

    if kill_switch == "FLATTEN_ALL":
        # The book is being closed deliberately. Its stops disappearing is the
        # intended outcome of that, not a fault to report.
        return None

    pct = facts.naked_pct

    if pct < threshold_pct:
        if state.last_kind != "naked":
            return None
        return Alert(
            kind="recovered",
            title="✅ Stop coverage restored",
            body=(
                f"{100.0 - pct:.0f}% of the book is protected again "
                f"({facts.naked_qty:.0f} of {facts.total_qty:.0f} shares still naked)."
            ),
            next_state=replace(
                state, last_kind="recovered", last_naked_pct=pct, last_run_date=facts.run_date
            ),
        )

    # Above the threshold. Page on the first crossing, and again only once it
    # has grown by a full escalation step.
    if state.last_kind == "naked" and pct < state.last_naked_pct + escalation_step_pct:
        return None

    names = ", ".join(facts.naked_symbols[:6])
    more = "" if len(facts.naked_symbols) <= 6 else f" +{len(facts.naked_symbols) - 6} more"
    caveat = (
        ""
        if facts.indeterminate_qty <= 0
        else f" {facts.indeterminate_qty:.0f} shares are indeterminate and not counted as naked."
    )
    return Alert(
        kind="naked",
        title=f"⚠️ {pct:.0f}% of the book has no stop",
        body=(
            f"{facts.naked_qty:.0f} of {facts.total_qty:.0f} shares are unprotected"
            f"{': ' + names + more if names else ''}.{caveat}"
        ),
        next_state=replace(
            state, last_kind="naked", last_naked_pct=pct, last_run_date=facts.run_date
        ),
    )
