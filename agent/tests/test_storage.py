"""TradeLogRepository tests against an in-memory SQLite DB."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine

from tradingagents_us.schemas import AgentDecision, AgentReasoning, OrderUpdate, TradeOrder
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.repository import row_to_decision


@pytest.fixture
def repo() -> TradeLogRepository:
    engine = create_engine("sqlite://", future=True)
    return TradeLogRepository(engine=engine)


def _decision(dec_id: str = "dec-1", ticker: str = "AAPL") -> AgentDecision:
    return AgentDecision(
        ticker=ticker, market="US", quote_currency="USD",
        rating="Overweight",
        entry_price=271.0, stop_loss=229.0, price_target=310.0,
        time_horizon="12-18 months", suggested_size_pct=0.045,
        reasoning=[
            AgentReasoning(agent="pm", model="claude-opus-4-7", summary="x",
                           tokens_in=0, tokens_out=0, latency_ms=0),
        ],
        final_decision_text="**Rating**: Overweight",
        timestamp_utc=datetime.now(timezone.utc),
        decision_id=dec_id,
    )


def _order(order_id: str, decision_id: str = "dec-1") -> TradeOrder:
    return TradeOrder(
        order_id=order_id, decision_id=decision_id,
        ticker="AAPL", market="US", side="BUY", quantity=29, order_type="MARKET",
        limit_price=None, stop_loss=229.0, risk_approved=True,
        rejection_reasons=[], submitted_at_utc=datetime.now(timezone.utc),
    )


def test_save_and_load_decision(repo: TradeLogRepository) -> None:
    d = _decision()
    repo.save_decision(d)
    loaded = repo.get_decision("dec-1")
    assert loaded is not None
    converted = row_to_decision(loaded)
    assert converted.rating == "Overweight"
    assert converted.price_target == 310.0
    assert converted.reasoning[0].agent == "pm"


def test_save_decision_is_idempotent(repo: TradeLogRepository) -> None:
    repo.save_decision(_decision())
    repo.save_decision(_decision())  # merge, no duplicate
    assert len(repo.list_recent_decisions()) == 1


def test_decision_then_order_then_updates(repo: TradeLogRepository) -> None:
    repo.save_decision(_decision())
    repo.save_order(_order("ord-1"), broker_order_id="alpaca-xyz")
    repo.append_update(OrderUpdate(
        order_id="ord-1", status="PENDING",
        timestamp_utc=datetime.now(timezone.utc),
    ))
    repo.append_update(OrderUpdate(
        order_id="ord-1", status="FILLED", filled_qty=29, avg_fill_price=271.50,
        slippage_bps=2.0, timestamp_utc=datetime.now(timezone.utc),
    ))

    orders = repo.list_open_orders()
    assert len(orders) == 1
    assert orders[0].broker_order_id == "alpaca-xyz"


def test_list_filters_by_ticker(repo: TradeLogRepository) -> None:
    repo.save_decision(_decision("dec-aapl-1", "AAPL"))
    repo.save_decision(_decision("dec-nvda-1", "NVDA"))
    repo.save_decision(_decision("dec-aapl-2", "AAPL"))
    aapl_only = repo.list_recent_decisions(ticker="AAPL")
    assert {d.decision_id for d in aapl_only} == {"dec-aapl-1", "dec-aapl-2"}
