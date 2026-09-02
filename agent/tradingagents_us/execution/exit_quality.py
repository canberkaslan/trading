"""Attribute realized P&L to the thing that actually closed each position.

The realized ledger has been read, since it was built on 2026-08-09, as one
number: 32 round trips, −$950 net, 25% win rate, profit factor 0.13. Read that
way it says the strategy's exit discipline loses money, and it has sat in
`task_plan.md` as the root cause behind the honest NO-GO. But a round trip's
P&L says nothing about *why* the position was closed, and the account's exits
did not all come from the same place:

  - a protective **stop** leg firing,
  - a **take-profit** limit leg filling,
  - a **decision sell** the agent submitted through `trade.py`,
  - a **flatten** — a market sell submitted outside the agent entirely, which
    on this account is the 2026-06-24 cleanup of the accumulation bug (the
    duplicate shares from buying the same ticker three times a day, dumped at
    market in one go).

Those are four different claims about the system, and averaging them into one
expectancy answers none of them. This module splits the ledger by exit path so
each can be judged on its own record.

Classification is by *provenance*, from the broker's own order record — never
by guessing from the P&L. `trade.py` stamps every order it submits with a
`tr-` client id, so a market sell carrying a broker-generated UUID demonstrably
did not come from the agent's execution path. What it does not tell us is
intent: it says "not the agent", not "a human meant to flatten". That is why
the class is named for what was observed.

Everything here is pure. The caller resolves fills to orders against the broker
and hands the result in, so this can be tested without a network.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from tradingagents_us.execution.reconcile import ClosedTrade

ExitClass = Literal["stop", "take_profit", "decision_sell", "flatten", "unknown"]

#: Order types that mean "a protective level was reached", whatever the venue
#: calls them. `trailing_stop` is here for completeness; nothing submits one yet.
_STOP_TYPES = frozenset({"stop", "stop_limit", "trailing_stop"})

#: `trade.py` derives every client order id it submits from this prefix
#: (`derive_client_order_id`). Its absence is what separates an agent order
#: from one submitted by hand.
AGENT_CLIENT_ID_PREFIX = "tr-"

#: Order of report rows: the agent's own exits first, then what happened to it.
CLASS_ORDER: tuple[ExitClass, ...] = (
    "take_profit",
    "stop",
    "decision_sell",
    "flatten",
    "unknown",
)

CLASS_LABELS: dict[str, str] = {
    "take_profit": "take-profit leg",
    "stop": "protective stop",
    "decision_sell": "agent decision sell",
    "flatten": "flatten (outside the agent)",
    "unknown": "unknown (order pruned)",
    "strategy": "strategy exits only",
}

#: The classes that represent the strategy closing its own position. A flatten
#: is an operator action and an unknown is missing evidence; scoring either as
#: strategy performance is the mistake this module exists to stop.
STRATEGY_CLASSES: frozenset[ExitClass] = frozenset({"take_profit", "stop", "decision_sell"})


@dataclass(frozen=True)
class ExitOrder:
    """The broker order behind a closing fill, flattened to what classification needs.

    Deliberately not the adapter's `Order`: this module must stay importable
    (and testable) without httpx, and the four fields below are the whole
    evidence base. `is_leg` distinguishes a bracket child from a top-level
    order, which is the only thing separating a take-profit leg from an
    ordinary limit sell.
    """

    order_id: str
    client_order_id: str
    order_type: str
    is_leg: bool
    trigger_price: float | None = None


def classify_exit(order: ExitOrder | None) -> ExitClass:
    """What closed this position, from the order record alone.

    `None` means the fill's order could not be found — Alpaca prunes order
    history while the activities feed is permanent, so an old exit can lose its
    order without losing its fill. That is reported as `unknown` rather than
    folded into a neighbouring class: an exit we cannot attribute is missing
    evidence, and quietly filing it under "decision sell" would inflate exactly
    the number this module is trying to measure honestly.
    """
    if order is None:
        return "unknown"

    order_type = (order.order_type or "").strip().lower()
    if order_type in _STOP_TYPES:
        return "stop"
    if order_type in {"limit", "limit_maker"} and order.is_leg:
        return "take_profit"
    if (order.client_order_id or "").startswith(AGENT_CLIENT_ID_PREFIX):
        return "decision_sell"
    if order.is_leg:
        # A bracket child that is neither stop nor limit should not exist. Say
        # so instead of filing it under an operator action it did not come from.
        return "unknown"
    return "flatten"


@dataclass(frozen=True)
class ExitBucket:
    """One exit path's record. `avg_pnl` is that path's expectancy per trade."""

    # A plain `str`, not `ExitClass`: `strategy_bucket` reports a roll-up that is
    # not one of the exit paths, and widening the classifier's return type to
    # carry it would let a roll-up label escape into a per-trade attribution.
    exit_class: str
    trades: int
    wins: int
    losses: int
    net_pnl: float
    gross_profit: float
    gross_loss: float
    avg_pnl: float
    win_rate: float
    avg_holding_days: float

    @property
    def label(self) -> str:
        return CLASS_LABELS.get(self.exit_class, self.exit_class)


@dataclass(frozen=True)
class AttributedTrade:
    """A closed round trip with the exit path that produced it."""

    trade: ClosedTrade
    exit_class: ExitClass
    order: ExitOrder | None


def attribute(
    trades: list[ClosedTrade], exits: Mapping[str, ExitOrder]
) -> list[AttributedTrade]:
    """Pair every closed trade with the order behind its closing fill.

    `exits` is keyed by fill *activity* id, not order id: one order fills in
    several slices and FIFO matching pairs entry lots against slices, so the
    activity is the only key that identifies which exit a given round trip
    belongs to.
    """
    return [
        AttributedTrade(
            trade=t,
            exit_class=classify_exit(exits.get(t.close_activity_id)),
            order=exits.get(t.close_activity_id),
        )
        for t in trades
    ]


def _bucket(exit_class: str, rows: list[AttributedTrade]) -> ExitBucket:
    pnls = [r.trade.realized_pnl for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(rows)
    return ExitBucket(
        exit_class=exit_class,
        trades=n,
        wins=len(wins),
        losses=len(losses),
        net_pnl=sum(pnls),
        gross_profit=sum(wins),
        gross_loss=abs(sum(losses)),
        avg_pnl=sum(pnls) / n if n else 0.0,
        # Scratches (exactly zero P&L) are in the denominator on purpose: a
        # round trip that made nothing is a trade that did not win.
        win_rate=len(wins) / n if n else 0.0,
        avg_holding_days=sum(r.trade.holding_days for r in rows) / n if n else 0.0,
    )


def bucket_by_exit(attributed: list[AttributedTrade]) -> list[ExitBucket]:
    """Per-exit-path records, in `CLASS_ORDER`. Empty classes are left out."""
    grouped: dict[ExitClass, list[AttributedTrade]] = {}
    for row in attributed:
        grouped.setdefault(row.exit_class, []).append(row)
    return [_bucket(c, grouped[c]) for c in CLASS_ORDER if c in grouped]


def strategy_bucket(attributed: list[AttributedTrade]) -> ExitBucket:
    """The ledger with operator flattens and unattributable exits removed.

    This is the number that answers "does the agent's own exit discipline make
    money?" — the question the blended expectancy was being read as answering.
    It is a narrower claim on a smaller sample, and it should be quoted with the
    trade count attached, never on its own.
    """
    return _bucket("strategy", [r for r in attributed if r.exit_class in STRATEGY_CLASSES])
