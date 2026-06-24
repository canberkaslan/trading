"""FastAPI entry point for the mobile backend.

Run locally:
    cd agent
    set -a && source .env && set +a
    ./.venv/bin/uvicorn api.main:app --reload --port 8000

Endpoints:
    GET  /healthz                              — liveness
    GET  /readyz                               — Alpaca + DB reachability
    GET  /v1/portfolio/snapshot                — equity + positions (live Alpaca)
    GET  /v1/agents/decisions[?ticker&limit]   — recent decisions (DB)
    GET  /v1/agents/decisions/{id}             — single decision
    GET  /v1/orders                            — recent orders, DB + Alpaca status
    POST /v1/orders/{id}/cancel                — cancel at broker
    GET  /v1/orders/kill-switch                — current kill state
    POST /v1/orders/kill-switch                — set kill state (RUN | PAUSE_NEW | FLATTEN_ALL)
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .deps import get_alpaca, get_repo
from .routes import agents, analyze, eval, notifications, orders, portfolio, prices

app = FastAPI(
    title="Trading API",
    version="0.1.0",
    description="AI-powered multi-agent trading system — mobile backend",
)

# CORS for Expo dev / web preview. Restrict in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(portfolio.router, prefix="/v1/portfolio", tags=["portfolio"])
app.include_router(orders.router, prefix="/v1/orders", tags=["orders"])
app.include_router(agents.router, prefix="/v1/agents", tags=["agents"])
app.include_router(analyze.router, prefix="/v1/analyze", tags=["analyze"])
app.include_router(prices.router, prefix="/v1/prices", tags=["prices"])
app.include_router(eval.router, prefix="/v1/eval", tags=["eval"])
app.include_router(notifications.router, prefix="/v1/notifications", tags=["notifications"])


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, str | bool]:
    """Check Alpaca + DB reachability."""
    alpaca_ok = False
    db_ok = False
    try:
        cli = get_alpaca()
        cli.account()
        cli.close()
        alpaca_ok = True
    except Exception:
        alpaca_ok = False
    try:
        repo = get_repo()
        repo.list_recent_decisions(limit=1)
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if (alpaca_ok and db_ok) else "degraded",
            "alpaca": alpaca_ok, "db": db_ok}
