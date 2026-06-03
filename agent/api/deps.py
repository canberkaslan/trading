"""FastAPI shared dependencies — auth, repo, broker client.

Dev-mode bearer auth (Phase 5a): single static token in DEV_API_TOKEN env
var, no Cognito yet. Mobile sends `Authorization: Bearer <token>`.
Phase 5h replaces this with Cognito JWT validation.
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import Header, HTTPException, status
from sqlalchemy import create_engine

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.storage import TradeLogRepository


@lru_cache(maxsize=1)
def get_repo() -> TradeLogRepository:
    """Process-wide repository singleton, sqlite by default."""
    url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
    return TradeLogRepository(engine=create_engine(url, future=True))


def get_alpaca() -> AlpacaClient:
    """Per-request Alpaca client. Caller is responsible for closing it
    (FastAPI uses it within one request and discards)."""
    return AlpacaClient()


async def require_token(authorization: str | None = Header(default=None)) -> str:
    """Dev-mode bearer auth. Disabled if DEV_API_TOKEN is empty."""
    expected = os.environ.get("DEV_API_TOKEN", "")
    if not expected:
        return "anonymous"  # auth disabled in pure-local dev

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    return "dev-user"
