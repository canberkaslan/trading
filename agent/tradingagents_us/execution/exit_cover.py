"""Which working sells are this system's own time exit: cover for the coverage check.

A time exit (`protected_close`) cancels every stop on a long lot and queues one
market sell for all of it at the next open, under the exit's stamp
(`executor.derive_exit_client_order_id`). That night the lot has no stop at
all, and `risk.stop_coverage` called every share of it naked: on 2026-10-05 the
coverage check at the end of the run paged "NAKED: 32 of 278 shares ... GOOGL"
over a lot our own exit had reserved whole, with the market shut. The night
after, the lot sold, it announced a "recovery". Every time-exit night did both.

The shares were covered in the sense the system cares about. The exit reserves
them, so the broker refuses any other sell (held_for_orders); outside the
session nothing can trigger; and at the open the lot is sold. The time-exit
model check already holds a lot "covered by a stop or by a working exit of
ours" (invariant I2). This module says which orders are that exit, so the
accounting can count them as `exiting`: never as protection, and never by order
type alone.

`own_time_exit` asks, in order, each cheaper than the next:

  * a SELL at MARKET, the only exit `protected_close` sends;
  * live (`stop_coverage.LIVE_STATUSES`). Refused, cancelled, expired, done
    for the day: a day order in any of those sells nothing more, and the lot
    has neither stop nor exit. Being cancelled or suspended: not the lot's
    way out at the open either;
  * under this symbol's time-exit stamp, or a retry of it
    (`protected_close.exit_stamp_date`). A market sell from a person, or from
    anything else, reserves the shares just the same, but nothing here knows
    what it is or when it goes away;
  * met by no open since it was sent, on the broker's calendar (`opened`, from
    `protected_close.opened_after`). An exit sent after the close queues for
    the next open, and one an open has met and that still reads as working is
    not one to vouch for. Not the stamp's date: a run past 00:00 UTC stamps
    the date of the run after it, and an exit queued over an exchange holiday
    still works under an earlier date's stamp, as the close leaves it to.

The calendar is read only for an order that passes the rest, so a book with no
exit reads none. Where it cannot be read, every exit counts as met by an open
(`opened_after`): the lot pages, where the other way a dead exit would pass in
silence. The rest of the book's rules (only a long, only up to what the exit has
left to sell, only what no stop covers) are the accounting's, in
`stop_coverage.coverage`.

Read-only, and pure but for the calendar read `opened` makes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime

from ..dataflows.alpaca_broker import Order, Position
from ..risk.stop_coverage import LIVE_STATUSES, OrderView, PositionView
from .protected_close import exit_stamp_date

#: Whether a regular session has opened after a given instant; see
#: `protected_close.opened_after`.
Opened = Callable[[datetime], bool]


def own_time_exit(order: Order, opened: Opened | None) -> bool:
    """Whether `order` is this system's time exit of its lot, working, and met by no open yet.

    `opened` None means no calendar was read: no exit is vouched for.
    """
    return (
        opened is not None
        and order.side.lower() == "sell"
        and order.order_type.lower() == "market"
        and order.status.lower() in LIVE_STATUSES
        and exit_stamp_date(order.client_order_id, order.symbol) is not None
        and not opened(order.submitted_at)
    )


def order_views(orders: Iterable[Order], opened: Opened | None = None) -> list[OrderView]:
    """Broker orders, already flattened, as the accounting's `OrderView`s.

    With `opened`, each of our own working time exits is flagged as such
    (`OrderView.own_exit`). Without it, none is, and a lot whose only sell is
    its exit reads as naked: the accounting the position pass keeps.
    """
    return [
        OrderView(
            symbol=o.symbol,
            side=o.side.lower(),
            order_type=o.order_type.lower(),
            status=o.status.lower(),
            remaining_qty=max(0.0, o.qty - o.filled_qty),
            stop_price=o.stop_price,
            own_exit=own_time_exit(o, opened),
        )
        for o in orders
    ]


def position_views(positions: Iterable[Position]) -> list[PositionView]:
    """Broker positions as the accounting's `PositionView`s: unsigned, with their side.

    Alpaca reports a short with a negative quantity. Read as a long, it summed
    to less than nothing and no share of it was ever naked; a sell, stop or
    exit, was counted as protecting it. A short is protected by a buy stop only.
    """
    return [PositionView(p.symbol, abs(p.qty), p.side.lower()) for p in positions]
