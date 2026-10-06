"""/v1/orders — trade history, approval, kill switch."""

from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.dataflows.sector_map import UNKNOWN_SECTOR, sector_for
from tradingagents_us.execution import ExecutionConfig, submit_order
from tradingagents_us.execution.book import pending_buy_exposure, read_open_buys, spendable_now
from tradingagents_us.execution.flatten import flatten_all
from tradingagents_us.execution.submit_lock import (
    SubmitLockUnavailableError,
    submit_section,
)
from tradingagents_us.risk.cash_budget import PendingBuy
from tradingagents_us.risk.kill_switch import FileKillSwitchReader, default_kill_switch_path
from tradingagents_us.risk.portfolio_limits import (
    PortfolioContext,
    PortfolioLimits,
    check_limits,
)
from tradingagents_us.schemas import KillSwitchState, OrderUpdate, TradeOrder
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.models import AgentDecisionRow, TradeOrderRow
from tradingagents_us.storage.repository import row_to_decision

from ..deps import get_alpaca, get_repo, require_admin, require_token

router = APIRouter()


class OrderListItem(BaseModel):
    """Wire format for /v1/orders — joins local row + broker view where possible."""
    order_id: str
    decision_id: str
    ticker: str
    side: str
    quantity: int
    order_type: str
    stop_loss: float
    risk_approved: bool
    rejection_reasons: list[str]
    broker_order_id: str | None
    broker_status: str | None
    filled_qty: int = 0
    avg_fill_price: float | None = None
    submitted_at_utc: datetime


class KillSwitchUpdate(BaseModel):
    state: KillSwitchState


@router.get("", response_model=list[OrderListItem])
async def list_orders(
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
    alpaca: AlpacaClient = Depends(get_alpaca),
) -> list[OrderListItem]:
    """Recent orders from local DB, enriched with current Alpaca status where available."""
    rows = repo.list_open_orders()

    # Best-effort broker lookup; if Alpaca is unreachable, return DB-only rows
    broker_by_id: dict[str, dict] = {}
    try:
        broker_orders = alpaca.list_orders(status="all", limit=50)
        for o in broker_orders:
            broker_by_id[o.id] = {
                "status": o.status,
                "filled_qty": int(o.filled_qty),
                "avg_fill_price": o.filled_avg_price,
            }
    except Exception:
        pass
    finally:
        alpaca.close()

    out: list[OrderListItem] = []
    for r in rows:
        bk = broker_by_id.get(r.broker_order_id or "")
        out.append(
            OrderListItem(
                order_id=r.order_id,
                decision_id=r.decision_id,
                ticker=r.ticker,
                side=r.side,
                quantity=r.quantity,
                order_type=r.order_type,
                stop_loss=r.stop_loss,
                risk_approved=r.risk_approved,
                rejection_reasons=r.rejection_reasons_json or [],
                broker_order_id=r.broker_order_id,
                broker_status=bk["status"] if bk else None,
                filled_qty=bk["filled_qty"] if bk else 0,
                avg_fill_price=bk["avg_fill_price"] if bk else None,
                submitted_at_utc=r.submitted_at_utc,
            )
        )
    return out


@router.get("/pending", response_model=list[OrderListItem])
async def list_pending_orders(
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> list[OrderListItem]:
    """Orders persisted via `scripts/trade.py --hold` awaiting approval.

    Excludes orders that have a broker_order_id (already at broker) OR a
    REJECTED update (user rejected via /reject)."""
    from tradingagents_us.storage.models import OrderUpdateRow

    out: list[OrderListItem] = []
    with repo.session() as s:
        rows = s.execute(
            select(TradeOrderRow).order_by(TradeOrderRow.submitted_at_utc.desc())
        ).scalars().all()
        for r in rows:
            if r.broker_order_id is not None:
                continue
            # Skip if any REJECTED update exists
            rejected = s.execute(
                select(OrderUpdateRow).where(
                    OrderUpdateRow.order_id == r.order_id,
                    OrderUpdateRow.status == "REJECTED",
                ).limit(1)
            ).scalar_one_or_none()
            if rejected is not None:
                continue
            out.append(OrderListItem(
                order_id=r.order_id, decision_id=r.decision_id, ticker=r.ticker,
                side=r.side, quantity=r.quantity, order_type=r.order_type,
                stop_loss=r.stop_loss, risk_approved=r.risk_approved,
                rejection_reasons=r.rejection_reasons_json or [],
                broker_order_id=None, broker_status="PENDING",
                filled_qty=0, avg_fill_price=None,
                submitted_at_utc=r.submitted_at_utc,
            ))
    return out


#: A tap waits this long for a daily-run ticker to finish sizing, then 503s.
APPROVE_LOCK_TIMEOUT_S = 15.0
#: The caps a held BUY is re-checked against: the daily run's, which are the
#: defaults (daily_run.sh passes trade.py no cap flags).
APPROVE_LIMITS = PortfolioLimits()


def _previous_close(ticker: str) -> float | None:
    try:
        from tradingagents_us.dataflows.polygon import PolygonClient
        with PolygonClient() as p:
            results = p.previous_close(ticker).get("results") or []
            if results:
                return float(results[0].get("c") or 0) or None
    except Exception:  # noqa: BLE001 — a missing price is handled by the caller
        return None
    return None


def _refuse_if_killed(repo: TradeLogRepository, user: str, order_id: str) -> None:
    ks_state = FileKillSwitchReader().read()
    if ks_state != "RUN":
        # Audit is best-effort: the block below happens either way.
        with contextlib.suppress(Exception):
            repo.append_kill_event(
                state=ks_state, actor=user, source="api",
                detail=f"blocked approve of order {order_id}",
            )
        raise HTTPException(409, f"kill switch is {ks_state} — approvals disabled")


def _refuse_if_submitted(repo: TradeLogRepository, order_id: str) -> None:
    """A double tap: the other tap may have sent the order while this one waited."""
    with repo.session() as s:
        row = s.get(TradeOrderRow, order_id)
        broker_order_id = row.broker_order_id if row is not None else None
    if broker_order_id is not None:
        raise HTTPException(409, f"order already submitted: broker={broker_order_id}")


def _refuse_if_unaffordable(
    alpaca: AlpacaClient, order: TradeOrder, price: float | None,
    prices: dict[str, float | None],
) -> list[PendingBuy]:
    """A held BUY is re-costed against the cash the account has NOW; returns the open BUYs.

    It was sized when it was held, possibly days ago; tonight's daily run may
    have committed that cash since. Priced from prices fetched before the lock
    (no market-data call is made while it is held); an unpriceable pending BUY
    or a missing reference price refuses rather than guesses.
    """
    try:
        spendable, open_buys = spendable_now(alpaca, prices.get)
    except Exception as exc:  # noqa: BLE001 — no cash figure, no BUY
        raise HTTPException(503, f"could not read the account to re-check cash: {exc}") from exc
    if price is None or spendable is None:
        raise HTTPException(
            409, "cannot re-check this BUY against current cash (no price for it or for a "
                 "pending BUY); it stays PENDING"
        )
    notional = order.quantity * price
    if notional > spendable:
        raise HTTPException(
            409, f"BUY needs ${notional:,.2f} but only ${spendable:,.2f} is spendable now; "
                 f"it stays PENDING"
        )
    return open_buys


def _refuse_if_over_caps(
    alpaca: AlpacaClient, order: TradeOrder, price: float,
    prices: dict[str, float | None], sectors: dict[str, str], open_buys: list[PendingBuy],
) -> None:
    """A held BUY is re-checked against the single-name and sector caps NOW.

    Tonight's run may have filled this name, or its sector, up to the cap
    since the order was held, and the run cannot see a held order: it lives
    only in the DB. The exposure is the daily run's: positions plus open BUYs.
    Correlation and liquidity are not measured again here (that would take
    market data under the lock); they were checked when the order was sized.
    """
    try:
        equity = alpaca.account().portfolio_value
        positions = alpaca.list_positions()
    except Exception as exc:  # noqa: BLE001 — no book, no BUY
        raise HTTPException(503, f"could not read the account to re-check the caps: {exc}") from exc
    by_ticker = {p.symbol: abs(p.market_value) for p in positions}
    for sym, value in pending_buy_exposure(open_buys, prices.get).items():
        by_ticker[sym] = by_ticker.get(sym, 0.0) + value
    # A name that joined the book while this request waited (another tap's BUY
    # filled) was never looked up. Counted as "Unknown" it would drop out of
    # its real sector, so refuse the way an unpriced pending BUY refuses.
    unlooked = sorted(sym for sym in by_ticker if sym not in sectors)
    if unlooked:
        raise HTTPException(
            409, f"no sector looked up for {', '.join(unlooked)}: it joined the book while "
                 f"this approval waited, or the position read before it failed; it stays "
                 f"PENDING, tap again"
        )
    by_sector: dict[str, float] = {}
    for sym, value in by_ticker.items():
        by_sector[sectors[sym]] = by_sector.get(sectors[sym], 0.0) + value
    ok, reasons = check_limits(
        order.ticker, sectors.get(order.ticker, UNKNOWN_SECTOR), order.quantity * price,
        avg_daily_volume_usd=float("inf"),
        ctx=PortfolioContext(
            equity=equity,
            existing_position_values_by_ticker=by_ticker,
            existing_position_values_by_sector=by_sector,
            high_correlation_count=0,
        ),
        limits=APPROVE_LIMITS,
    )
    if not ok:
        raise HTTPException(
            409, f"BUY would break the portfolio caps now ({'; '.join(reasons)}); "
                 f"it stays PENDING"
        )


def _sectors(alpaca: AlpacaClient, symbols: set[str]) -> dict[str, str]:
    """Sector of every name the locked cap check can meet, looked up before it.

    A lookup can be a Polygon call, and none is made under the lock. A name
    found only under the lock refuses the BUY (`_refuse_if_over_caps`).
    """
    names = set(symbols)
    with contextlib.suppress(Exception):
        names.update(p.symbol for p in alpaca.list_positions())
    return {sym: sector_for(sym) for sym in names}


# A plain `def`, not `async def`: FastAPI runs it in its threadpool. The lock
# wait below sleeps, and in an async handler that sleep would stall the event
# loop, and with it every other request, the kill switch included.
@router.post("/{order_id}/approve")
def approve_order(
    order_id: str,
    user: str = Depends(require_admin),
    repo: TradeLogRepository = Depends(get_repo),
) -> dict:
    """Mobile-approved submission. Re-runs the executor with dry_run=False so
    the stale + entry sanity guards re-evaluate against the *current* market
    state — not the one captured when the order was held."""

    # Kill switch gates THIS path too — an armed PAUSE_NEW/FLATTEN_ALL must
    # block a stale Approve tap the same way it blocks the daily run.
    _refuse_if_killed(repo, user, order_id)

    # Load order + decision rows
    with repo.session() as s:
        order_row = s.get(TradeOrderRow, order_id)
        if order_row is None:
            raise HTTPException(404, f"order not found: {order_id}")
        if order_row.broker_order_id is not None:
            raise HTTPException(409, f"order already submitted: broker={order_row.broker_order_id}")
        decision_row = s.get(AgentDecisionRow, order_row.decision_id)

    if decision_row is None:
        raise HTTPException(404, f"decision not found for order {order_id}")

    decision = row_to_decision(decision_row)

    # Rebuild TradeOrder for the executor
    order = TradeOrder(
        order_id=order_row.order_id, decision_id=order_row.decision_id,
        ticker=order_row.ticker, market=order_row.market,
        side=order_row.side, quantity=order_row.quantity,
        order_type=order_row.order_type, limit_price=order_row.limit_price,
        stop_loss=order_row.stop_loss, risk_approved=order_row.risk_approved,
        rejection_reasons=order_row.rejection_reasons_json or [],
        submitted_at_utc=order_row.submitted_at_utc,
    )
    is_buy = order.side == "BUY"

    # Market data BEFORE the lock: the order's own price for re-validation and,
    # for a BUY, the prices of the pending BUYs whose cash it must not take.
    current_price = _previous_close(order.ticker)
    prices: dict[str, float | None] = {order.ticker: current_price}
    # Only a BUY reads the account (to re-check its cash); a SELL needs no
    # broker client here at all.
    alpaca = _account_client() if is_buy else None
    sectors: dict[str, str] = {}
    try:
        if alpaca is not None:
            with contextlib.suppress(Exception):
                for pending in read_open_buys(alpaca):
                    if pending.symbol not in prices:
                        prices[pending.symbol] = _previous_close(pending.symbol)
            sectors = _sectors(alpaca, set(prices))
        return _submit_locked(repo, user, order_id, order, decision, current_price,
                              prices, sectors, alpaca)
    finally:
        if alpaca is not None:
            with contextlib.suppress(Exception):
                alpaca.close()


def _account_client() -> AlpacaClient:
    return get_alpaca()


def _submit_locked(
    repo: TradeLogRepository, user: str, order_id: str, order: TradeOrder,
    decision: Any, current_price: float | None, prices: dict[str, float | None],
    sectors: dict[str, str], alpaca: AlpacaClient | None,
) -> dict:
    is_buy = alpaca is not None

    # The daily run may be sizing BUYs right now against the same cash; take
    # the lock every order path takes (execution/submit_lock.py). A SELL goes
    # ahead without it rather than wait on a run; a BUY is refused, and the
    # held order stays PENDING for another tap.
    try:
        with submit_section(exit_only=not is_buy, timeout_s=APPROVE_LOCK_TIMEOUT_S):
            # Re-checked under the lock: the switch may have been flipped, the
            # order sent by another tap, or the cash or the caps used up, while
            # this request waited for it.
            _refuse_if_killed(repo, user, order_id)
            _refuse_if_submitted(repo, order_id)
            if alpaca is not None:
                ref = current_price or order.limit_price or decision.entry_price
                open_buys = _refuse_if_unaffordable(alpaca, order, ref, prices)
                _refuse_if_over_caps(alpaca, order, ref, prices, sectors, open_buys)
            result = submit_order(
                order, config=ExecutionConfig(dry_run=False),
                decision=decision, current_price=current_price,
            )
            # Recorded before the lock is let go, so a second tap waiting on
            # it finds the order at the broker. The held row exists already;
            # a refusal carries no broker id, and merging that None in would
            # erase the id another tap recorded.
            if result.broker_order_id is not None:
                repo.save_order(order, broker_order_id=result.broker_order_id)
            repo.append_update(result.update)
    except SubmitLockUnavailableError as exc:
        raise HTTPException(
            503, f"another order path is sizing against this account; retry shortly ({exc})"
        ) from exc

    if not result.submitted:
        raise HTTPException(
            422,
            {"order_id": order_id, "status": result.update.status,
             "refusal_reasons": result.refusal_reasons or [],
             "error": result.update.error_message},
        )

    return {
        "order_id": order_id,
        "broker_order_id": result.broker_order_id,
        "status": result.update.status,
    }


@router.post("/{order_id}/reject")
async def reject_order(
    order_id: str,
    user: str = Depends(require_admin),
    repo: TradeLogRepository = Depends(get_repo),
) -> dict:
    """User-rejected: record a REJECTED update so the order disappears from
    the pending list. No broker call (nothing was submitted)."""
    with repo.session() as s:
        order_row = s.get(TradeOrderRow, order_id)
        if order_row is None:
            raise HTTPException(404, f"order not found: {order_id}")
        if order_row.broker_order_id is not None:
            raise HTTPException(409, "order already at broker; use /cancel")

    repo.append_update(OrderUpdate(
        order_id=order_id, status="REJECTED",
        error_message="user_rejected",
        timestamp_utc=datetime.now(UTC),
    ))
    return {"order_id": order_id, "status": "REJECTED"}


@router.post("/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    user: str = Depends(require_admin),
    repo: TradeLogRepository = Depends(get_repo),
    alpaca: AlpacaClient = Depends(get_alpaca),
) -> dict[str, str | None]:
    """Cancel an order that is already AT the broker; persist the outcome.

    Three failure modes this path used to have, all of which lie to a money
    screen:
      - it answered `{"status": "cancelled"}` for an order with no
        broker_order_id — nothing was cancelled, the order was never submitted
        (that case is /reject's job);
      - it never wrote an OrderUpdate, so the local DB kept the order live and
        the history tab could never show a cancellation;
      - a broker refusal (Alpaca 422 "order is not cancelable" — i.e. it filled
        in the meantime) surfaced as an opaque 500 while still reporting
        success to the caller.

    Alpaca acks a cancel with 204 and moves the order to `pending_cancel`; the
    terminal `canceled` status lands asynchronously, and a late fill can still
    win the race. So the CANCELLED row records *the request*, and the
    broker-enriched /v1/orders view stays the source of truth for what the
    order actually did.
    """
    with repo.session() as s:
        order_row = s.get(TradeOrderRow, order_id)
        if order_row is None:
            raise HTTPException(404, f"order not found: {order_id}")
        broker_order_id = order_row.broker_order_id

    if broker_order_id is None:
        raise HTTPException(409, "order is not at the broker; use /reject")

    try:
        alpaca.cancel_order(broker_order_id)
    except Exception as exc:
        # Deliberately no CANCELLED row here: the order is still live at the
        # broker and can still fill. Failing loud beats a comforting lie.
        raise HTTPException(502, f"broker refused cancel: {exc}") from exc
    finally:
        alpaca.close()

    repo.append_update(OrderUpdate(
        order_id=order_id, status="CANCELLED",
        error_message="user_cancelled",
        timestamp_utc=datetime.now(UTC),
    ))
    return {
        "order_id": order_id,
        "broker_order_id": broker_order_id,
        "status": "CANCELLED",
    }


# A plain `def`, like approve: an armed state waits on the submit lock below,
# and that wait must not stall the event loop.
@router.post("/kill-switch")
def set_kill_switch(
    body: KillSwitchUpdate,
    user: str = Depends(require_admin),
    repo: TradeLogRepository = Depends(get_repo),
) -> dict[str, str]:
    """Mobile-controlled remote kill switch. See ADR-005.

    Enforced by FileKillSwitchReader in trade.py's circuit breaker and by
    the daily_run.sh pre-check (which also executes FLATTEN_ALL). The
    single-box file backend is fine while API + trader share a host; the
    DynamoDB reader exists for a future multi-host split.

    An armed state answers only after passing through the submit lock. Every
    live BUY reads the switch under that lock as the last step before its POST
    (the executor), so a BUY already past that read is in before this
    answers, and the flatten then cancels it; any later BUY sees the new
    state. The wait is exit-only: a lock that stays held delays the flatten by
    at most EXIT_TIMEOUT_S, never drops it, and the BUY that held the lock
    meets the armed switch at its POST.
    """
    flag_path = default_kill_switch_path()
    # Atomic replace — a crash mid-write must never leave a truncated file
    # (the reader treats empty as PAUSE_NEW, but never risk it).
    tmp_path = f"{flag_path}.tmp"
    with open(tmp_path, "w") as f:
        f.write(body.state)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, flag_path)

    # Audit is best-effort; the state write above already took effect.
    with contextlib.suppress(Exception):
        repo.append_kill_event(state=body.state, actor=user, source="api")

    flatten_summary: str | None = None
    if body.state != "RUN":
        with submit_section(exit_only=True):
            if body.state == "FLATTEN_ALL":
                flatten_summary = _flatten_now(repo, user)

    resp = {"state": body.state, "path": flag_path}
    if flatten_summary is not None:
        resp["flatten"] = flatten_summary
    return resp


def _flatten_now(repo: TradeLogRepository, user: str) -> str:
    """FLATTEN_ALL executes NOW, not at the next 22:30 UTC run.

    The switch is a panic button, hours of latency defeats it. kill_check
    remains the daily backstop if this attempt fails.
    """
    try:
        result = flatten_all()
    except Exception as exc:
        with contextlib.suppress(Exception):
            repo.append_kill_event(
                state="FLATTEN_ALL", actor=user, source="api",
                detail=f"immediate flatten FAILED: {exc}",
            )
        raise HTTPException(
            502,
            f"kill switch armed, but immediate flatten failed: {exc} — "
            f"the daily-run backstop will retry",
        ) from exc
    with contextlib.suppress(Exception):
        repo.append_kill_event(
            state="FLATTEN_ALL", actor=user, source="api",
            detail=("noop: " if result.noop else ("executed: " if result.ok else "partial: "))
            + result.summary,
        )
    if not result.ok:
        raise HTTPException(
            502,
            f"kill switch armed, but flatten was PARTIAL: {result.summary}",
        )
    return result.summary


@router.get("/kill-switch")
async def get_kill_switch(user: str = Depends(require_token)) -> dict[str, str]:
    flag_path = default_kill_switch_path()
    try:
        with open(flag_path) as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return {"state": "RUN"}
    # Mirror FileKillSwitchReader: an armed-then-emptied file fails safe.
    if raw not in ("RUN", "PAUSE_NEW", "FLATTEN_ALL"):
        return {"state": "PAUSE_NEW"}
    return {"state": raw}
