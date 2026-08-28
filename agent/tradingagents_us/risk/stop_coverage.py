"""How much of the book actually has a protective stop behind it.

This exists because the question is easy to get wrong in the dangerous direction,
and the repo had no single answer to it. Asking Alpaca for `status=open` and
counting stop orders reports **zero stops on a fully bracketed book**: the resting
leg of a bracket sits in status `held`, and Alpaca's "open" filter excludes it.
The legs are also returned NESTED inside their parent unless the request opts in,
so a caller that does neither sees an unprotected book that is in fact protected.

Both mistakes point the same way — they invent naked exposure. That matters
because the consumer of this number is a backfill that submits the missing stops:
believing a covered position is naked makes it add a SECOND stop on shares that
already have one, and two stops on one lot is a short position waiting for a gap
down. The failure mode of the naive count is not a bad report, it is a bad trade.

Everything here is pure — orders and positions in, an accounting out. No broker
calls, so the rules below are pinned by unit tests against hand-built order books
rather than discovered against a live account.

Three buckets, never two:

    protected + naked + indeterminate == held quantity

`indeterminate` is the point of the module as much as `naked` is. An order in a
status this module does not recognize is not evidence of protection and not
evidence of its absence, and collapsing it into either one produces a confident
number with no support under it. Callers that place orders must refuse to act on
an indeterminate symbol — the same rule `risk.cash_budget` follows when it returns
None instead of 0.0 for an unpriceable commitment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Statuses in which an order can still execute, so it is real protection.
# `held` is the one that matters: it is where a bracket's stop leg rests while
# its take-profit sibling is the active side of the OCO. It is not a pending or
# inactive state — a `held` stop on this account triggered and filled on
# 2026-08-20 (UNH, 11 shares).
LIVE_STATUSES = frozenset(
    {
        "new",
        "accepted",
        "accepted_for_bidding",
        "held",
        "partially_filled",
        "pending_new",
        "pending_replace",
        "pending_review",
    }
)

# Statuses in which the order is gone and protects nothing further. `stopped` is
# here deliberately: it means a trade is guaranteed for that order, i.e. the
# protection is in the act of being consumed rather than standing by.
TERMINAL_STATUSES = frozenset(
    {
        "filled",
        "canceled",
        "expired",
        "replaced",
        "rejected",
        "done_for_day",
        "stopped",
    }
)

# Deliberately in NEITHER set. `pending_cancel` may or may not fill before the
# cancel lands; `suspended` is not working now but may resume; `calculated` is
# an end-of-day settlement state. Each is genuinely unknown, and this module
# reports unknown rather than guessing. Anything Alpaca adds later that we have
# not classified lands here too, which is the safe default for a new status.
AMBIGUOUS_STATUSES = frozenset({"pending_cancel", "suspended", "calculated"})

# Order types that stop a loss. `stop_limit` is included because it is protection,
# but it is protection with a caveat: in a gap the limit can be skipped and the
# position stays open. `SymbolCoverage.stop_types` carries the type through so a
# report can say which kind of protection a name has.
PROTECTIVE_TYPES = frozenset({"stop", "stop_limit", "trailing_stop"})

# Fractional-share arithmetic leaves float dust; anything under this is not a
# real uncovered sliver. Matches execution.reconcile.QTY_EPSILON.
QTY_EPSILON = 1e-9


@dataclass(frozen=True)
class OrderView:
    """The order-flow facts this module needs, decoupled from the broker adapter.

    `remaining_qty` is the unfilled part: a stop that already sold half its size
    protects only what is left of it.
    """

    symbol: str
    side: str  # "buy" | "sell"
    order_type: str
    status: str
    remaining_qty: float
    stop_price: float | None = None


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: float  # unsigned
    side: str  # "long" | "short"


@dataclass(frozen=True)
class SymbolCoverage:
    symbol: str
    position_qty: float
    position_side: str
    protected_qty: float
    indeterminate_qty: float
    # Shares with neither live protection nor an ambiguous order standing over
    # them. This is the number a backfill acts on.
    naked_qty: float
    # Protective quantity beyond the position size. Not a safety margin — a stop
    # for more shares than are held opens a short when it triggers. Usually the
    # fingerprint of a backfill that ran twice.
    excess_qty: float
    stop_prices: tuple[float, ...] = ()
    stop_types: tuple[str, ...] = ()

    @property
    def is_fully_protected(self) -> bool:
        return self.naked_qty <= QTY_EPSILON and self.indeterminate_qty <= QTY_EPSILON

    @property
    def is_actionable(self) -> bool:
        """Whether a backfill may safely place the missing stops for this name.

        False while anything is indeterminate: the missing quantity cannot be
        computed without knowing whether those orders protect, and adding a stop
        on top of one that turns out to be live is the duplicate this module
        exists to prevent.
        """
        return self.naked_qty > QTY_EPSILON and self.indeterminate_qty <= QTY_EPSILON


@dataclass(frozen=True)
class CoverageReport:
    symbols: tuple[SymbolCoverage, ...] = ()
    # Protective orders whose symbol is not held at all. A stop with no position
    # under it is a naked short order, not dead weight — it belongs in a report.
    orphan_stop_symbols: tuple[str, ...] = field(default=())

    @property
    def total_qty(self) -> float:
        return sum(s.position_qty for s in self.symbols)

    @property
    def protected_qty(self) -> float:
        return sum(min(s.protected_qty, s.position_qty) for s in self.symbols)

    @property
    def naked_qty(self) -> float:
        return sum(s.naked_qty for s in self.symbols)

    @property
    def indeterminate_qty(self) -> float:
        return sum(s.indeterminate_qty for s in self.symbols)

    @property
    def naked_pct(self) -> float:
        """Percent of held shares with no protection. 0.0 on an empty book —
        holding nothing is not an exposure, and reporting 100% would page someone
        for a flat account."""
        total = self.total_qty
        return (self.naked_qty / total * 100.0) if total > QTY_EPSILON else 0.0

    @property
    def has_indeterminate(self) -> bool:
        return self.indeterminate_qty > QTY_EPSILON


def flatten_orders(orders: list[OrderView] | list, legs_of=None) -> list:
    """Depth-first flatten of an order tree into one list including all children.

    Bracket children arrive nested under their parent. `legs_of` extracts them
    (defaults to a `.legs` attribute) so this works on both the broker dataclass
    and plain test fixtures.
    """
    if legs_of is None:
        def legs_of(order):  # noqa: E731 — a default, not a stored lambda
            return getattr(order, "legs", ()) or ()

    out: list = []
    stack = list(reversed(orders))
    while stack:
        order = stack.pop()
        out.append(order)
        stack.extend(reversed(list(legs_of(order))))
    return out


def _protects(order: OrderView, position_side: str) -> bool:
    """Whether this order is the protective side for a position of that side.

    A long is stopped out by a SELL; a short is stopped out by a BUY. A sell stop
    sitting under a short position is not protection, it is an entry — counting
    it would report a short book as covered while its risk is entirely open.
    """
    if order.order_type not in PROTECTIVE_TYPES:
        return False
    wanted = "sell" if position_side == "long" else "buy"
    return order.side == wanted


def coverage(
    positions: list[PositionView], orders: list[OrderView]
) -> CoverageReport:
    """Per-symbol protective-stop accounting for the whole book.

    `orders` must already be flattened (see `flatten_orders`) and should come from
    a query wide enough to include `held` — `status="all"` at the broker, not
    `status="open"`.
    """
    live: dict[str, list[OrderView]] = {}
    unknown: dict[str, list[OrderView]] = {}
    for order in orders:
        if order.remaining_qty <= QTY_EPSILON:
            continue
        if order.status in TERMINAL_STATUSES:
            continue
        bucket = live if order.status in LIVE_STATUSES else unknown
        bucket.setdefault(order.symbol, []).append(order)

    held_symbols = {p.symbol for p in positions}
    rows: list[SymbolCoverage] = []
    for position in sorted(positions, key=lambda p: p.symbol):
        mine = [o for o in live.get(position.symbol, []) if _protects(o, position.side)]
        maybe = [
            o for o in unknown.get(position.symbol, []) if _protects(o, position.side)
        ]

        protective_qty = sum(o.remaining_qty for o in mine)
        indeterminate = sum(o.remaining_qty for o in maybe)

        # Excess is measured against live protection only. An ambiguous order is
        # not evidence of over-protection any more than it is of protection.
        excess = max(0.0, protective_qty - position.qty)
        # Indeterminate quantity cannot also be counted as naked, and cannot
        # exceed what is left after live protection.
        unprotected = max(0.0, position.qty - protective_qty)
        indeterminate = min(indeterminate, unprotected)
        naked = unprotected - indeterminate

        rows.append(
            SymbolCoverage(
                symbol=position.symbol,
                position_qty=position.qty,
                position_side=position.side,
                protected_qty=protective_qty,
                indeterminate_qty=indeterminate,
                naked_qty=naked if naked > QTY_EPSILON else 0.0,
                excess_qty=excess if excess > QTY_EPSILON else 0.0,
                stop_prices=tuple(
                    o.stop_price for o in mine if o.stop_price is not None
                ),
                stop_types=tuple(sorted({o.order_type for o in mine})),
            )
        )

    orphans = sorted(
        symbol
        for symbol, group in live.items()
        if symbol not in held_symbols
        and any(o.order_type in PROTECTIVE_TYPES for o in group)
    )
    return CoverageReport(symbols=tuple(rows), orphan_stop_symbols=tuple(orphans))
