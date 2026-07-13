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

import os

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .deps import get_alpaca, get_repo
from .routes import agents, analyze, eval, notifications, orders, portfolio, prices

app = FastAPI(
    title="Trading API",
    version="0.1.0",
    description="AI-powered multi-agent trading system — mobile backend",
)

# CORS for Expo dev / web preview. Native mobile (fetch) and server-side
# clients (curl) are unaffected by CORS — it only gates browser origins — so
# tightening the allowlist costs nothing for the app while closing the
# wildcard. Override on the box with CORS_ALLOW_ORIGINS (comma-separated).
_DEFAULT_CORS_ORIGINS = "http://localhost:8081,http://localhost:19006,http://localhost:19000"
_cors_origins = [
    o.strip()
    for o in os.environ.get("CORS_ALLOW_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(portfolio.router, prefix="/v1/portfolio", tags=["portfolio"])
app.include_router(orders.router, prefix="/v1/orders", tags=["orders"])
app.include_router(agents.router, prefix="/v1/agents", tags=["agents"])
app.include_router(analyze.router, prefix="/v1/analyze", tags=["analyze"])
app.include_router(prices.router, prefix="/v1/prices", tags=["prices"])
app.include_router(eval.router, prefix="/v1/eval", tags=["eval"])
app.include_router(notifications.router, prefix="/v1/notifications", tags=["notifications"])


def _trading_mode() -> str:
    """'paper' unless ALPACA_BASE_URL points at the live endpoint. Mirrors
    AlpacaClient's routing default so the badge can never disagree with
    where orders actually go."""
    base = os.environ.get("ALPACA_BASE_URL", "")
    return "paper" if (not base or "paper" in base) else "live"


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "trading_mode": _trading_mode()}


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
            "alpaca": alpaca_ok, "db": db_ok, "trading_mode": _trading_mode()}
