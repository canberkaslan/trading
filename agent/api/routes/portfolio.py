"""GET /v1/portfolio/* — read-only views of user portfolio state."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from tradingagents_tr.schemas import PortfolioSnapshot

router = APIRouter()


@router.get("/snapshot", response_model=PortfolioSnapshot)
async def get_snapshot() -> PortfolioSnapshot:
    """Current equity + positions for the authenticated user.

    TODO(phase-4):
        - Cognito JWT auth dependency
        - Read from Aurora trade_log + ElastiCache last-known quotes
    """
    raise HTTPException(501, "phase 4 stub")
