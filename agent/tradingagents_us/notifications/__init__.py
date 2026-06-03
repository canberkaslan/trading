"""Push notification layer — Expo Push (dev) -> SNS/APNs/FCM (prod, Phase 7+).

Expo Push is fine for our scale (single user / dev). Send a JSON payload
to https://exp.host/--/api/v2/push/send; Expo routes it to APNs/FCM. No
Apple Developer account or certs needed until we're shipping to App Store.

Backend persists device tokens (`device_tokens` table). Fire-and-forget
async send — never block a request waiting for the push service.
"""

from .sender import (
    notify_decision_pending,
    notify_order_filled,
    notify_order_rejected,
    notify_order_submitted,
    send_expo_push,
)

__all__ = [
    "notify_decision_pending",
    "notify_order_filled",
    "notify_order_rejected",
    "notify_order_submitted",
    "send_expo_push",
]
