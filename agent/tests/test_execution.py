"""Unit tests for execution.submit_order.

Heavily mocked — we exercise the executor's control flow without touching
the broker. A live paper-submit smoke test lives in tests/test_execution_paper.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from tradingagents_us.execution.executor import (
    ExecutionConfig,
    derive_client_order_id,
    submit_order,
)
from tradingagents_us.schemas import AgentDecision, AgentReasoning, TradeOrder


def _decision_with_pt(price_target: float = 310.0) -> AgentDecision:
    return AgentDecision(
        ticker="AAPL", market="US", quote_currency="USD",
        rating="Overweight",
        entry_price=271.0, stop_loss=229.0, price_target=price_target,
        time_horizon="12-18 months", suggested_size_pct=0.045,
        reasoning=[AgentReasoning(agent="pm", model="claude-opus-4-7", summary="x",
                                  tokens_in=0, tokens_out=0, latency_ms=0)],
        timestamp_utc=datetime.now(timezone.utc),
        decision_id="dec-bracket-test",
    )


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

        cli.get_order_by_client_order_id.return_value = None  # no duplicate
        cli.close = MagicMock()
        return cli

    def test_successful_submission(self) -> None:
        cli = self._mock_client()
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert result.submitted
        assert result.broker_order_id == "broker-order-id-xyz"
        assert result.update.status == "ACCEPTED"
        # Idempotency: client_order_id derived from (ticker, date, side) —
        # stable across process restarts, unlike the old per-run-UUID hash.
        sent_kwargs = cli.submit_order.call_args.kwargs
        assert sent_kwargs["client_order_id"].startswith("tr-AAPL-")
        assert sent_kwargs["client_order_id"].endswith("-BUY")

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


class TestBracketOrder:
    def _mock_client_for_bracket(self):
        from unittest.mock import MagicMock
        cli = MagicMock()
        acct = MagicMock()
        acct.trading_blocked = False
        acct.pattern_day_trader = False
        cli.account.return_value = acct

        clock = MagicMock()
        clock.is_open = True
        cli.clock.return_value = clock

        broker = MagicMock()
        broker.id = "bracket-parent-id"
        broker.status = "accepted"
        broker.filled_qty = 0
        broker.filled_avg_price = None
        cli.submit_order.return_value = broker
        cli.get_order_by_client_order_id.return_value = None  # no duplicate
        cli.close = MagicMock()
        return cli

    def test_bracket_attaches_tp_and_sl_when_decision_has_pt(self) -> None:
        cli = self._mock_client_for_bracket()
        result = submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, use_bracket=True),
            decision=_decision_with_pt(price_target=310.0),
        )
        assert result.submitted
        sent = cli.submit_order.call_args.kwargs
        assert sent["take_profit_price"] == 310.0
        assert sent["stop_loss_price"] == 229.0

    def test_no_bracket_when_use_bracket_off(self) -> None:
        cli = self._mock_client_for_bracket()
        submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, use_bracket=False),
            decision=_decision_with_pt(),
        )
        sent = cli.submit_order.call_args.kwargs
        assert "take_profit_price" not in sent
        assert "stop_loss_price" not in sent

    def test_stop_still_attaches_without_decision(self) -> None:
        # No decision -> no TP, but the protective stop leg must STILL
        # attach (OTO). The old `if tp and sl` dropped both together.
        cli = self._mock_client_for_bracket()
        submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, use_bracket=True),
            decision=None,
        )
        sent = cli.submit_order.call_args.kwargs
        assert "take_profit_price" not in sent
        assert sent["stop_loss_price"] == 229.0

    def test_stop_still_attaches_without_price_target(self) -> None:
        # Decision present but the PM produced no PT -> stop-only OTO.
        cli = self._mock_client_for_bracket()
        decision = _decision_with_pt().model_copy(update={"price_target": None})
        submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, use_bracket=True, tp_headroom=0),
            decision=decision,
        )
        sent = cli.submit_order.call_args.kwargs
        assert "take_profit_price" not in sent
        assert sent["stop_loss_price"] == 229.0

    def test_explicit_tp_override(self) -> None:
        cli = self._mock_client_for_bracket()
        submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, use_bracket=True, take_profit_price=299.5),
            decision=_decision_with_pt(price_target=310.0),  # should be overridden
        )
        sent = cli.submit_order.call_args.kwargs
        assert sent["take_profit_price"] == 299.5


class TestStaleAndEntryGuards:
    """Phase 4g — guards against replaying stale decisions and bad entries."""

    def test_stale_decision_rejected(self) -> None:
        decision = _decision_with_pt()
        # Force the decision to be 48h old (default max age is 24h)
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        decision = decision.model_copy(update={"timestamp_utc": old})

        # Dry-run so we don't need the broker. Stale check runs before dry-run path.
        result = submit_order(_order(), decision=decision, config=ExecutionConfig(dry_run=True))
        assert not result.submitted
        assert result.update.status == "REJECTED"
        assert any("stale_decision" in r for r in (result.refusal_reasons or []))

    def test_fresh_decision_passes(self) -> None:
        decision = _decision_with_pt()  # timestamp=now
        result = submit_order(_order(), decision=decision, config=ExecutionConfig(dry_run=True))
        # Dry-run path -> PENDING (not rejected by staleness)
        assert result.dry_run
        assert result.update.status == "PENDING"

    def test_no_tp_headroom_rejected(self) -> None:
        # PT=$310, current=$300 -> ratio 300/310 = 0.968, threshold (1-0.05)=0.95
        # 0.968 >= 0.95 -> no headroom, REJECTED
        decision = _decision_with_pt(price_target=310.0)
        result = submit_order(
            _order(), decision=decision, current_price=300.0,
            config=ExecutionConfig(dry_run=True, tp_headroom=0.05),
        )
        assert not result.submitted
        assert any("no_tp_headroom" in r for r in (result.refusal_reasons or []))

    def test_enough_tp_headroom_passes(self) -> None:
        # PT=$310, current=$280 -> ratio 0.903 < 0.95 -> plenty of headroom
        decision = _decision_with_pt(price_target=310.0)
        result = submit_order(
            _order(), decision=decision, current_price=280.0,
            config=ExecutionConfig(dry_run=True, tp_headroom=0.05),
        )
        assert result.dry_run and result.update.status == "PENDING"

    def test_too_close_to_stop_rejected(self) -> None:
        # stop=$229, buffer=2% -> need current > $229 * 1.02 = $233.58
        # current=$232 -> too close
        decision = _decision_with_pt()
        result = submit_order(
            _order(), decision=decision, current_price=232.0,
            config=ExecutionConfig(dry_run=True, stop_buffer=0.02),
        )
        assert not result.submitted
        assert any("too_close_to_stop" in r for r in (result.refusal_reasons or []))

    def test_guards_disabled_when_zero(self) -> None:
        decision = _decision_with_pt().model_copy(
            update={"timestamp_utc": datetime.now(timezone.utc) - timedelta(days=30)}
        )
        result = submit_order(
            _order(), decision=decision, current_price=309.0,  # would normally trip headroom
            config=ExecutionConfig(dry_run=True, decision_max_age_hours=0,
                                   tp_headroom=0, stop_buffer=0),
        )
        assert result.dry_run and result.update.status == "PENDING"

    def test_guards_skipped_without_current_price(self) -> None:
        # No current_price provided -> only stale check runs
        decision = _decision_with_pt()
        result = submit_order(
            _order(), decision=decision, current_price=None,
            config=ExecutionConfig(dry_run=True, tp_headroom=0.05, stop_buffer=0.02),
        )
        assert result.dry_run and result.update.status == "PENDING"


class TestErrorFlag:
    """`ExecutionResult.error` must be True ONLY for operational failures
    (broker/API error, unexpected exception) — never for a policy refusal.
    The daily-run wrapper keys its exit code off this: a legitimate "no trade"
    outcome (Hold, risk guard, PDT, market closed) must not fail the run.
    """

    def _mock_client(self):
        cli = MagicMock()
        acct = MagicMock()
        acct.trading_blocked = False
        acct.pattern_day_trader = False
        acct.status = "ACTIVE"
        cli.account.return_value = acct
        clock = MagicMock()
        clock.is_open = True
        clock.next_open = "2026-05-23T13:30:00Z"
        cli.clock.return_value = clock
        cli.get_order_by_client_order_id.return_value = None  # no duplicate
        cli.close = MagicMock()
        return cli

    def test_policy_refusal_unapproved_is_not_error(self) -> None:
        result = submit_order(_order(approved=False, reasons=["non-actionable rating=Hold"]))
        assert not result.submitted
        assert result.error is False

    def test_policy_refusal_pdt_is_not_error(self) -> None:
        cli = self._mock_client()
        cli.account.return_value.pattern_day_trader = True
        result = submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, refuse_on_pdt=True),
        )
        assert not result.submitted
        assert result.error is False

    def test_broker_http_error_is_error(self) -> None:
        import httpx

        cli = self._mock_client()
        cli.submit_order.side_effect = httpx.HTTPError("boom")
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert not result.submitted
        assert result.error is True
        assert any("broker_error" in r for r in (result.refusal_reasons or []))

    def test_unexpected_exception_is_error(self) -> None:
        cli = self._mock_client()
        cli.submit_order.side_effect = RuntimeError("kaboom")
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert not result.submitted
        assert result.error is True

    def test_successful_submission_is_not_error(self) -> None:
        cli = self._mock_client()
        broker_order = MagicMock()
        broker_order.id = "oid"
        broker_order.status = "accepted"
        broker_order.filled_qty = 0
        broker_order.filled_avg_price = None
        cli.submit_order.return_value = broker_order
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert result.submitted
        assert result.error is False


class TestIdempotencyKey:
    def test_client_order_id_is_deterministic(self) -> None:
        from datetime import date

        a = derive_client_order_id("aapl", date(2026, 7, 11), "buy")
        b = derive_client_order_id("AAPL", date(2026, 7, 11), "BUY")
        assert a == b == "tr-AAPL-20260711-BUY"
        assert len(a) <= 48  # Alpaca client_order_id cap

    def test_different_day_different_key(self) -> None:
        from datetime import date

        a = derive_client_order_id("AAPL", date(2026, 7, 11), "BUY")
        b = derive_client_order_id("AAPL", date(2026, 7, 14), "BUY")
        assert a != b


class TestBrokerStatusMapping:
    def _mock_client(self, broker_status: str):
        cli = MagicMock()
        acct = MagicMock()
        acct.trading_blocked = False
        acct.pattern_day_trader = False
        cli.account.return_value = acct
        broker = MagicMock()
        broker.id = "oid-1"
        broker.status = broker_status
        broker.filled_qty = 3
        broker.filled_avg_price = 271.5
        cli.submit_order.return_value = broker
        cli.get_order_by_client_order_id.return_value = None
        cli.close = MagicMock()
        return cli

    def test_partially_filled_maps_to_partial(self) -> None:
        # The old `.upper()` passthrough made this fail OrderUpdate
        # validation and read back as a REJECTED error.
        cli = self._mock_client("partially_filled")
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert result.submitted
        assert result.error is False
        assert result.update.status == "PARTIAL"
        assert result.update.filled_qty == 3

    def test_filled_maps_to_filled(self) -> None:
        cli = self._mock_client("filled")
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert result.update.status == "FILLED"

    def test_unknown_status_maps_to_needs_reconcile(self) -> None:
        cli = self._mock_client("held_for_review")  # not in the allowlist
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert result.submitted  # the order IS at the broker
        assert result.error is False
        assert result.update.status == "NEEDS_RECONCILE"
        assert "held_for_review" in (result.update.error_message or "")

    def test_stopped_and_suspended_are_live_not_rejected(self) -> None:
        # 'stopped' = fill guaranteed, 'suspended' = parked but live — a
        # REJECTED mapping would hide a position that WILL exist.
        for raw in ("stopped", "suspended"):
            cli = self._mock_client(raw)
            result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
            assert result.update.status == "NEEDS_RECONCILE", raw


class TestDuplicateSubmission:
    def _mock_client(self, existing_status: str | None):
        cli = MagicMock()
        acct = MagicMock()
        acct.trading_blocked = False
        acct.pattern_day_trader = False
        cli.account.return_value = acct
        if existing_status is None:
            cli.get_order_by_client_order_id.return_value = None
        else:
            existing = MagicMock()
            existing.id = "existing-broker-id"
            existing.status = existing_status
            existing.filled_qty = 10
            existing.filled_avg_price = 270.0
            cli.get_order_by_client_order_id.return_value = existing
        broker = MagicMock()
        broker.id = "fresh-broker-id"
        broker.status = "accepted"
        broker.filled_qty = 0
        broker.filled_avg_price = None
        cli.submit_order.return_value = broker
        cli.close = MagicMock()
        return cli

    def test_live_duplicate_refuses_without_welding(self) -> None:
        # Same (ticker, date, side) already live at the broker -> refuse the
        # NEW order; the old order's broker id / fill data must NOT be
        # written under this order_id (the original row already has them).
        cli = self._mock_client("filled")
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert not result.submitted
        assert result.error is False
        assert result.broker_order_id is None
        assert result.update.status == "REJECTED"
        assert result.update.filled_qty == 0
        assert "existing-broker-id" in (result.update.error_message or "")
        assert any("duplicate" in r for r in (result.refusal_reasons or []))
        cli.submit_order.assert_not_called()

    def test_cancelled_previous_attempt_resubmits_with_fresh_key(self) -> None:
        # Alpaca rejects any REUSED client_order_id (even for cancelled
        # orders) — the retry must carry a suffixed key, not the same one.
        cli = self._mock_client(None)
        cancelled = MagicMock()
        cancelled.id = "old-cancelled-id"
        cancelled.status = "canceled"
        cancelled.filled_qty = 0
        cancelled.filled_avg_price = None
        cli.get_order_by_client_order_id.side_effect = [cancelled, None]
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert result.submitted
        assert result.broker_order_id == "fresh-broker-id"
        sent_key = cli.submit_order.call_args.kwargs["client_order_id"]
        assert sent_key.endswith("-r2")

    def test_cancelled_after_partial_fill_needs_reconcile(self) -> None:
        # Resubmitting the full quantity would double-buy the filled part.
        cli = self._mock_client(None)
        part = MagicMock()
        part.id = "old-partial-id"
        part.status = "canceled"
        part.filled_qty = 4
        part.filled_avg_price = 270.0
        cli.get_order_by_client_order_id.side_effect = [part]
        result = submit_order(_order(), client=cli, config=ExecutionConfig(dry_run=False))
        assert not result.submitted
        assert result.update.status == "NEEDS_RECONCILE"
        cli.submit_order.assert_not_called()


class TestRunDateKey:
    def _mock_client(self):
        cli = MagicMock()
        acct = MagicMock()
        acct.trading_blocked = False
        acct.pattern_day_trader = False
        cli.account.return_value = acct
        broker = MagicMock()
        broker.id = "oid"
        broker.status = "accepted"
        broker.filled_qty = 0
        broker.filled_avg_price = None
        cli.submit_order.return_value = broker
        cli.get_order_by_client_order_id.return_value = None
        cli.close = MagicMock()
        return cli

    def test_trade_date_overrides_decision_timestamp(self) -> None:
        # Daily run starts 22:30 UTC; tail tickers finish AFTER midnight.
        # The key must stay on the RUN's date or tomorrow's legitimate
        # trade is silently blocked as a duplicate.
        from datetime import date

        cli = self._mock_client()
        decision = _decision_with_pt().model_copy(
            update={"timestamp_utc": datetime(2026, 7, 14, 0, 15, tzinfo=timezone.utc)}
        )
        submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, decision_max_age_hours=0,
                                   tp_headroom=0, stop_buffer=0),
            decision=decision,
            trade_date=date(2026, 7, 13),
        )
        sent_key = cli.submit_order.call_args.kwargs["client_order_id"]
        assert sent_key == "tr-AAPL-20260713-BUY"

    def test_bracket_legs_force_gtc(self) -> None:
        # Day-TIF bracket legs expire at the close of entry day, leaving the
        # position naked overnight — protective legs must be GTC.
        cli = self._mock_client()
        submit_order(
            _order(), client=cli,
            config=ExecutionConfig(dry_run=False, use_bracket=True),
            decision=_decision_with_pt(),
        )
        sent = cli.submit_order.call_args.kwargs
        assert "stop_loss_price" in sent
        assert sent["time_in_force"] == "gtc"

    def test_no_legs_stays_day(self) -> None:
        cli = self._mock_client()
        order = _order()
        order = order.model_copy(update={"stop_loss": 0.0})
        submit_order(order, client=cli, config=ExecutionConfig(dry_run=False, use_bracket=False))
        assert cli.submit_order.call_args.kwargs["time_in_force"] == "day"
