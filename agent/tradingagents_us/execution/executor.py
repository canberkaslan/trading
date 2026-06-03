"""TradeOrder -> Alpaca submission.

Deterministic, idempotent, dry-run-safe. The client_order_id is derived
from the decision_id + order_id so retries don't double-submit.

Sanity guards (Phase 4g, after the 2026-06-01 AAPL stale-decision incident):

- decision_max_age_hours: refuse if the LLM decision is older than this.
  Catches replays of cached state after the underlying price moved.
- Headroom check: refuse BUY if current_price >= price_target * (1 - tp_headroom).
  Catches "the target was reached while we slept" — no profit left in the trade.
- Stop-proximity check: refuse BUY if current_price <= stop_loss * (1 + stop_buffer).
  Catches "we'd get stopped out immediately on a normal spread".
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx

from ..dataflows.alpaca_broker import AlpacaClient
from ..schemas import AgentDecision, OrderUpdate, TradeOrder

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionConfig:
    dry_run: bool = True              # safety default — never submit by default
    refuse_outside_hours: bool = False   # paper trades queue OK; flip on for live
    refuse_on_pdt: bool = True        # safety — never trade if PDT flag triggers
    use_bracket: bool = True          # attach stop + take_profit as broker-side legs
    take_profit_price: float | None = None  # explicit override; else use decision PT
    decision_max_age_hours: float = 24.0   # refuse stale decisions; 0 disables
    tp_headroom: float = 0.05         # require ≥5% upside left to TP; 0 disables
    stop_buffer: float = 0.02         # current_price must be >=2% above stop; 0 disables


@dataclass(frozen=True)
class ExecutionResult:
    submitted: bool
    dry_run: bool
    update: OrderUpdate
    broker_order_id: str | None = None
    refusal_reasons: list[str] | None = None


def submit_order(
    order: TradeOrder,
    client: AlpacaClient | None = None,
    config: ExecutionConfig = ExecutionConfig(),
    decision: AgentDecision | None = None,
    current_price: float | None = None,
) -> ExecutionResult:
    """Push a risk-approved TradeOrder to Alpaca (paper or live).

    Behavior:
    - If `order.risk_approved` is False, refuse and surface the existing
      rejection_reasons.
    - Stale-decision / entry-sanity guards run before dry_run so even a
      dry run reports them (helps catch bad invocations in CI).
    - If `config.dry_run` (default), build the payload and log it but do
      not contact the broker.
    - Otherwise check broker preconditions (account status, PDT flag,
      market hours if configured) and submit with bracket legs.

    Args:
        current_price: live last-trade price. Required when tp_headroom or
            stop_buffer guards are enabled (default ON). Pass None to skip
            those guards (e.g. backtests).
    """
    refusals: list[str] = []
    now = datetime.now(timezone.utc)

    # 1. Risk approval gate
    if not order.risk_approved:
        refusals.extend(order.rejection_reasons or ["risk_layer_rejected"])
        return ExecutionResult(
            submitted=False,
            dry_run=config.dry_run,
            update=OrderUpdate(
                order_id=order.order_id, status="REJECTED",
                error_message=";".join(refusals), timestamp_utc=now,
            ),
            refusal_reasons=refusals,
        )

    # 2. Stale-decision guard (Phase 4g)
    if config.decision_max_age_hours > 0 and decision is not None:
        age = now - decision.timestamp_utc
        if age > timedelta(hours=config.decision_max_age_hours):
            refusals.append(
                f"stale_decision: age={age.total_seconds() / 3600:.1f}h "
                f"exceeds {config.decision_max_age_hours:.1f}h"
            )

    # 3. Entry sanity (TP headroom + stop proximity)
    if current_price is not None and decision is not None and order.side == "BUY":
        if (
            config.tp_headroom > 0
            and decision.price_target
            and current_price >= decision.price_target * (1 - config.tp_headroom)
        ):
            refusals.append(
                f"no_tp_headroom: current=${current_price:.2f} "
                f">= TP=${decision.price_target:.2f} * (1-{config.tp_headroom:.0%})"
            )
        if (
            config.stop_buffer > 0
            and decision.stop_loss
            and current_price <= decision.stop_loss * (1 + config.stop_buffer)
        ):
            refusals.append(
                f"too_close_to_stop: current=${current_price:.2f} "
                f"<= stop=${decision.stop_loss:.2f} * (1+{config.stop_buffer:.0%})"
            )

    if refusals:
        return ExecutionResult(
            submitted=False,
            dry_run=config.dry_run,
            update=OrderUpdate(
                order_id=order.order_id, status="REJECTED",
                error_message=";".join(refusals), timestamp_utc=now,
            ),
            refusal_reasons=refusals,
        )

    # 4. Dry run — log what we would have done, no broker call
    if config.dry_run:
        log.info(
            "[dry_run] would submit: %s %s qty=%s order_type=%s",
            order.side, order.ticker, order.quantity, order.order_type,
        )
        return ExecutionResult(
            submitted=False,
            dry_run=True,
            update=OrderUpdate(
                order_id=order.order_id, status="PENDING",
                error_message=None, timestamp_utc=now,
            ),
        )

    # 5. Live broker submission
    own_client = client is None
    cli = client or AlpacaClient()
    try:
        acct = cli.account()
        if acct.trading_blocked:
            refusals.append("account_trading_blocked")
        if config.refuse_on_pdt and acct.pattern_day_trader:
            refusals.append("pattern_day_trader_active")

        if config.refuse_outside_hours:
            clock = cli.clock()
            if not clock.is_open:
                refusals.append(f"market_closed_until={clock.next_open}")

        if refusals:
            return ExecutionResult(
                submitted=False,
                dry_run=False,
                update=OrderUpdate(
                    order_id=order.order_id, status="REJECTED",
                    error_message=";".join(refusals), timestamp_utc=now,
                ),
                refusal_reasons=refusals,
            )

        # Idempotency: deterministic client_order_id from decision_id + order_id
        client_oid = f"tr-{order.decision_id[:8]}-{order.order_id[:8]}"

        # Bracket: attach stop_loss as broker-side leg + (if available)
        # take_profit from decision.price_target. Broker-side stop survives
        # disconnects and process restarts — much safer than only tracking
        # it in our risk layer.
        bracket_kwargs: dict = {}
        if config.use_bracket and order.side == "BUY":
            tp = config.take_profit_price or (decision.price_target if decision else None)
            sl = order.stop_loss if order.stop_loss > 0 else None
            if tp and sl:
                bracket_kwargs["take_profit_price"] = tp
                bracket_kwargs["stop_loss_price"] = sl

        broker = cli.submit_order(
            symbol=order.ticker,
            qty=order.quantity,
            side=order.side.lower(),  # type: ignore[arg-type]
            order_type="limit" if order.order_type == "LIMIT" else "market",
            time_in_force="day",
            limit_price=order.limit_price,
            client_order_id=client_oid,
            **bracket_kwargs,
        )

        return ExecutionResult(
            submitted=True,
            dry_run=False,
            update=OrderUpdate(
                order_id=order.order_id,
                status="ACCEPTED" if broker.status in {"accepted", "new", "pending_new"} else broker.status.upper(),
                filled_qty=int(broker.filled_qty),
                avg_fill_price=broker.filled_avg_price,
                timestamp_utc=now,
            ),
            broker_order_id=broker.id,
        )
    except httpx.HTTPError as e:
        return ExecutionResult(
            submitted=False,
            dry_run=False,
            update=OrderUpdate(
                order_id=order.order_id, status="REJECTED",
                error_message=f"broker_error: {e}", timestamp_utc=now,
            ),
            refusal_reasons=[f"broker_error: {e}"],
        )
    except Exception as e:  # broad: surface anything unexpected
        return ExecutionResult(
            submitted=False,
            dry_run=False,
            update=OrderUpdate(
                order_id=order.order_id, status="REJECTED",
                error_message=f"unexpected: {e}", timestamp_utc=now,
            ),
            refusal_reasons=[f"unexpected: {e}"],
        )
    finally:
        if own_client:
            cli.close()


def derive_client_order_id(decision_id: str, order_id: str | None = None) -> str:
    """Stable per-decision client_order_id, used for idempotency."""
    oid = order_id or str(uuid.uuid4())
    return f"tr-{decision_id[:8]}-{oid[:8]}"
