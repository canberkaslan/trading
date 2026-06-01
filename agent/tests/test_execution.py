"""Unit tests for execution.submit_order.

Heavily mocked — we exercise the executor's control flow without touching
the broker. A live paper-submit smoke test lives in tests/test_execution_paper.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from tradingagents_us.execution.executor import (
    ExecutionConfig,
    derive_client_order_id,
    submit_order,
)
from tradingagents_us.schemas import TradeOrder


def _order(approved: bool = True, reasons: list[str] | None = None) -> TradeOrder:
    return TradeOrder(
        order_id="ord-12345678-aaaa",
        decision_id="dec-87654321-bbbb",
        ticker="AAPL",
        market="US",
        side="BUY",
        quantity=10,
        order_type="MARKET",
        limit_price=None,
        stop_loss=229.0,
        risk_approved=approved,
        rejection_reasons=reasons or [],
        submitted_at_utc=datetime.now(timezone.utc),
    )


class TestRiskApprovalGate:
    def test_refuses_unapproved_order(self) -> None:
        result = submit_order(_order(approved=False, reasons=["test"]))
        assert not result.submitted
        assert result.update.status == "REJECTED"
        assert "test" in (result.refusal_reasons or [])


class TestDryRun:
    def test_dry_run_default_does_not_submit(self) -> None:
        # No client passed; if dry_run leaked through, AlpacaClient() would
        # be constructed and would either succeed or error — either way it
        # would mean we contacted the broker. We assert we did NOT.
        result = submit_order(_order(), client=None, config=ExecutionConfig(dry_run=True))
        assert not result.submitted
        assert result.dry_run
        assert result.update.status == "PENDING"


class TestLiveSubmissionMocked:
    def _mock_client(self, account_active: bool = True, pdt: bool = False, blocked: bool = False, market_open: bool = True):
        cli = MagicMock()
        acct = MagicMock()
        acct.trading_blocked = blocked
        acct.pattern_day_trader = pdt
        acct.status = "ACTIVE" if account_active else "INACTIVE"
        cli.account.return_value = acct

        clock = MagicMock()
        clock.is_open = market_open
        clock.next_open = "2026-05-23T13:30:00Z"
        cli.clock.return_value = clock

        broker_order = MagicMock()
        broker_order.id = "broker-order-id-xyz"
        broker_order.status = "accepted"
        broker_order.filled_qty = 0
        broker_order.filled_avg_price = None
        cli.submit_order.return_value = broker_order

        cli.close = MagicMock()
        return cli

    def test_successful_submission(self) -> None:
        cli = self._mock_client()
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert result.submitted
        assert result.broker_order_id == "broker-order-id-xyz"
        assert result.update.status == "ACCEPTED"
        # Idempotency: client_order_id derived deterministically from ids
        sent_kwargs = cli.submit_order.call_args.kwargs
        assert sent_kwargs["client_order_id"].startswith("tr-dec-8765-ord-1234".replace("dec-8765", "dec-8765"))

    def test_refuses_pdt_when_configured(self) -> None:
        cli = self._mock_client(pdt=True)
        result = submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, refuse_on_pdt=True),
        )
        assert not result.submitted
        assert any("pattern_day_trader" in r for r in (result.refusal_reasons or []))
        cli.submit_order.assert_not_called()

    def test_refuses_when_trading_blocked(self) -> None:
        cli = self._mock_client(blocked=True)
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert not result.submitted
        assert any("trading_blocked" in r for r in (result.refusal_reasons or []))

    def test_refuses_outside_hours_when_configured(self) -> None:
        cli = self._mock_client(market_open=False)
        result = submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, refuse_outside_hours=True),
        )
        assert not result.submitted
        assert any("market_closed" in r for r in (result.refusal_reasons or []))

    def test_allows_outside_hours_by_default(self) -> None:
        # Paper trades queue overnight — we default to allowing them
        cli = self._mock_client(market_open=False)
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert result.submitted


class TestIdempotencyKey:
    def test_client_order_id_is_deterministic(self) -> None:
        a = derive_client_order_id("decision-abc-12345", "order-xyz-67890")
        b = derive_client_order_id("decision-abc-12345", "order-xyz-67890")
        assert a == b
        assert a.startswith("tr-decision-")
