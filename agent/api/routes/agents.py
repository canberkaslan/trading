"""/v1/agents — agent decision history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from tradingagents_us.schemas import AgentDecision
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.repository import row_to_decision

from ..deps import get_repo, require_token

router = APIRouter()


@router.get("/decisions", response_model=list[AgentDecision])
async def list_decisions(
    ticker: str | None = None,
    limit: int = 50,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> list[AgentDecision]:
    rows = repo.list_recent_decisions(limit=limit, ticker=ticker)
    return [row_to_decision(r) for r in rows]


@router.get("/decisions/{decision_id}", response_model=AgentDecision)
async def get_decision(
    decision_id: str,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> AgentDecision:
    row = repo.get_decision(decision_id)
    if row is None:
        raise HTTPException(404, f"decision not found: {decision_id}")
    return row_to_decision(row)
