"""FIFO round-trip matching — turns a fill stream into realized P&L.

The system could report unrealized P&L from day one (Alpaca hands it over per
position) but had no idea whether it *wins*: a book that is up 5% says nothing
about how many trades paid and how many bled, and neither win rate nor
expectancy can be computed from an open position. Those need round trips, and
a round trip only exists once you pair each exit against the entries it closed.

Everything here is pure: fills in, closed trades out. No broker calls, no DB —
so the matching rules are unit-testable against hand-built fill streams, which
matters because a P&L ledger that is quietly wrong is worse than none.

Rules:
  - FIFO. The oldest open lot is closed first (US tax-lot default, and what
    Alpaca's own average-cost display assumes).
  - Shorts are first-class. A sell with no inventory opens a SHORT lot; the
    covering buy closes it. P&L flips sign accordingly.
  - Flips are split. A fill that crosses through zero (sell 30 while long 10)
    closes the 10 and opens a 20-share short — one fill, two effects.
  - Partial fills stay separate. Each slice is its own lot at its own price;
    averaging them first would lose the price a specific exit actually got.

Deliberately NOT modeled: fees and commissions (Alpaca charges none on the
paper lane and reports no fee on FILL activities — the day a live account
carries them, they belong here as a per-leg deduction, not smeared over the
average), and corporate actions (a split re-prices open lots and would silently
corrupt every round trip crossing it; `reconcile_fills` cannot see them, so a
split on a held name needs a manual re-baseline).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

# Share quantities are floats because Alpaca supports fractional shares.
# Anything smaller than this is float noise from repeated subtraction, not a
# real residual lot — treat the lot as fully consumed.
QTY_EPSILON = 1e-9


@dataclass(frozen=True)
class Fill:
    """One execution slice. Mirrors AlpacaClient.FillActivity, decoupled so the
    matcher can be tested (and reused) without the broker adapter."""

    id: str
    symbol: str
    side: str  # "buy" | "sell"
    qty: float
    price: float
    transaction_time: datetime


@dataclass(frozen=True)
class ClosedTrade:
    """A completed round trip: one entry lot paired with one exit slice."""

    trade_id: str
    symbol: str
    direction: str  # "LONG" | "SHORT"
    quantity: float
    entry_price: float
    exit_price: float
    opened_at_utc: datetime
    closed_at_utc: datetime
    realized_pnl: float
    realized_pnl_pct: float
    holding_days: float
    open_activity_id: str
    close_activity_id: str


@dataclass
class _Lot:
    """An open entry slice with the quantity still unclosed."""

    activity_id: str
    qty: float
    price: float
    opened_at: datetime


@dataclass
class OpenLot:
    """Inventory left over after matching — the position still on the book."""

    symbol: str
    direction: str
    quantity: float
    price: float
    opened_at_utc: datetime
    activity_id: str


@dataclass
class ReconcileResult:
    closed: list[ClosedTrade] = field(default_factory=list)
    open_lots: list[OpenLot] = field(default_factory=list)


def _trade_id(symbol: str, open_id: str, close_id: str) -> str:
    """Stable id for a round trip.

    Derived from the two activity ids rather than a random UUID so a full
    replay of the same fill history produces the same rows — the reconciler
    can re-run hourly and converge instead of duplicating the ledger. An
    (entry slice, exit slice) pair is matched at most once, so the pair alone
    is unique; quantity is left out on purpose, so a re-run that matches the
    same pair for a different size updates the row instead of orphaning it.
    """
    digest = hashlib.sha1(f"{symbol}|{open_id}|{close_id}".encode()).hexdigest()
    return digest[:32]


def reconcile_fills(fills: list[Fill]) -> ReconcileResult:
    """Match a fill stream into closed round trips + remaining open lots.

    Fills are grouped per symbol and replayed in time order. Ties on
    `transaction_time` (same-second partials of one order) fall back to the
    activity id, which Alpaca issues in execution order — without that the
    lot order would depend on dict iteration and the ledger would not be
    reproducible.
    """
    by_symbol: dict[str, list[Fill]] = {}
    for f in fills:
        by_symbol.setdefault(f.symbol, []).append(f)

    result = ReconcileResult()
    for symbol in sorted(by_symbol):
        ordered = sorted(by_symbol[symbol], key=lambda f: (f.transaction_time, f.id))
        closed, open_lots = _reconcile_symbol(symbol, ordered)
        result.closed.extend(closed)
        result.open_lots.extend(open_lots)

    # One exit can close several lots at the same instant, so closed_at alone
    # does not order them. Tie-break on the ENTRY (time, then activity id) to
    # keep the ledger in FIFO order — falling back to trade_id would sort those
    # rows by hash, i.e. arbitrarily.
    result.closed.sort(
        key=lambda t: (t.closed_at_utc, t.opened_at_utc, t.open_activity_id)
    )
    return result


def _reconcile_symbol(
    symbol: str, fills: list[Fill]
) -> tuple[list[ClosedTrade], list[OpenLot]]:
    closed: list[ClosedTrade] = []
    lots: list[_Lot] = []
    pos_dir = 0  # +1 long inventory, -1 short inventory, 0 flat

    for fill in fills:
        fill_dir = 1 if fill.side == "buy" else -1
        remaining = abs(fill.qty)

        # Close against opposing inventory first, oldest lot first.
        # PLR1714 waived below: "holds no inventory" and "holds inventory on
        # the same side as this fill" are different facts about the book, and
        # collapsing them into one membership test reads as if they were one.
        while (
            remaining > QTY_EPSILON and pos_dir != 0 and fill_dir != pos_dir and lots  # noqa: PLR1714
        ):
            lot = lots[0]
            matched = min(lot.qty, remaining)

            # Long: profit when the exit prints above entry. Short: inverted.
            pnl = (fill.price - lot.price) * matched * pos_dir
            cost = lot.price * matched
            holding = (fill.transaction_time - lot.opened_at).total_seconds() / 86400.0

            closed.append(
                ClosedTrade(
                    trade_id=_trade_id(symbol, lot.activity_id, fill.id),
                    symbol=symbol,
                    direction="LONG" if pos_dir > 0 else "SHORT",
                    quantity=matched,
                    entry_price=lot.price,
                    exit_price=fill.price,
                    opened_at_utc=lot.opened_at,
                    closed_at_utc=fill.transaction_time,
                    realized_pnl=pnl,
                    # Return on the capital the entry tied up. A zero entry
                    # price is not a 100% winner, it is missing data — 0.0.
                    realized_pnl_pct=(pnl / cost) if cost > 0 else 0.0,
                    holding_days=max(holding, 0.0),
                    open_activity_id=lot.activity_id,
                    close_activity_id=fill.id,
                )
            )

            lot.qty -= matched
            remaining -= matched
            if lot.qty <= QTY_EPSILON:
                lots.pop(0)
            if not lots:
                pos_dir = 0

        # Whatever the fill did not close, it opens — this is the flip case:
        # the same fill can close a long and open a short.
        if remaining > QTY_EPSILON:
            lots.append(
                _Lot(
                    activity_id=fill.id,
                    qty=remaining,
                    price=fill.price,
                    opened_at=fill.transaction_time,
                )
            )
            pos_dir = fill_dir

    open_lots = [
        OpenLot(
            symbol=symbol,
            direction="LONG" if pos_dir > 0 else "SHORT",
            quantity=lot.qty,
            price=lot.price,
            opened_at_utc=lot.opened_at,
            activity_id=lot.activity_id,
        )
        for lot in lots
    ]
    return closed, open_lots


@dataclass(frozen=True)
class TradeStats:
    """Round-trip performance. Every field is derived from closed trades only —
    open positions are excluded by definition, so this never flatters the
    record by counting a paper gain that has not been taken."""

    trades: int
    wins: int
    losses: int
    scratches: int
    win_rate: float
    gross_profit: float
    gross_loss: float  # reported positive
    net_pnl: float
    avg_win: float
    avg_loss: float  # reported positive
    profit_factor: float | None
    expectancy: float
    avg_holding_days: float
    best_trade: float
    worst_trade: float


def compute_stats(trades: list[ClosedTrade]) -> TradeStats:
    """Win rate / expectancy / profit factor over a set of round trips.

    Definitions worth pinning down, because these terms get used loosely:
      - `win_rate` is wins / ALL closed trades, so scratches (exactly flat)
        dilute it rather than being quietly dropped from the denominator —
        an inflated win rate is exactly the kind of self-flattery this ledger
        exists to prevent. Scratches are reported separately.
      - `expectancy` is expected dollars per round trip, i.e. net P&L / trades.
        That is algebraically win_rate*avg_win − loss_rate*avg_loss; computing
        it from the totals avoids compounding rounding across three means.
      - `profit_factor` is None, not infinity, when nothing lost yet. A run of
        pure winners is an undefined ratio and a small sample, and rendering
        it as "∞" reads as proof of an edge.
    """
    if not trades:
        return TradeStats(
            trades=0, wins=0, losses=0, scratches=0, win_rate=0.0,
            gross_profit=0.0, gross_loss=0.0, net_pnl=0.0,
            avg_win=0.0, avg_loss=0.0, profit_factor=None, expectancy=0.0,
            avg_holding_days=0.0, best_trade=0.0, worst_trade=0.0,
        )

    wins = [t.realized_pnl for t in trades if t.realized_pnl > 0]
    losses = [t.realized_pnl for t in trades if t.realized_pnl < 0]
    scratches = len(trades) - len(wins) - len(losses)

    gross_profit = sum(wins)
    gross_loss = -sum(losses)  # positive
    net = gross_profit - gross_loss

    return TradeStats(
        trades=len(trades),
        wins=len(wins),
        losses=len(losses),
        scratches=scratches,
        win_rate=len(wins) / len(trades),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net,
        avg_win=(gross_profit / len(wins)) if wins else 0.0,
        avg_loss=(gross_loss / len(losses)) if losses else 0.0,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else None,
        expectancy=net / len(trades),
        avg_holding_days=sum(t.holding_days for t in trades) / len(trades),
        best_trade=max(t.realized_pnl for t in trades),
        worst_trade=min(t.realized_pnl for t in trades),
    )
