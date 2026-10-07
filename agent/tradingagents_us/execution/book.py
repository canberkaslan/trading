"""What the account has already committed: open BUY orders and the cash left.

Shared by the two paths that send cash-spending orders, the daily run's
tickers (scripts/trade.py) and a mobile approval (api/routes/orders.py), so
both reserve the same pending orders the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tradingagents_us.risk.cash_budget import (
    PendingBuy,
    reserved_cash_for_open_buys,
    spendable_cash,
)

#: Alpaca's per-call maximum for list_orders.
_PAGE_LIMIT = 500


def read_open_buys(client: Any) -> list[PendingBuy]:
    """Every open BUY on the account, across pages.

    Alpaca's list_orders returns at most 500 per call, and a truncated list
    under-reserves cash, so this pages (newest first, each page ending before
    the oldest of the last) until a short page.
    """
    open_buys: list[PendingBuy] = []
    fetched_count = _PAGE_LIMIT
    until_timestamp: str | None = None

    while fetched_count >= _PAGE_LIMIT:
        # AlpacaClient.list_orders does not expose 'until' directly; use httpx
        query = f"/orders?status=open&limit={_PAGE_LIMIT}&direction=desc"
        if until_timestamp is not None:
            query += f"&until={until_timestamp}"
        page = client._http.get(client.base_url + query).json()
        if not isinstance(page, list):
            break
        fetched_count = len(page)
        if fetched_count == 0:
            break

        for o_dict in page:
            if o_dict.get("side", "").upper() != "BUY":
                continue
            qty = float(o_dict["qty"])
            filled = float(o_dict.get("filled_qty", 0))
            limit_price = float(o_dict["limit_price"]) if o_dict.get("limit_price") else None
            open_buys.append(
                PendingBuy(
                    symbol=o_dict["symbol"],
                    unfilled_qty=qty - filled,
                    limit_price=limit_price,
                )
            )

        # Next page starts BEFORE the oldest (last in desc order) of this page
        if fetched_count >= _PAGE_LIMIT and page:
            until_timestamp = page[-1]["submitted_at"]
    return open_buys


def pending_buy_exposure(
    open_buys: list[PendingBuy], price_of: Callable[[str], float | None]
) -> dict[str, float]:
    """Notional of open BUYs per symbol: exposure the book is about to hold.

    Tonight's earlier tickers' BUYs have not filled yet, so the positions list
    does not show them, and the single-name and sector caps would let several
    BUYs into one sector that together break it. An unpriceable one is left
    out here; the cash budget already refuses new exposure in that case.
    """
    exposure: dict[str, float] = {}
    for buy in open_buys:
        price = buy.limit_price or price_of(buy.symbol)
        if price and buy.unfilled_qty > 0:
            exposure[buy.symbol] = exposure.get(buy.symbol, 0.0) + buy.unfilled_qty * price
    return exposure


def spendable_now(
    client: Any, price_of: Callable[[str], float | None]
) -> tuple[float | None, list[PendingBuy]]:
    """Settled cash net of open BUYs; None when a pending BUY cannot be priced.

    Open orders first, cash second. A BUY that fills between the two reads is
    then counted twice, in the orders and out of the cash, which errs safe;
    read the other way round it would be counted in neither.
    """
    open_buys = read_open_buys(client)
    acct = client.account()
    reserved = reserved_cash_for_open_buys(open_buys, price_of)
    return spendable_cash(acct.cash, reserved), open_buys
