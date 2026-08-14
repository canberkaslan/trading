"""Settled-cash budgeting across a multi-process daily run.

`apply_cash_cap` stops ONE order from spending cash the account does not have.
It is not enough on its own, because `scripts/daily_run.sh` runs `scripts/trade.py`
once per ticker as a SEPARATE process: eleven processes each read the same
`account.cash` from Alpaca and each conclude they may spend all of it. Orders
submitted post-close sit `new`/`accepted` until the next open, so nothing in the
account snapshot reflects them while the run is still going.

The shared state that DOES span those processes is the broker's open-order book.
Reserving the notional of already-pending BUYs against cash turns eleven
independent budgets into one running budget, without any local lock or DB.

Pending SELLs are ignored deliberately: they release cash rather than consume it,
and counting proceeds before the fill would re-create the same optimism in the
other direction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingBuy:
    """An open BUY whose unfilled remainder is a claim on settled cash."""

    symbol: str
    unfilled_qty: float
    limit_price: float | None = None


def reserved_cash_for_open_buys(
    pending: list[PendingBuy],
    price_of: Callable[[str], float | None],
) -> float | None:
    """Cash already claimed by open BUYs, or None if any of them can't be priced.

    A limit order carries its own worst-case price. A market order does not, so the
    caller supplies `price_of` (a last/previous close) as the estimate.

    Returns None — meaning "unknown", not "zero" — when a pending BUY has neither a
    limit price nor a resolvable quote. The caller must treat that as a refusal to
    open new exposure: the whole point of this reservation is that unmeasured
    commitments must not be topped up with more of them. Collapsing an unpriceable
    order to 0.0 would restore exactly the over-commitment this module exists to stop.
    """
    total = 0.0
    for order in pending:
        qty = max(0.0, order.unfilled_qty)
        if qty == 0:
            continue
        price = order.limit_price
        if price is None or price <= 0:
            price = price_of(order.symbol)
        if price is None or price <= 0:
            return None
        total += qty * price
    return total


def spendable_cash(settled_cash: float, reserved: float | None) -> float | None:
    """Settled cash minus pending-BUY claims; None propagates the unknown.

    Never negative: an already-levered book has zero to spend, not a negative
    budget that some later subtraction could accidentally flip positive.
    """
    if reserved is None:
        return None
    return max(0.0, settled_cash - reserved)
