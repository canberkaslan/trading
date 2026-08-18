"""Is the agent still *able* to act, or is it only able to hold?

A daily run that produces ten confident decisions and submits nothing looks
identical, from every metric this project reports, to a run that produced ten
Holds — and identical to a healthy run in the logs, because a policy refusal is
a success (`ExecutionResult.error` is False by design, see the Round-3
false-failure fix). Sharpe, drawdown and the GO verdict are all computed from
the equity curve, and a frozen book still has an equity curve: it tracks its
own marks and reports the tape's performance as the strategy's.

So the gap this module closes is not "did the strategy make money" but "did the
strategy still have a choice". Every rejected order is already persisted with
its reasons; nothing here re-derives a decision or touches the broker. It reads
order rows and answers three questions:

  * how many orders reached the broker in the window (`submitted`),
  * why the rest did not (`by_reason`, normalized so one bucket per cause),
  * how many consecutive run-days ended with zero submissions (`inert_run_days`).

`inert_run_days` counts DAYS THE AGENT RAN, not calendar days: weekends and
holidays produce no rows at all, and counting them as inert would report a
two-day inertia every Monday. A run day is any UTC date with at least one order
row, which is exactly the definition the daily run creates — it persists a row
per ticker whether or not the order was approved.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

# Reasons carry live numbers in a trailing parenthetical — e.g.
# "trimmed_to_zero_by_cash_cap (spendable=$0.00)". Grouping on the raw string
# would spread one cause across as many buckets as it had distinct balances,
# which is how a dominant, persistent blocker renders as a scatter of one-offs.
_DETAIL_SUFFIX = re.compile(r"\s*\(.*\)\s*$")


@dataclass(frozen=True)
class OrderRecord:
    """The order-flow facts this module needs, decoupled from the ORM row."""

    ticker: str
    side: str
    submitted_at_utc: datetime
    risk_approved: bool
    rejection_reasons: list[str]
    broker_order_id: str | None


@dataclass(frozen=True)
class ActionabilityReport:
    orders: int
    submitted: int
    refused: int
    # Reason -> count, normalized and ordered most-frequent-first. One order can
    # carry several reasons, so these sum to >= `refused`, never to `orders`.
    by_reason: dict[str, int] = field(default_factory=dict)
    # Consecutive most-recent run days that submitted nothing. 0 = the latest
    # run day reached the broker.
    inert_run_days: int = 0
    run_days: int = 0
    last_submitted_at_utc: datetime | None = None
    first_order_at_utc: datetime | None = None
    last_order_at_utc: datetime | None = None

    @property
    def dominant_reason(self) -> str | None:
        """The single reason blocking the most orders, or None if nothing was refused."""
        return next(iter(self.by_reason), None)

    def verdict(self, inert_threshold: int = 3) -> str:
        """'active' | 'inert' | 'idle'.

        'idle' is the honest answer when the window holds no order rows at all:
        the run may not have happened, and reporting a book that never ran as
        'inert' would blame the strategy for a scheduling failure.
        """
        if self.orders == 0:
            return "idle"
        if self.inert_run_days >= inert_threshold:
            return "inert"
        return "active"


def normalize_reason(reason: str) -> str:
    """Strip the trailing live-value parenthetical so one cause is one bucket."""
    return _DETAIL_SUFFIX.sub("", reason).strip()


def _run_date(order: OrderRecord) -> date:
    return order.submitted_at_utc.date()


def build_report(orders: list[OrderRecord]) -> ActionabilityReport:
    """Summarize order flow. Pure: no DB, no broker, no clock."""
    if not orders:
        return ActionabilityReport(orders=0, submitted=0, refused=0)

    ordered = sorted(orders, key=lambda o: o.submitted_at_utc)

    # An order counts as submitted only when the broker acknowledged it with an
    # id. `risk_approved` is the agent's own verdict on itself: approved orders
    # can still die at the broker, and treating intent as execution is the exact
    # optimism this module exists to catch.
    submitted = [o for o in ordered if o.broker_order_id]
    refused = [o for o in ordered if not o.broker_order_id]

    counts = Counter(
        normalize_reason(r) for o in refused for r in o.rejection_reasons if r.strip()
    )

    submitted_dates = {_run_date(o) for o in submitted}
    run_dates = sorted({_run_date(o) for o in ordered}, reverse=True)
    inert = 0
    for d in run_dates:
        if d in submitted_dates:
            break
        inert += 1

    return ActionabilityReport(
        orders=len(ordered),
        submitted=len(submitted),
        refused=len(refused),
        by_reason=dict(counts.most_common()),
        inert_run_days=inert,
        run_days=len(run_dates),
        last_submitted_at_utc=submitted[-1].submitted_at_utc if submitted else None,
        first_order_at_utc=ordered[0].submitted_at_utc,
        last_order_at_utc=ordered[-1].submitted_at_utc,
    )
