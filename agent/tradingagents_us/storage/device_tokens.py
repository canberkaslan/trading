"""Device-token table for push notification delivery.

One row per (user_id, token) — Expo Push tokens are stable but can rotate
when the app is uninstalled and reinstalled. Insert-or-update via merge().
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, select
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class DeviceTokenRow(Base):
    __tablename__ = "device_tokens"

    token: Mapped[str] = mapped_column(String(256), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    platform: Mapped[str] = mapped_column(String(16))   # "ios" | "android" | "web"
    registered_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def upsert_token(session, *, token: str, user_id: str, platform: str, ts: datetime) -> None:
    session.merge(DeviceTokenRow(
        token=token, user_id=user_id, platform=platform, registered_at_utc=ts,
    ))


def list_tokens_for(session, user_id: str) -> list[str]:
    rows = session.execute(
        select(DeviceTokenRow).where(DeviceTokenRow.user_id == user_id)
    ).scalars().all()
    return [r.token for r in rows]


def list_all_tokens(session) -> list[str]:
    """Single-user dev mode: return every registered device."""
    rows = session.execute(select(DeviceTokenRow)).scalars().all()
    return [r.token for r in rows]
