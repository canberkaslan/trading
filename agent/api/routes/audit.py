"""GET /v1/audit — the audit trail the backend already keeps, read back.

Two append-only tables have been written for months and never served:
`kill_switch_events` (who flipped the switch, from where, with what result)
and `order_updates` (an operator's reject / cancel, stamped). Risk refusals
live on `trade_orders.risk_approved = False` with their reasons. This route
unions exactly those — nothing reconstructed, nothing guessed.

What it deliberately does NOT list is anything without a record of its own.
An approval leaves an `order_updates` row, but so does the daily run's own
submission, and neither carries an actor — so an "approved by the operator"
row would be an inference, on the one screen someone points at to say "I did
not do that". Those kinds are named in `not_recorded` instead, so the client
can say the log is partial rather than let it look complete.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.models import KillSwitchEventRow, OrderUpdateRow, TradeOrderRow

from ..deps import get_repo, require_admin

router = APIRouter()

#: Action kinds the design lists that no table records yet.
NOT_RECORDED: list[str] = ["approve", "hold", "login", "setting", "report"]

#: The order_updates markers the reject / cancel routes write.
_OPERATOR_MARKERS: dict[str, Literal["reject", "cancel"]] = {
    "user_rejected": "reject",
    "user_cancelled": "cancel",
}

#: How many characters of an actor uid the log shows — enough to tell two
#: operators apart, not a full identifier on a screen.
_ACTOR_PREFIX = 8


class AuditEntry(BaseModel):
    id: str
    ts: datetime
    actor: Literal["operator", "system", "risk"]
    action: Literal["kill", "reject", "cancel", "refuse"]
    detail: str
    where: str
    #: Machine-keyed refusal reasons, for `refuse` rows only.
    reasons: list[str] = []


class AuditLog(BaseModel):
    entries: list[AuditEntry]
    not_recorded: list[str]


def _utc(ts: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _short(actor: str | None) -> str:
    if not actor:
        return "bilinmiyor"
    return actor if len(actor) <= _ACTOR_PREFIX else actor[:_ACTOR_PREFIX] + "…"


def _kill_entries(repo: TradeLogRepository, limit: int) -> list[AuditEntry]:
    with repo.session() as s:
        rows = s.execute(
            select(KillSwitchEventRow).order_by(KillSwitchEventRow.timestamp_utc.desc()).limit(limit)
        ).scalars().all()
        out = []
        for r in rows:
            by_operator = r.source == "api"
            detail = r.state if not r.detail else f"{r.state} · {r.detail}"
            out.append(AuditEntry(
                id=f"kill-{r.id}",
                ts=_utc(r.timestamp_utc),
                actor="operator" if by_operator else "system",
                action="kill",
                detail=detail,
                where=f"{r.source} · {_short(r.actor)}" if by_operator else r.source,
            ))
        return out


def _operator_order_entries(repo: TradeLogRepository, limit: int) -> list[AuditEntry]:
    with repo.session() as s:
        rows = s.execute(
            select(OrderUpdateRow, TradeOrderRow)
            .join(TradeOrderRow, TradeOrderRow.order_id == OrderUpdateRow.order_id)
            .where(OrderUpdateRow.error_message.in_(list(_OPERATOR_MARKERS)))
            .order_by(OrderUpdateRow.timestamp_utc.desc())
            .limit(limit)
        ).all()
        return [
            AuditEntry(
                id=f"upd-{u.id}",
                ts=_utc(u.timestamp_utc),
                actor="operator",
                action=_OPERATOR_MARKERS[u.error_message or ""],
                detail=f"{o.ticker} {o.side} {o.quantity}",
                where="api",
            )
            for u, o in rows
        ]


def _refusal_entries(repo: TradeLogRepository, limit: int) -> list[AuditEntry]:
    with repo.session() as s:
        rows = s.execute(
            select(TradeOrderRow)
            .where(TradeOrderRow.risk_approved.is_(False))
            .order_by(TradeOrderRow.submitted_at_utc.desc())
            .limit(limit)
        ).scalars().all()
        return [
            AuditEntry(
                id=f"refuse-{o.order_id}",
                ts=_utc(o.submitted_at_utc),
                actor="risk",
                action="refuse",
                detail=f"{o.ticker} {o.side} {o.quantity}",
                where="risk katmanı",
                reasons=[str(r) for r in (o.rejection_reasons_json or [])],
            )
            for o in rows
        ]


@router.get("", response_model=AuditLog)
async def get_audit(
    limit: int = Query(default=100, ge=1, le=500),
    _user: str = Depends(require_admin),
    repo: TradeLogRepository = Depends(get_repo),
) -> AuditLog:
    """Newest first, across the three recorded sources. Admin only: rows name actors."""
    entries = (
        _kill_entries(repo, limit)
        + _operator_order_entries(repo, limit)
        + _refusal_entries(repo, limit)
    )
    entries.sort(key=lambda e: e.ts, reverse=True)
    return AuditLog(entries=entries[:limit], not_recorded=NOT_RECORDED)
