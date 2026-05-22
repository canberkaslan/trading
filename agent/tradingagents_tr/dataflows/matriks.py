"""Matriks IQ — BIST market data + execution adapter.

This is our primary BIST broker integration (Garanti BBVA Yatırım upstream).
Subscription-based; subscription is per terminal seat. C# SDK exists; we wrap
the REST + socket layer in Python.

Phase 1 stub.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Quote:
    ticker: str
    bid: float
    ask: float
    last: float
    volume: int
    timestamp_utc: datetime


@dataclass
class BISTOrder:
    ticker: str
    side: str  # "BUY" | "SELL"
    quantity: int
    order_type: str  # "MARKET" | "LIMIT"
    limit_price: float | None


class MatriksClient:
    """REST + socket client for Matriks IQ.

    TODO(phase-1):
        - Authenticate against Matriks REST
        - Open persistent socket for real-time quotes (publish to Redis)
        - Submit orders via order entry API (paper mode first)
        - Honor BIST lot rules (varies by ticker price band)
    """

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    def get_quote(self, ticker: str) -> Quote:
        raise NotImplementedError("matriks.MatriksClient.get_quote — phase 1 stub")

    def submit_order(self, order: BISTOrder) -> str:
        """Returns broker order_id on accept."""
        raise NotImplementedError("matriks.MatriksClient.submit_order — phase 1 stub")
