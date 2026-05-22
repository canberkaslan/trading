"""/v1/orders — trade approval, submission, status."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from tradingagents_tr.schemas import OrderUpdate, TradeOrder

router = APIRouter()


@router.post("/{order_id}/approve", response_model=OrderUpdate)
async def approve_order(order_id: str) -> OrderUpdate:
    """Manual mode: user approves a pending trade in the mobile UI."""
    raise HTTPException(501, "phase 4 stub")


@router.post("/{order_id}/reject", response_model=OrderUpdate)
async def reject_order(order_id: str) -> OrderUpdate:
    raise HTTPException(501, "phase 4 stub")


@router.get("/", response_model=list[TradeOrder])
async def list_orders(status: str | None = None) -> list[TradeOrder]:
    raise HTTPException(501, "phase 4 stub")


@router.post("/kill-switch")
async def set_kill_switch(state: str) -> dict[str, str]:
    """Mobile-controlled remote kill switch. See ADR-005."""
    if state not in {"RUN", "PAUSE_NEW", "FLATTEN_ALL"}:
        raise HTTPException(400, f"invalid state: {state}")
    raise HTTPException(501, "phase 4 stub")
