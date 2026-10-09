"""When to page about a book that has lost its protective stops.

`risk.stop_coverage` has been able to compute this number since the day it was
written, and nothing ever called it. The first run of the new position pass
found 75.5% of held shares naked — 8 of 10 names, on an account whose go-live
checklist assumes every entry ships a bracket. Nobody was told, because nothing
was asking.

The naked-book check is now a failure of the daily run, and `daily_run.sh`
pages on every run an exposure persists. That overrides the rationing below on
purpose: after the position pass has back-filled stops, a share still naked is
a broken protection floor, not news. `scripts/naked_alert.py` uses this policy
only for the one-time all-clear.

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
  * A lot our own time exit is selling at the next open (`exiting`, see
    `risk.stop_coverage`). It has no stop, and needs none: the exit reserves
    every share of it and the market is shut. Counted as naked, every
    time-exit night paged, and the night after announced a recovery. It is
    named in the body of any alert that fires, so "protected" never stands
    for "on its way out".
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

#: Days an undelivered all-clear keeps being retried, counted from the run that
#: last saw the book naked. The retry is for a send that failed in transit. On
#: the live box no channel was configured at all, so the all-clear for the
#: 2026-10-06 exposure went to nobody on every run after it, read in the journal
#: as fresh news each morning, and would have opened the first channel ever
#: configured with a recovery from weeks before.
RECOVERY_RETRY_DAYS = 7

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
    #: Shares no stop covers that our own working time exit holds back, and
    #: whose. Not naked, not protected: on their way out at the next open.
    exiting_qty: float = 0.0
    exiting_symbols: tuple[str, ...] = ()

    @property
    def naked_pct(self) -> float:
        return (self.naked_qty / self.total_qty * 100.0) if self.total_qty > 0 else 0.0

    def exiting_note(self) -> str:
        """' 32 shares are exiting at the next open under our time exit: GOOGL.', or ''."""
        if self.exiting_qty <= 0:
            return ""
        names = ", ".join(self.exiting_symbols)
        return (
            f" {self.exiting_qty:.0f} shares are exiting at the next open under our"
            f" time exit{': ' + names if names else ''}."
        )


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
                f"{100.0 - pct:.0f}% of the book is covered again "
                f"({facts.naked_qty:.0f} of {facts.total_qty:.0f} shares still naked)."
                f"{_last_seen_naked(state, facts.run_date)}{facts.exiting_note()}"
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
            f"{': ' + names + more if names else ''}.{caveat}{facts.exiting_note()}"
        ),
        next_state=replace(
            state, last_kind="naked", last_naked_pct=pct, last_run_date=facts.run_date
        ),
    )


def days_between(earlier: str | None, later: str | None) -> int | None:
    """Whole days from one ISO date to a later one; None if either is missing or unreadable."""
    if not earlier or not later:
        return None
    try:
        return (date.fromisoformat(later) - date.fromisoformat(earlier)).days
    except ValueError:
        return None


def recovery_expired(
    state: NakedAlertState, run_date: str | None, retry_days: int = RECOVERY_RETRY_DAYS
) -> bool:
    """True once an all-clear for `state`'s exposure has been retried long enough.

    Only an exposure with a readable date can expire: without one there is no
    age to measure, and dropping an all-clear on a guess is the failure the
    retry exists to prevent.
    """
    age = days_between(state.last_run_date, run_date)
    return state.last_kind == "naked" and age is not None and age >= retry_days


def _last_seen_naked(state: NakedAlertState, run_date: str | None) -> str:
    """' Last seen naked on 2026-10-06 (12% of the book, 3 days ago).', or ''.

    An all-clear can go out runs after the exposure it clears (it is retried
    until a channel takes it), so it carries the date of the exposure itself.
    Without it a late delivery reads as news about tonight's book.
    """
    if not state.last_run_date:
        return ""
    age = days_between(state.last_run_date, run_date)
    ago = f", {age} day{'' if age == 1 else 's'} ago" if age is not None and age > 0 else ""
    return (
        f" Last seen naked on {state.last_run_date} ({state.last_naked_pct:.0f}% of the book{ago})."
    )
