"""/v1/risk — the protections standing behind the book right now.

Separate from /v1/portfolio on purpose. The snapshot answers "what do I hold";
this answers "what happens if it gaps down tonight", and the two were being
conflated: `Position.stop_loss` in the snapshot is hardcoded to 0.0 because the
protective leg lives on an ORDER, not on the position, so the only honest place
to answer the question is here, from the order book.

This exists because the answer turned out to be bad. `risk.stop_coverage` could
compute it from the day it was written, nothing ever called it, and the first
run found 75.5% of held shares with no stop behind them — on an account whose
go-live checklist assumes every entry ships a bracket. A number nothing reads is
a number nobody knows.

Read-only: it submits nothing and touches no decision path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.risk.stop_coverage import (
    OrderView,
    PositionView,
    coverage,
    flatten_orders,
)

from ..deps import get_alpaca, require_token

router = APIRouter()


class SymbolCoverageOut(BaseModel):
    symbol: str
    position_qty: float
    protected_qty: float
    naked_qty: float
    #: Shares standing behind an order whose status the accounting does not
    #: recognise. Neither protected nor naked — see `is_actionable`.
    indeterminate_qty: float
    #: Protective quantity BEYOND the position size. Not a safety margin: a stop
    #: for more shares than are held opens a short when it triggers.
    excess_qty: float
    stop_prices: list[float]
    #: Whether a back-fill may safely place the missing stops for this name.
    #: False while anything is indeterminate — adding a stop on top of one that
    #: turns out to be live is two stops on one lot.
    is_actionable: bool


class StopCoverageOut(BaseModel):
    total_qty: float
    protected_qty: float
    naked_qty: float
    indeterminate_qty: float
    #: Percent of held shares with no protection. 0.0 on an empty book —
    #: holding nothing is not an exposure.
    naked_pct: float
    symbols: list[SymbolCoverageOut]
    #: Protective orders with no position under them. A stop with nothing to
    #: sell is a naked short order, not dead weight.
    orphan_stop_symbols: list[str]


@router.get("/stop-coverage", response_model=StopCoverageOut)
def stop_coverage(_: None = Depends(require_token), alpaca: AlpacaClient = Depends(get_alpaca)):
    """How much of the book has a protective stop behind it.

    `status="all"` and `nested=True` are load-bearing, not defensive: the resting
    leg of a bracket sits in status `held`, which Alpaca's "open" filter
    excludes, and it is returned as a child of its parent rather than at the top
    level. Asking the obvious way reports a fully bracketed book as having zero
    stops — an error that invents naked exposure, which is the direction that
    makes a back-fill place a second stop on protected shares.
    """
    try:
        positions = alpaca.list_positions()
        orders = flatten_orders(alpaca.list_orders(status="all", limit=500, nested=True))
    except Exception as e:
        raise HTTPException(502, f"alpaca_error: {e}") from e
    finally:
        alpaca.close()

    views = [
        OrderView(
            symbol=o.symbol,
            side=o.side.lower(),
            order_type=o.order_type.lower(),
            status=o.status.lower(),
            remaining_qty=max(0.0, o.qty - o.filled_qty),
            stop_price=o.stop_price,
        )
        for o in orders
    ]
    report = coverage([PositionView(p.symbol, p.qty, "long") for p in positions], views)

    return StopCoverageOut(
        total_qty=report.total_qty,
        protected_qty=sum(s.protected_qty for s in report.symbols),
        naked_qty=report.naked_qty,
        indeterminate_qty=report.indeterminate_qty,
        naked_pct=report.naked_pct,
        symbols=[
            SymbolCoverageOut(
                symbol=s.symbol,
                position_qty=s.position_qty,
                protected_qty=s.protected_qty,
                naked_qty=s.naked_qty,
                indeterminate_qty=s.indeterminate_qty,
                excess_qty=s.excess_qty,
                stop_prices=list(s.stop_prices),
                is_actionable=s.is_actionable,
            )
            for s in report.symbols
        ],
        orphan_stop_symbols=list(report.orphan_stop_symbols),
    )
