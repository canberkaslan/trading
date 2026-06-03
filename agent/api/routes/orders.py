"""/v1/orders — trade history, approval, kill switch."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.schemas import KillSwitchState
from tradingagents_us.storage import TradeLogRepository

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
) -> dict[str, str]:
    """Mobile-controlled remote kill switch. See ADR-005.

    Phase 5 dev: writes to a local flag file. Phase 4+ prod replaces with
    DynamoDB so EC2 agents can poll it.
    """
    flag_path = os.environ.get("KILL_SWITCH_PATH", "./kill_switch.state")
    with open(flag_path, "w") as f:
        f.write(body.state)
    return {"state": body.state, "path": flag_path}


@router.get("/kill-switch")
async def get_kill_switch(user: str = Depends(require_token)) -> dict[str, str]:
    flag_path = os.environ.get("KILL_SWITCH_PATH", "./kill_switch.state")
    try:
        with open(flag_path) as f:
            return {"state": f.read().strip() or "RUN"}
    except FileNotFoundError:
        return {"state": "RUN"}
