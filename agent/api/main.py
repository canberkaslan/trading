"""FastAPI entry point for the mobile backend.

Phase 4 work — fleshed out once paper trade infra is up.
"""

from __future__ import annotations

from fastapi import FastAPI

from .routes import agents, orders, portfolio

app = FastAPI(
    title="Trading API",
    version="0.1.0",
    description="AI-powered multi-agent trading system — mobile backend",
)

app.include_router(portfolio.router, prefix="/v1/portfolio", tags=["portfolio"])
app.include_router(orders.router, prefix="/v1/orders", tags=["orders"])
app.include_router(agents.router, prefix="/v1/agents", tags=["agents"])


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str]:
    # TODO: check Redis + Aurora + Anthropic API reachability
    return {"status": "ok"}
