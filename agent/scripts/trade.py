"""End-to-end CLI: decide -> size -> (optional) Alpaca paper submit.

Single command for the daily flow:

    # Dry run (default): see what would happen, no broker call, no LLM cost
    python -m scripts.trade --ticker AAPL --use-cached

    # Real LLM call (~$0.50-1.50 cost, 5-10 min)
    python -m scripts.trade --ticker NVDA

    # Submit to Alpaca paper (after a fresh decision)
    python -m scripts.trade --ticker AAPL --use-cached --submit

The cached path replays the Phase 2 AAPL state — perfect for end-to-end
validation without spending LLM tokens.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Make package + vendor importable when running as a script
_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
if str(_AGENT_ROOT / "vendor" / "tradingagents") not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT / "vendor" / "tradingagents"))

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient  # noqa: E402
from tradingagents_us.dataflows.polygon import PolygonClient  # noqa: E402
from tradingagents_us.execution import ExecutionConfig, submit_order  # noqa: E402
from tradingagents_us.graph.pipeline import (  # noqa: E402
    _parse_pm_output,
    _parse_trader_output,
    propagate,
)
from tradingagents_us.risk.circuit_breaker import CircuitBreaker  # noqa: E402
from tradingagents_us.risk.kill_switch import StaticKillSwitchReader  # noqa: E402
from tradingagents_us.risk.portfolio_limits import PortfolioContext, PortfolioLimits  # noqa: E402
from tradingagents_us.risk.sizer import MarketContext, size_from_decision  # noqa: E402
from tradingagents_us.schemas import AgentDecision, AgentReasoning  # noqa: E402
from tradingagents_us.storage import TradeLogRepository  # noqa: E402

log = logging.getLogger("trade")


def _load_env() -> None:
    env_path = _AGENT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"')
        if v:
            os.environ.setdefault(k, v)


def _decision_from_cached(ticker: str) -> AgentDecision:
    """Load a previously-generated full_states_log_*.json from disk."""
    home = Path.home() / ".tradingagents" / "logs" / ticker / "TradingAgentsStrategy_logs"
    if not home.exists():
        raise FileNotFoundError(f"no cached state for {ticker} at {home}")
    files = sorted(home.glob("full_states_log_*.json"))
    if not files:
        raise FileNotFoundError(f"no full_states_log_*.json in {home}")
    state = json.loads(files[-1].read_text())

    pm = state.get("final_trade_decision", "") or ""
    trader = state.get("trader_investment_decision") or state.get("trader_investment_plan") or ""
    rating, pt, horizon = _parse_pm_output(pm)
    entry, stop, size = _parse_trader_output(trader)

    reasoning = [
        AgentReasoning(agent="portfolio_manager", model="claude-opus-4-7",
                       summary=pm[:1500], tokens_in=0, tokens_out=0, latency_ms=0),
    ]

    # Use the ORIGINAL trade_date from the cached state as the decision
    # timestamp — never "now". Otherwise the stale-decision guard is silently
    # bypassed for replays (which is exactly the bug that caused the
    # 2026-06-01 AAPL -$5.64 incident).
    trade_date = state.get("trade_date")
    if trade_date:
        try:
            decision_ts = datetime.fromisoformat(trade_date).replace(tzinfo=timezone.utc)
        except ValueError:
            decision_ts = datetime.now(timezone.utc)
    else:
        decision_ts = datetime.now(timezone.utc)

    return AgentDecision(
        ticker=ticker, market="US", quote_currency="USD",
        rating=rating, entry_price=entry, stop_loss=stop,
        suggested_size_pct=size, price_target=pt, time_horizon=horizon,
        reasoning=reasoning, final_decision_text=pm[:8000],
        timestamp_utc=decision_ts, decision_id=str(uuid.uuid4()),
    )


def _fetch_current_price(ticker: str) -> float | None:
    """Pull the most recent close from Polygon (Stocks Starter is 15-min
    delayed but close enough for sanity checks). Returns None if unavailable."""
    try:
        with PolygonClient() as p:
            resp = p.previous_close(ticker)
            results = resp.get("results") or []
            if not results:
                return None
            return float(results[0].get("c", 0)) or None
    except Exception as e:
        log.warning("could not fetch current price for %s: %s", ticker, e)
        return None


def _print_decision(d: AgentDecision) -> None:
    print("\n=== AGENT DECISION ===")
    print(f"  Ticker:      {d.ticker} ({d.market}/{d.quote_currency})")
    print(f"  Rating:      {d.rating}")
    print(f"  Entry:       ${d.entry_price}")
    print(f"  Stop:        ${d.stop_loss}")
    print(f"  Price Tgt:   ${d.price_target}")
    print(f"  Horizon:     {d.time_horizon}")
    print(f"  Size (LLM):  {d.suggested_size_pct * 100:.2f}% of portfolio")
    print(f"  Decision ID: {d.decision_id}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    _load_env()

    parser = argparse.ArgumentParser(description="Decide + size + (optional) submit to Alpaca paper.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--use-cached", action="store_true",
                        help="Replay the most recent cached state for this ticker (no LLM cost)")
    parser.add_argument("--method", choices=["atr", "llm_pct"], default="atr",
                        help="Risk sizing method")
    parser.add_argument("--risk-per-trade", type=float, default=0.005)
    parser.add_argument("--max-position-pct", type=float, default=0.10)
    parser.add_argument("--submit", action="store_true",
                        help="Actually submit the order to Alpaca (default: dry run)")
    parser.add_argument("--hold", action="store_true",
                        help="Save the order as PENDING (no broker call) — wait for mobile approval")
    parser.add_argument("--refuse-outside-hours", action="store_true")
    parser.add_argument("--no-persist", action="store_true",
                        help="Skip writing to the trade log DB")
    parser.add_argument("--db-url", default=os.environ.get("LOCAL_DATABASE_URL", "sqlite:///./local.db"),
                        help="Trade log DB URL (default: SQLite file in CWD). "
                             "Production sets DATABASE_URL to Aurora — this flag overrides.")
    args = parser.parse_args()

    from sqlalchemy import create_engine
    repo = None if args.no_persist else TradeLogRepository(engine=create_engine(args.db_url, future=True))

    # 1. Get decision
    if args.use_cached:
        log.info("loading cached decision for %s", args.ticker)
        decision = _decision_from_cached(args.ticker)
    else:
        log.info("running fresh LLM pipeline for %s @ %s (~5-10 min, ~$0.50-1.50)", args.ticker, args.date)
        decision = propagate(args.ticker, args.date)
    _print_decision(decision)

    if repo is not None:
        repo.save_decision(decision)
        log.info("persisted decision %s", decision.decision_id)

    if not (decision.entry_price and decision.stop_loss):
        log.warning("decision missing entry/stop — cannot size; aborting before risk layer")
        return 1

    # 2. Live Alpaca context (equity + market price proxy)
    with AlpacaClient() as ac:
        acct = ac.account()
        print("\n=== ALPACA ACCOUNT ===")
        print(f"  Number:    {acct.account_number} ({acct.status})")
        print(f"  Equity:    ${acct.portfolio_value:,.2f}")
        print(f"  Buying pw: ${acct.buying_power:,.2f}")
        print(f"  PDT:       {acct.pattern_day_trader}")

    # Use entry as price proxy for sizing demo (real run would pull live quote)
    market_ctx = MarketContext(
        current_price=decision.entry_price,
        rolling_mean=decision.entry_price,
        rolling_std=max(decision.entry_price * 0.02, 1.0),  # 2% as conservative band
        atr=abs(decision.entry_price - decision.stop_loss) / 5.0,
        avg_daily_volume_usd=1_000_000_000.0,
        sector="Unknown",
    )
    portfolio_ctx = PortfolioContext(
        equity=acct.portfolio_value,
        existing_position_values_by_ticker={},
        existing_position_values_by_sector={},
        high_correlation_count=0,
    )
    cb = CircuitBreaker(kill_switch=StaticKillSwitchReader("RUN"))  # type: ignore[arg-type]

    # 3. Risk sizing -> TradeOrder
    order = size_from_decision(
        decision=decision,
        account_equity=acct.portfolio_value,
        market_ctx=market_ctx,
        portfolio_ctx=portfolio_ctx,
        circuit_breaker=cb,
        method=args.method,
        risk_per_trade=args.risk_per_trade,
        portfolio_limits=PortfolioLimits(max_position_pct=args.max_position_pct),
    )

    notional = order.quantity * (decision.entry_price or 0)
    pct = notional / acct.portfolio_value * 100 if acct.portfolio_value > 0 else 0
    print("\n=== TRADE ORDER ===")
    print(f"  Side:        {order.side}")
    print(f"  Quantity:    {order.quantity} shares (method={args.method})")
    print(f"  Notional:    ${notional:,.2f}  ({pct:.2f}% of equity)")
    print(f"  Stop:        ${order.stop_loss}")
    print(f"  Approved:    {order.risk_approved}")
    if order.rejection_reasons:
        print(f"  Reasons:     {order.rejection_reasons}")

    # 4. Submit / hold / dry-run
    current_price = _fetch_current_price(args.ticker)
    if current_price:
        print(f"\n  Current price (Polygon, delayed): ${current_price:.2f}")

    if args.hold:
        # Persist the order in PENDING state — mobile approval will submit later.
        # Still run all the guards so we don't store a bad order.
        from tradingagents_us.schemas import OrderUpdate

        guard_config = ExecutionConfig(dry_run=True, refuse_outside_hours=False)
        guard_result = submit_order(order, config=guard_config, decision=decision, current_price=current_price)
        if not guard_result.dry_run or guard_result.update.status == "REJECTED":
            # Guards failed even in dry-run -> reject before persisting
            if repo is not None:
                repo.save_order(order, broker_order_id=None)
                repo.append_update(guard_result.update)
            print(f"\n=== HOLD: REJECTED by guards ===\n  Reasons: {guard_result.refusal_reasons}")
            return 1

        if repo is not None:
            repo.save_order(order, broker_order_id=None)
            repo.append_update(OrderUpdate(
                order_id=order.order_id, status="PENDING",
                error_message="awaiting_mobile_approval",
                timestamp_utc=datetime.now(timezone.utc),
            ))
        print(f"\n=== HOLD: order persisted as PENDING ===")
        print(f"  Order ID:    {order.order_id}")
        print(f"  Approve via: POST /v1/orders/{order.order_id}/approve (mobile)")
        print(f"  Or CLI:      curl -X POST http://localhost:8000/v1/orders/{order.order_id}/approve")
        return 0

    config = ExecutionConfig(dry_run=not args.submit, refuse_outside_hours=args.refuse_outside_hours)
    result = submit_order(order, config=config, decision=decision, current_price=current_price)

    if repo is not None:
        repo.save_order(order, broker_order_id=result.broker_order_id)
        repo.append_update(result.update)
        log.info("persisted order %s + update status=%s", order.order_id, result.update.status)

    print("\n=== EXECUTION RESULT ===")
    print(f"  Dry run:     {result.dry_run}")
    print(f"  Submitted:   {result.submitted}")
    print(f"  Status:      {result.update.status}")
    if result.broker_order_id:
        print(f"  Broker ID:   {result.broker_order_id}")
    if result.refusal_reasons:
        print(f"  Refusals:    {result.refusal_reasons}")
    if not args.submit:
        print("\n(--submit not set — no broker call made. Re-run with --submit to send to Alpaca paper.)")
    return 0 if (result.submitted or result.dry_run) else 1


if __name__ == "__main__":
    sys.exit(main())
