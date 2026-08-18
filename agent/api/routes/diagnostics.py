"""/v1/diagnostics — is the agent still able to act?

Companion to /v1/eval. The scorecard answers "how did the book do"; this
answers "did the book still have a choice". Both are needed because they can
disagree: a fully-invested basket that no longer passes its own sizing caps
keeps posting the tape's Sharpe while submitting nothing, and every metric on
the scorecard is computed from equity, which a frozen book still has.

Read-only, DB-only, off the decision path. No broker call: a diagnostic that
depends on Alpaca reports "unreachable" exactly when a broker outage is the
thing you are trying to diagnose.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from tradingagents_us.execution.actionability import OrderRecord, build_report
from tradingagents_us.storage import TradeLogRepository

from ..deps import get_repo, require_token

router = APIRouter()

# 3 consecutive run days with nothing reaching the broker. One day is a normal
# all-Hold day; three is a pattern worth a human looking at it.
INERT_THRESHOLD_RUN_DAYS = 3


class ActionabilityResponse(BaseModel):
    # "active" | "inert" | "idle" — 'idle' means no order rows at all in the
    # window (the run may simply not have happened), which must not be reported
    # as the strategy having gone quiet.
    verdict: str
    window_days: int
    orders: int
    submitted: int
    refused: int
    # Normalized reason -> count, most frequent first. Sums to >= `refused`:
    # one order can be refused for several reasons at once.
    by_reason: dict[str, int]
    dominant_reason: str | None
    # Consecutive most-recent days-the-agent-ran that submitted nothing.
    # Counted in run days, not calendar days, so weekends do not inflate it.
    inert_run_days: int
    run_days: int
    inert_threshold_run_days: int
    last_submitted_at_utc: datetime | None
    first_order_at_utc: datetime | None
    last_order_at_utc: datetime | None


@router.get("/actionability", response_model=ActionabilityResponse)
async def actionability(
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
    days: int = Query(30, ge=1, le=365, description="lookback window in calendar days"),
) -> ActionabilityResponse:
    """Order-flow health over the last `days`: what got submitted, and what blocked the rest."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = repo.list_orders_since(since=since)

    report = build_report(
        [
            OrderRecord(
                ticker=r.ticker,
                side=r.side,
                submitted_at_utc=r.submitted_at_utc,
                risk_approved=r.risk_approved,
                # Stored as JSON; a legacy row could hold null or a bare string,
                # and a diagnostic that 500s on its own history is worthless.
                rejection_reasons=_as_reasons(r.rejection_reasons_json),
                broker_order_id=r.broker_order_id,
            )
            for r in rows
        ]
    )

    return ActionabilityResponse(
        verdict=report.verdict(inert_threshold=INERT_THRESHOLD_RUN_DAYS),
        window_days=days,
        orders=report.orders,
        submitted=report.submitted,
        refused=report.refused,
        by_reason=report.by_reason,
        dominant_reason=report.dominant_reason,
        inert_run_days=report.inert_run_days,
        run_days=report.run_days,
        inert_threshold_run_days=INERT_THRESHOLD_RUN_DAYS,
        last_submitted_at_utc=report.last_submitted_at_utc,
        first_order_at_utc=report.first_order_at_utc,
        last_order_at_utc=report.last_order_at_utc,
    )


def _as_reasons(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None]
    if isinstance(raw, str) and raw.strip():
        return [raw]
    return []
