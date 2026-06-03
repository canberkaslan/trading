"""/v1/notifications — push token registration + test send."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from tradingagents_us.notifications import send_expo_push
from tradingagents_us.notifications.sender import PushMessage
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.device_tokens import (
    list_all_tokens,
    upsert_token,
)

from ..deps import get_repo, require_token

router = APIRouter()


class RegisterTokenBody(BaseModel):
    token: str
    platform: Literal["ios", "android", "web"]


@router.post("/register")
async def register_token(
    body: RegisterTokenBody,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> dict:
    """Mobile calls this after expo-notifications gives it an ExponentPushToken."""
    with repo.session() as s:
        upsert_token(
            s, token=body.token, user_id=user, platform=body.platform,
            ts=datetime.now(timezone.utc),
        )
    return {"status": "registered", "token": body.token[:24] + "…"}


@router.post("/test")
async def test_notification(
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> dict:
    """Send a no-op push to every registered device for the user.

    Useful from the mobile Settings screen to confirm wiring without
    waiting for a real trade event."""
    with repo.session() as s:
        tokens = list_all_tokens(s)
    if not tokens:
        return {"status": "no_devices_registered", "sent": 0}
    resp = send_expo_push([
        PushMessage(
            to=t, title="Trading", body="Push wiring works ✓",
            data={"type": "test"},
        )
        for t in tokens
    ])
    return {"status": "ok", "sent": len(tokens), "expo_response": resp}
