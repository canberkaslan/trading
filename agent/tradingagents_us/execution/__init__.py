"""Execution layer — TradeOrder -> broker.

The risk layer hands us a `TradeOrder` with `risk_approved=True` (or False
+ rejection_reasons). The executor's job:

1. Refuse if not risk_approved
2. Refuse if the broker reports trading_blocked
3. Submit with an idempotent client_order_id
4. Wait for status (PENDING → ACCEPTED) and surface an OrderUpdate

Paper vs live is a single env-var (ALPACA_BASE_URL); the broker SDK has
no notion of "test mode" beyond the endpoint URL.
"""

from .executor import (
    ExecutionConfig,
    ExecutionResult,
    submit_order,
)

__all__ = ["ExecutionConfig", "ExecutionResult", "submit_order"]
