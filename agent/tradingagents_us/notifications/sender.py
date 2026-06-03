"""Expo Push API sender — fire and forget.

Per Expo's HTTP/2 push docs, batch sends up to 100 messages per call. We
keep it simple: a single POST per event. Async via httpx so calls don't
block the request thread.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


@dataclass(frozen=True)
class PushMessage:
    to: str                          # ExponentPushToken[xxxxxx]
    title: str
    body: str
    data: dict[str, Any] | None = None
    sound: str | None = "default"
    channel_id: str | None = None    # Android notification channel
    badge: int | None = None
    priority: str = "high"           # 'default' | 'normal' | 'high'

    def as_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "to": self.to, "title": self.title, "body": self.body,
            "sound": self.sound, "priority": self.priority,
        }
        if self.data is not None:
            out["data"] = self.data
        if self.channel_id is not None:
            out["channelId"] = self.channel_id
        if self.badge is not None:
            out["badge"] = self.badge
        return out


def send_expo_push(messages: list[PushMessage], timeout_s: float = 5.0) -> dict[str, Any]:
    """Synchronously send a batch of push messages. Returns the Expo response.

    Caller decides whether to call this on a background thread/task. We keep
    it sync so it composes with sync FastAPI endpoints; callers using BG
    workers wrap accordingly.
    """
    if not messages:
        return {"data": []}

    if os.environ.get("PUSH_DISABLED") == "1":
        log.info("push disabled via PUSH_DISABLED=1 (%d messages dropped)", len(messages))
        return {"data": [], "disabled": True}

    payload = [m.as_payload() for m in messages]
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/json",
    }
    # If the user has a paid EAS Push receipt access token, use it (higher
    # rate limits, receipts). Optional.
    if access_token := os.environ.get("EXPO_PUSH_ACCESS_TOKEN"):
        headers["Authorization"] = f"Bearer {access_token}"

    try:
        with httpx.Client(timeout=timeout_s) as cli:
            r = cli.post(EXPO_PUSH_URL, json=payload, headers=headers)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        log.warning("expo push failed: %s", e)
        return {"data": [], "error": str(e)}


# ----------------------------- semantic helpers ---------------------------


def notify_decision_pending(tokens: list[str], ticker: str, rating: str, order_id: str) -> None:
    """A new decision has produced a pending order awaiting approval."""
    if not tokens:
        return
    messages = [
        PushMessage(
            to=t,
            title=f"{ticker}: {rating}",
            body=f"Pending order ready for approval",
            data={"type": "decision_pending", "ticker": ticker, "order_id": order_id},
        )
        for t in tokens
    ]
    send_expo_push(messages)


def notify_order_submitted(tokens: list[str], ticker: str, side: str, qty: int,
                            broker_order_id: str) -> None:
    """Order successfully submitted to broker."""
    if not tokens:
        return
    messages = [
        PushMessage(
            to=t,
            title=f"Order submitted: {side} {qty} {ticker}",
            body=f"Broker ID: {broker_order_id[:12]}…",
            data={"type": "order_submitted", "ticker": ticker,
                  "broker_order_id": broker_order_id},
        )
        for t in tokens
    ]
    send_expo_push(messages)


def notify_order_filled(tokens: list[str], ticker: str, side: str, qty: int,
                         avg_price: float, broker_order_id: str) -> None:
    """Bracket parent (or any order) filled at the broker."""
    if not tokens:
        return
    messages = [
        PushMessage(
            to=t,
            title=f"Filled: {side} {qty} {ticker} @ ${avg_price:.2f}",
            body=f"Broker ID: {broker_order_id[:12]}…",
            data={"type": "order_filled", "ticker": ticker,
                  "broker_order_id": broker_order_id, "avg_price": avg_price},
        )
        for t in tokens
    ]
    send_expo_push(messages)


def notify_order_rejected(tokens: list[str], ticker: str, reasons: list[str]) -> None:
    """Guards / risk layer / broker rejected the order."""
    if not tokens:
        return
    body = "; ".join(reasons)[:200]  # iOS truncates around this anyway
    messages = [
        PushMessage(
            to=t,
            title=f"Refused: {ticker}",
            body=body,
            data={"type": "order_rejected", "ticker": ticker, "reasons": reasons},
        )
        for t in tokens
    ]
    send_expo_push(messages)
