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
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from tradingagents_us.log_redaction import install as install_log_redaction

from .deps import get_alpaca, get_repo, is_admin, require_token
from .routes import (
    agents,
    analyze,
    diagnostics,
    learn,
    market,
    notifications,
    orders,
    portfolio,
    prices,
    risk,
    tickers,
    trades,
)
from .routes import (
    eval as eval_routes,
)

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
app.include_router(learn.router, prefix="/v1/learn", tags=["learn"])
app.include_router(eval_routes.router, prefix="/v1/eval", tags=["eval"])
app.include_router(notifications.router, prefix="/v1/notifications", tags=["notifications"])
app.include_router(trades.router, prefix="/v1/trades", tags=["trades"])
app.include_router(diagnostics.router, prefix="/v1/diagnostics", tags=["diagnostics"])
app.include_router(risk.router, prefix="/v1/risk", tags=["risk"])
app.include_router(tickers.router, prefix="/v1/tickers", tags=["tickers"])
app.include_router(market.router, prefix="/v1/market", tags=["market"])


# Same reason as the scripts: an outbound call that carries its key in the
# query string must not land in the service log.
install_log_redaction()

_STATIC = Path(__file__).resolve().parent / "static"


@app.get("/v1/me", include_in_schema=True, tags=["auth"])
async def whoami(user: str = Depends(require_token)) -> dict[str, object]:
    """Who the caller is and what they may do.

    The app needs this to stop drawing controls that will 403. Showing a kill
    switch to someone who cannot throw it is the same failure this codebase
    already fixed once — a control whose state or effect is unknown invites a
    tap to find out, and the answer arrives as an error after the decision has
    been made.

    It returns the caller's OWN privilege only, never the list. A refusal must
    not enumerate the privileged accounts, and neither must a success.
    """
    return {"uid": user, "is_admin": is_admin(user)}


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """The bare hostname is what someone types from memory, so it has to land
    somewhere. It answered 404 — the app was at /app and nothing said so."""
    return RedirectResponse(url="/app", status_code=307)


@app.get("/dashboard", include_in_schema=False)
async def dashboard() -> RedirectResponse:
    """Superseded by /app — the Modernist screens the phone runs.

    This URL is in the operator's muscle memory and their browser history, so
    it kept reopening the old ops panel and reading as "the redesign did not
    ship". The two are not two views of the same thing: /app is the product,
    this was the stopgap. A redirect is therefore the honest answer rather than
    a banner pointing elsewhere.

    307 rather than 301: a permanent redirect is cached by the browser
    indefinitely, and this one should stay reversible while /app is new.
    """
    return RedirectResponse(url="/app", status_code=307)


@app.get("/dashboard-legacy", include_in_schema=False)
async def dashboard_legacy() -> FileResponse:
    """Web dashboard. The HTML itself is public (no data in it); every data
    call it makes goes through the bearer-token API. Token is entered once in
    the page and kept in localStorage — never embedded here."""
    return FileResponse(
        _STATIC / "dashboard.html",
        media_type="text/html",
        # Always revalidate: browsers heuristically cached the old page and
        # users kept seeing stale designs after deploys.
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


# The Expo web build (the Modernist RN screens compiled with react-native-web).
# Deployed as a directory of static files rather than vendored into the repo —
# it is ~4 MB of hashed build output that changes on every UI edit.
#
# `baseUrl: '/app'` is set in the app's expo config, so every asset the bundle
# requests is already prefixed with /app; serving it anywhere else would 404 on
# the JS entry point.
_WEBAPP = Path(os.environ.get("WEBAPP_DIR", "/opt/ai-trader/webapp"))


def _webapp_file(rel: str) -> Path | None:
    """Resolve `rel` under the web build, or None if it escapes or is missing.

    The resolve()/is_relative_to() pair is the guard: without it a request for
    `/app/../../etc/passwd` would be read straight off disk. Symlinks resolve
    first, so a link planted inside the build cannot point out of it either.
    """
    if not _WEBAPP.is_dir():
        return None
    try:
        target = (_WEBAPP / rel).resolve()
    except (OSError, RuntimeError):
        return None
    root = _WEBAPP.resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


@app.get("/app", include_in_schema=False)
@app.get("/app/{path:path}", include_in_schema=False)
async def webapp(path: str = "") -> FileResponse:
    """Serve the Expo web build, falling back to index.html for client routes.

    expo-router does its own routing in the browser, so /app/portfolio is not a
    file — it is a route the bundle resolves after it boots. Anything that is
    not a real file therefore has to return index.html rather than a 404, or a
    reload on any screen but the first would break.

    The HTML shell is public for the same reason /dashboard is: it contains no
    data. Every figure it shows arrives over the bearer-gated /v1 API, and the
    bearer is typed into the app and kept per-device, never built into the
    bundle.
    """
    if not _WEBAPP.is_dir():
        raise HTTPException(
            status_code=503,
            detail="web build not deployed — run scripts/deploy_webapp.sh",
        )

    target = _webapp_file(path) if path else None
    if target is not None:
        # Bundle filenames carry a content hash, so they are safe to cache hard.
        # index.html must not be: it is what names the current hash.
        immutable = "/_expo/static/" in f"/{path}"
        return FileResponse(
            target,
            headers={
                "Cache-Control": "public, max-age=31536000, immutable"
                if immutable
                else "no-store, must-revalidate"
            },
        )

    index = _WEBAPP / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="web build incomplete: no index.html")
    return FileResponse(
        index,
        media_type="text/html",
        headers={"Cache-Control": "no-store, must-revalidate"},
    )


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
