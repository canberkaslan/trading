"""Auth dispatcher unit tests for api.deps.require_token."""

from __future__ import annotations

import base64
import json
import os

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("COGNITO_APP_CLIENT_ID", raising=False)
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)


async def _call(authorization: str | None = None) -> str:
    from api.deps import require_token
    return await require_token(authorization=authorization)


@pytest.mark.asyncio
async def test_disabled_when_no_env_vars() -> None:
    assert await _call(None) == "anonymous"


@pytest.mark.asyncio
async def test_dev_bearer_accepts_correct_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_API_TOKEN", "secret-abc")
    assert await _call("Bearer secret-abc") == "dev-user"


@pytest.mark.asyncio
async def test_dev_bearer_rejects_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_API_TOKEN", "secret-abc")
    with pytest.raises(HTTPException) as exc:
        await _call("Bearer wrong")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_dev_bearer_rejects_missing_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEV_API_TOKEN", "secret-abc")
    with pytest.raises(HTTPException) as exc:
        await _call(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_cognito_path_requires_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_dummy")
    with pytest.raises(HTTPException) as exc:
        await _call(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_cognito_path_unsigned_fallback_extracts_sub(monkeypatch: pytest.MonkeyPatch) -> None:
    """When python-jose is missing the fallback parses claims unverified —
    keeps local dev unblocked but never reaches production (CI pins jose)."""
    monkeypatch.setenv("COGNITO_USER_POOL_ID", "us-east-1_dummy")
    # Build a fake JWT: header.payload.signature
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "kid": "x"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "user-1234"}).encode()).rstrip(b"=")
    token = f"{header.decode()}.{payload.decode()}.fakesig"

    # Pre-emptively block python-jose import to exercise the fallback
    import sys
    monkeypatch.setitem(sys.modules, "jose", None)

    result = await _call(f"Bearer {token}")
    assert result == "user-1234"
