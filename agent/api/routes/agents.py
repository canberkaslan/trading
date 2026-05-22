"""/v1/agents — agent decision history + streaming reasoning."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from tradingagents_us.schemas import AgentDecision

router = APIRouter()


@router.get("/decisions", response_model=list[AgentDecision])
async def list_decisions(ticker: str | None = None, limit: int = 50) -> list[AgentDecision]:
    raise HTTPException(501, "phase 4 stub")


@router.get("/decisions/{decision_id}", response_model=AgentDecision)
async def get_decision(decision_id: str) -> AgentDecision:
    raise HTTPException(501, "phase 4 stub")
