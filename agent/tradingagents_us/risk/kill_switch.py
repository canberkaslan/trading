"""Remote kill switch.

State lives in DynamoDB (or Redis in dev). Mobile app writes; agent reads at
poll interval (default 5s).

States:
- RUN: normal operation
- PAUSE_NEW: no new entries; manage existing (honor stops)
- FLATTEN_ALL: immediate market-exit of all positions
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Literal

KillSwitchState = Literal["RUN", "PAUSE_NEW", "FLATTEN_ALL"]


class KillSwitchReader(ABC):
    """Backend-agnostic reader. Cached for poll_interval_seconds."""

    @abstractmethod
    def read(self) -> KillSwitchState: ...


class CachedKillSwitchReader(KillSwitchReader):
    """Caches the underlying read for `poll_interval_seconds` to avoid hammering."""

    def __init__(self, underlying: KillSwitchReader, poll_interval_seconds: float = 5.0) -> None:
        self.underlying = underlying
        self.poll_interval = poll_interval_seconds
        self._last_read: float = 0.0
        self._last_value: KillSwitchState = "RUN"

    def read(self) -> KillSwitchState:
        now = time.monotonic()
        if now - self._last_read > self.poll_interval:
            self._last_value = self.underlying.read()
            self._last_read = now
        return self._last_value


class StaticKillSwitchReader(KillSwitchReader):
    """For tests."""

    def __init__(self, state: KillSwitchState = "RUN") -> None:
        self.state = state

    def read(self) -> KillSwitchState:
        return self.state


class DynamoDBKillSwitchReader(KillSwitchReader):
    """Reads from DynamoDB table `kill_switches`, item key `{user_id}`."""

    def __init__(self, table_name: str, user_id: str) -> None:
        # Lazy import — boto3 is heavy
        import boto3

        self.client = boto3.resource("dynamodb").Table(table_name)
        self.user_id = user_id

    def read(self) -> KillSwitchState:
        try:
            resp = self.client.get_item(Key={"user_id": self.user_id})
        except Exception:
            # Fail closed: if we can't reach DynamoDB, stop opening new positions
            return "PAUSE_NEW"
        item = resp.get("Item")
        if not item:
            return "RUN"
        state = item.get("state", "RUN")
        if state not in ("RUN", "PAUSE_NEW", "FLATTEN_ALL"):
            return "PAUSE_NEW"
        return state  # type: ignore[return-value]
