"""When to page a human about a book that has stopped acting.

`/v1/diagnostics/actionability` already answers "is the agent still able to
act". It answers it only when somebody opens the app and looks — and the whole
point of the diagnostic is that a frozen book looks healthy everywhere else, so
nobody has a reason to look. The live run went inert on 2026-08-11 and the GO
badge kept rendering for nine run days.

This module is the push side of that diagnostic: the policy that decides
whether today's report is worth waking somebody for. It is pure — no DB, no
clock, no network — so the interesting part (when NOT to send) is testable.

Three rules carry the design:

  * **'idle' never alerts.** An empty window means no order rows at all, which
    means the run did not happen. That is a scheduling failure, already owned by
    the daily-run non-zero exit and the healthchecks.io dead-man's switch.
    Reporting it here as "the strategy went quiet" blames the strategy for the
    cron, and an alert that cries about the wrong subsystem is one people learn
    to swipe away.
  * **One alert per run day, then only on escalation.** A book stuck for three
    weeks is the same news on day 4 as on day 3. Re-alert when the inertia
    deepens by `ESCALATION_STEP_RUN_DAYS`, or when the dominant blocker
    changes — a new reason is new information even at the same depth.
  * **Recovery is worth one push.** Somebody told the book is frozen has no way
    to learn it thawed; without this they either keep checking or stop trusting
    the alert. It fires once, only if we actually alerted about the freeze.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from ..execution.actionability import ActionabilityReport

# How much deeper the inertia must get before the same freeze pages again.
# 3 -> 8 -> 13 run days: roughly weekly on a 5-day trading week, which is the
# cadence a stuck book deserves — present, not nagging.
ESCALATION_STEP_RUN_DAYS = 5


@dataclass(frozen=True)
class AlertState:
    """What the last alert said, so the next one only fires on new information.

    Persisted between runs. `last_run_date` is the run day of the report that
    triggered the alert (not the wall clock): a re-run of the same trading day
    must not push twice, and a manual catch-up run of an old day must not look
    like a new day's news.
    """

    last_kind: str | None = None          # "inert" | "recovered" | None (never alerted)
    last_run_date: str | None = None      # ISO date of the report's newest order row
    last_inert_run_days: int = 0
    last_dominant_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "last_kind": self.last_kind,
            "last_run_date": self.last_run_date,
            "last_inert_run_days": self.last_inert_run_days,
            "last_dominant_reason": self.last_dominant_reason,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "AlertState":
        """Tolerant parse — a corrupt or hand-edited state file must not stop alerting.

        The failure mode of an unreadable state file is one extra push, which is
        strictly better than the alternative (silence about a frozen book
        because a JSON blob got mangled), so every field falls back to "we have
        never alerted" rather than raising.
        """
        if not isinstance(raw, dict):
            return cls()
        kind = raw.get("last_kind")
        run_date = raw.get("last_run_date")
        days = raw.get("last_inert_run_days")
        reason = raw.get("last_dominant_reason")
        return cls(
            last_kind=kind if isinstance(kind, str) else None,
            last_run_date=run_date if isinstance(run_date, str) else None,
            last_inert_run_days=days if isinstance(days, int) and days >= 0 else 0,
            last_dominant_reason=reason if isinstance(reason, str) else None,
        )


@dataclass(frozen=True)
class Alert:
    """A push to send, plus the state to persist once it is sent."""

    kind: str            # "inert" | "recovered"
    title: str
    body: str
    next_state: AlertState


def decide(
    report: ActionabilityReport,
    state: AlertState,
    inert_threshold: int = 3,
    escalation_step: int = ESCALATION_STEP_RUN_DAYS,
) -> Alert | None:
    """Return the alert today's report warrants, or None to stay quiet."""
    verdict = report.verdict(inert_threshold=inert_threshold)

    if verdict == "idle":
        # No order rows: the run did not happen. Not this alert's subject.
        return None

    run_date = report.last_order_at_utc.date() if report.last_order_at_utc else None

    if verdict == "active":
        if state.last_kind != "inert":
            return None
        return Alert(
            kind="recovered",
            title="✅ Order flow resumed",
            body=_recovered_body(report),
            next_state=replace(
                state,
                last_kind="recovered",
                last_run_date=run_date.isoformat() if run_date else None,
                last_inert_run_days=0,
                last_dominant_reason=None,
            ),
        )

    # verdict == "inert"
    if run_date is not None and state.last_run_date == run_date.isoformat():
        # Already alerted off this run day (a re-run, or a second invocation).
        return None

    if state.last_kind == "inert":
        deepened = report.inert_run_days >= state.last_inert_run_days + escalation_step
        reason_changed = report.dominant_reason != state.last_dominant_reason
        if not (deepened or reason_changed):
            return None

    return Alert(
        kind="inert",
        title=f"⚠️ No orders reaching the broker — {report.inert_run_days} run days",
        body=_inert_body(report),
        next_state=AlertState(
            last_kind="inert",
            last_run_date=run_date.isoformat() if run_date else None,
            last_inert_run_days=report.inert_run_days,
            last_dominant_reason=report.dominant_reason,
        ),
    )


def _inert_body(report: ActionabilityReport) -> str:
    parts = [f"{report.submitted}/{report.orders} orders submitted in the window"]
    if report.dominant_reason:
        blocked = report.by_reason.get(report.dominant_reason, 0)
        parts.append(f"top blocker: {report.dominant_reason} ({blocked})")
    parts.append(f"last broker ack: {_ack(report.last_submitted_at_utc)}")
    return " · ".join(parts)


def _recovered_body(report: ActionabilityReport) -> str:
    return (
        f"{report.submitted}/{report.orders} orders submitted in the window · "
        f"last broker ack: {_ack(report.last_submitted_at_utc)}"
    )


def _ack(value: datetime | None) -> str:
    """Format the last ack as a bare UTC date, or say plainly that there is none.

    "never" rather than an empty slot: a window whose every order was refused is
    a different (worse) story than one whose last ack merely fell out of range.
    """
    return value.date().isoformat() if value is not None else "never"
