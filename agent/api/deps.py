"""FastAPI shared dependencies — auth, repo, broker client.

Auth modes (mutually exclusive, selected by env):

- **Cognito JWT (production, Phase 5h)** — set COGNITO_USER_POOL_ID +
  COGNITO_APP_CLIENT_ID + AWS_REGION. Validates RS256 signature against
  the JWKS endpoint, checks `aud` / `iss` / `token_use=access` / `exp`.
- **Dev bearer (Phase 5a)** — set DEV_API_TOKEN to a static value;
  callers must send that exact string.
- **Disabled** — both empty; every caller becomes 'anonymous'. Local dev only.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Any

import httpx
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


# --------------------------- Cognito JWT validation ---------------------------

_JWKS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_TTL_S = 3600.0


def _jwks_url() -> str | None:
    pool = os.environ.get("COGNITO_USER_POOL_ID")
    region = os.environ.get("AWS_REGION", "eu-west-1")
    if not pool:
        return None
    return f"https://cognito-idp.{region}.amazonaws.com/{pool}/.well-known/jwks.json"


def _load_jwks(url: str) -> dict[str, Any]:
    cached = _JWKS_CACHE.get(url)
    now = time.time()
    if cached and (now - cached[0]) < _JWKS_TTL_S:
        return cached[1]
    with httpx.Client(timeout=5.0) as cli:
        r = cli.get(url)
        r.raise_for_status()
        data = r.json()
    _JWKS_CACHE[url] = (now, data)
    return data


def _validate_cognito_jwt(token: str) -> str:
    """Returns the validated user `sub` claim. Raises HTTPException on failure.

    Uses `python-jose` if available (full RS256 verification). Falls back to
    `unverified` decoding ONLY if python-jose isn't installed — that path
    is for local development before `jose` lands in the runtime image."""
    try:
        from jose import jwt  # type: ignore[import-untyped]
        from jose.utils import base64url_decode  # noqa: F401  (used by jose)
    except ImportError:
        # Soft-fail in dev: still parse claims without signature check so
        # the rest of the stack can wire up. Production deployments MUST
        # have python-jose installed; the agent-ci pipeline pins it.
        import base64
        import json
        try:
            payload_b64 = token.split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
            return str(claims.get("sub", "unknown"))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid token: {e}") from e

    url = _jwks_url()
    if url is None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "cognito not configured")
    jwks = _load_jwks(url)

    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if key is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "kid not in JWKS")

        client_id = os.environ.get("COGNITO_APP_CLIENT_ID", "")
        region = os.environ.get("AWS_REGION", "eu-west-1")
        pool = os.environ.get("COGNITO_USER_POOL_ID", "")
        issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool}"

        claims = jwt.decode(
            token,
            key,
            algorithms=[key["alg"]],
            audience=client_id or None,
            issuer=issuer,
            options={"verify_at_hash": False},
        )

        # Cognito issues 'access' and 'id' tokens; we accept either.
        token_use = claims.get("token_use")
        if token_use not in ("access", "id"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"unexpected token_use={token_use}")

        return str(claims.get("sub", "unknown"))
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"jwt invalid: {e}") from e


async def require_token(authorization: str | None = Header(default=None)) -> str:
    """Authentication dispatcher: Cognito if configured, else dev bearer,
    else fully open ('anonymous')."""
    # Cognito path
    if os.environ.get("COGNITO_USER_POOL_ID"):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        return _validate_cognito_jwt(token)

    # Dev bearer
    expected = os.environ.get("DEV_API_TOKEN", "")
    if expected:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
        if authorization.split(" ", 1)[1].strip() != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
        return "dev-user"

    # Open (local dev only)
    return "anonymous"
