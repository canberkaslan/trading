"""/v1/orders — trade history, approval, kill switch."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.execution import ExecutionConfig, submit_order
from tradingagents_us.execution.flatten import flatten_all
from tradingagents_us.risk.kill_switch import FileKillSwitchReader, default_kill_switch_path
from tradingagents_us.schemas import KillSwitchState, OrderUpdate, TradeOrder
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.models import AgentDecisionRow, TradeOrderRow
from tradingagents_us.storage.repository import row_to_decision

from ..deps import get_alpaca, get_repo, require_token

router = APIRouter()


class OrderListItem(BaseModel):
    """Wire format for /v1/orders — joins local row + broker view where possible."""
    order_id: str
    decision_id: str
    ticker: str
    side: str
    quantity: int
    order_type: str
    stop_loss: float
    risk_approved: bool
    rejection_reasons: list[str]
    broker_order_id: str | None
    broker_status: str | None
    filled_qty: int = 0
    avg_fill_price: float | None = None
    submitted_at_utc: datetime


class KillSwitchUpdate(BaseModel):
    state: KillSwitchState


@router.get("", response_model=list[OrderListItem])
async def list_orders(
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
    alpaca: AlpacaClient = Depends(get_alpaca),
) -> list[OrderListItem]:
    """Recent orders from local DB, enriched with current Alpaca status where available."""
    rows = repo.list_open_orders()

    # Best-effort broker lookup; if Alpaca is unreachable, return DB-only rows
    broker_by_id: dict[str, dict] = {}
    try:
        broker_orders = alpaca.list_orders(status="all", limit=50)
        for o in broker_orders:
            broker_by_id[o.id] = {
                "status": o.status,
                "filled_qty": int(o.filled_qty),
                "avg_fill_price": o.filled_avg_price,
            }
    except Exception:
        pass
    finally:
        alpaca.close()

    out: list[OrderListItem] = []
    for r in rows:
        bk = broker_by_id.get(r.broker_order_id or "")
        out.append(
            OrderListItem(
                order_id=r.order_id,
                decision_id=r.decision_id,
                ticker=r.ticker,
                side=r.side,
                quantity=r.quantity,
                order_type=r.order_type,
                stop_loss=r.stop_loss,
                risk_approved=r.risk_approved,
                rejection_reasons=r.rejection_reasons_json or [],
                broker_order_id=r.broker_order_id,
                broker_status=bk["status"] if bk else None,
                filled_qty=bk["filled_qty"] if bk else 0,
                avg_fill_price=bk["avg_fill_price"] if bk else None,
                submitted_at_utc=r.submitted_at_utc,
            )
        )
    return out


@router.get("/pending", response_model=list[OrderListItem])
async def list_pending_orders(
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> list[OrderListItem]:
    """Orders persisted via `scripts/trade.py --hold` awaiting approval.

    Excludes orders that have a broker_order_id (already at broker) OR a
    REJECTED update (user rejected via /reject)."""
    from tradingagents_us.storage.models import OrderUpdateRow

    out: list[OrderListItem] = []
    with repo.session() as s:
        rows = s.execute(
            select(TradeOrderRow).order_by(TradeOrderRow.submitted_at_utc.desc())
        ).scalars().all()
        for r in rows:
            if r.broker_order_id is not None:
                continue
            # Skip if any REJECTED update exists
            rejected = s.execute(
                select(OrderUpdateRow).where(
                    OrderUpdateRow.order_id == r.order_id,
                    OrderUpdateRow.status == "REJECTED",
                ).limit(1)
            ).scalar_one_or_none()
            if rejected is not None:
                continue
            out.append(OrderListItem(
                order_id=r.order_id, decision_id=r.decision_id, ticker=r.ticker,
                side=r.side, quantity=r.quantity, order_type=r.order_type,
                stop_loss=r.stop_loss, risk_approved=r.risk_approved,
                rejection_reasons=r.rejection_reasons_json or [],
                broker_order_id=None, broker_status="PENDING",
                filled_qty=0, avg_fill_price=None,
                submitted_at_utc=r.submitted_at_utc,
            ))
    return out


@router.post("/{order_id}/approve")
async def approve_order(
    order_id: str,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> dict:
    """Mobile-approved submission. Re-runs the executor with dry_run=False so
    the stale + entry sanity guards re-evaluate against the *current* market
    state — not the one captured when the order was held."""
    # Kill switch gates THIS path too — an armed PAUSE_NEW/FLATTEN_ALL must
    # block a stale Approve tap the same way it blocks the daily run.
    ks_state = FileKillSwitchReader().read()
    if ks_state != "RUN":
        try:
            repo.append_kill_event(
                state=ks_state, actor=user, source="api",
                detail=f"blocked approve of order {order_id}",
            )
        except Exception:
            pass
        raise HTTPException(409, f"kill switch is {ks_state} — approvals disabled")

    # Load order + decision rows
    with repo.session() as s:
        order_row = s.get(TradeOrderRow, order_id)
        if order_row is None:
            raise HTTPException(404, f"order not found: {order_id}")
        if order_row.broker_order_id is not None:
            raise HTTPException(409, f"order already submitted: broker={order_row.broker_order_id}")
        decision_row = s.get(AgentDecisionRow, order_row.decision_id)

    if decision_row is None:
        raise HTTPException(404, f"decision not found for order {order_id}")

    decision = row_to_decision(decision_row)

    # Rebuild TradeOrder for the executor
    order = TradeOrder(
        order_id=order_row.order_id, decision_id=order_row.decision_id,
        ticker=order_row.ticker, market=order_row.market,
        side=order_row.side, quantity=order_row.quantity,
        order_type=order_row.order_type, limit_price=order_row.limit_price,
        stop_loss=order_row.stop_loss, risk_approved=order_row.risk_approved,
        rejection_reasons=order_row.rejection_reasons_json or [],
        submitted_at_utc=order_row.submitted_at_utc,
    )

    # Fetch current price for re-validation
    current_price: float | None = None
    try:
        from tradingagents_us.dataflows.polygon import PolygonClient
        with PolygonClient() as p:
            results = p.previous_close(order.ticker).get("results") or []
            if results:
                current_price = float(results[0].get("c") or 0) or None
    except Exception:
        pass

    result = submit_order(
        order, config=ExecutionConfig(dry_run=False),
        decision=decision, current_price=current_price,
    )
    repo.save_order(order, broker_order_id=result.broker_order_id)
    repo.append_update(result.update)

    if not result.submitted:
        raise HTTPException(
            422,
            {"order_id": order_id, "status": result.update.status,
             "refusal_reasons": result.refusal_reasons or [],
             "error": result.update.error_message},
        )

    return {
        "order_id": order_id,
        "broker_order_id": result.broker_order_id,
        "status": result.update.status,
    }


@router.post("/{order_id}/reject")
async def reject_order(
    order_id: str,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> dict:
    """User-rejected: record a REJECTED update so the order disappears from
    the pending list. No broker call (nothing was submitted)."""
    with repo.session() as s:
        order_row = s.get(TradeOrderRow, order_id)
        if order_row is None:
            raise HTTPException(404, f"order not found: {order_id}")
        if order_row.broker_order_id is not None:
            raise HTTPException(409, "order already at broker; use /cancel")

    repo.append_update(OrderUpdate(
        order_id=order_id, status="REJECTED",
        error_message="user_rejected",
        timestamp_utc=datetime.now(timezone.utc),
    ))
    return {"order_id": order_id, "status": "REJECTED"}


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
    alpaca: AlpacaClient = Depends(get_alpaca),
) -> dict[str, str]:
    """Cancel a pending order at the broker, mark it cancelled in local DB."""
    try:
        # Look up our row to get the broker_order_id
        for r in repo.list_open_orders():
            if r.order_id == order_id:
                if r.broker_order_id:
                    alpaca.cancel_order(r.broker_order_id)
                return {"status": "cancelled", "order_id": order_id}
        raise HTTPException(404, f"order not found: {order_id}")
    finally:
        alpaca.close()


@router.post("/kill-switch")
async def set_kill_switch(
    body: KillSwitchUpdate,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> dict[str, str]:
    """Mobile-controlled remote kill switch. See ADR-005.

    Enforced by FileKillSwitchReader in trade.py's circuit breaker and by
    the daily_run.sh pre-check (which also executes FLATTEN_ALL). The
    single-box file backend is fine while API + trader share a host; the
    DynamoDB reader exists for a future multi-host split.
    """
    flag_path = default_kill_switch_path()
    # Atomic replace — a crash mid-write must never leave a truncated file
    # (the reader treats empty as PAUSE_NEW, but never risk it).
    tmp_path = f"{flag_path}.tmp"
    with open(tmp_path, "w") as f:
        f.write(body.state)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, flag_path)

    try:
        repo.append_kill_event(state=body.state, actor=user, source="api")
    except Exception:
        pass  # audit is best-effort; the state write above already took effect

    # FLATTEN_ALL executes NOW, not at the next 22:30 UTC run — the switch
    # is a panic button, hours of latency defeats it. kill_check remains
    # the daily backstop if this attempt fails.
    flatten_summary: str | None = None
    if body.state == "FLATTEN_ALL":
        try:
            result = flatten_all()
        except Exception as exc:
            try:
                repo.append_kill_event(
                    state="FLATTEN_ALL", actor=user, source="api",
                    detail=f"immediate flatten FAILED: {exc}",
                )
            except Exception:
                pass
            raise HTTPException(
                502,
                f"kill switch armed, but immediate flatten failed: {exc} — "
                f"the daily-run backstop will retry",
            ) from exc
        try:
            repo.append_kill_event(
                state="FLATTEN_ALL", actor=user, source="api",
                detail=("noop: " if result.noop else ("executed: " if result.ok else "partial: "))
                + result.summary,
            )
        except Exception:
            pass
        if not result.ok:
            raise HTTPException(
                502,
                f"kill switch armed, but flatten was PARTIAL: {result.summary}",
            )
        flatten_summary = result.summary

    resp = {"state": body.state, "path": flag_path}
    if flatten_summary is not None:
        resp["flatten"] = flatten_summary
    return resp


@router.get("/kill-switch")
async def get_kill_switch(user: str = Depends(require_token)) -> dict[str, str]:
    flag_path = default_kill_switch_path()
    try:
        with open(flag_path) as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return {"state": "RUN"}
    # Mirror FileKillSwitchReader: an armed-then-emptied file fails safe.
    if raw not in ("RUN", "PAUSE_NEW", "FLATTEN_ALL"):
        return {"state": "PAUSE_NEW"}
    return {"state": raw}
