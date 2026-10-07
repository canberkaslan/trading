"""An Alpaca stand-in for the position pass: records every call, reserves shares.

Like the real broker, a live sell order reserves the shares it could sell, and a
new sell for more than is left unreserved is refused (403, held_for_orders). A
close that forgets to release its stop therefore fails here the way it fails
live, and a stop left standing beside an exit shows up as a refused sell rather
than passing unnoticed.

Every failure point of a protected close has a knob, keyed on symbol or order
id, so a test can break exactly one step and look at what reached the broker.

Two knobs model what makes the real broker hard to read. A request whose reply
is lost can still be in flight: `sell_in_flight` lands a timed-out exit a set
number of broker calls later, before that call takes effect. A cancel is not a
cancelled order: `slow_cancel` keeps an order in an interim status for a set
number of reads, and `oco` cancels a take-profit's stop with it the way a
bracket does. By default a sell on a flat symbol is refused, as a cash account
would; `allow_short` accepts it as a margin account does, as a short sale, which
is the double-covered end state these tests exist to rule out.

The same broker loses more than one reply. `stop_reply_error` places every stop
it is sent and then raises instead of answering (a timeout, or a 5xx from a
gateway in front of a backend that took it), and `throttle_own_cancels` answers
the first DELETE of each stop it placed with a 429 that changes nothing.
`stop_in_flight` is `sell_in_flight` for a stop: its POST times out and it lands
a set number of broker calls later, checked against the book as it is then.

`market_open` is the clock's answer, closed by default: the daily run is after
the close, and protected_close acts only then. `minutes_to_open` is how far off
the next open is, from `now` (the clock's time; the wall clock's by default,
which is what the orders a test builds carry). The calendar has a session every
day, each opening a whole number of days before that next open: by default the
last one opened nine hours before `now`, as the 22:30 UTC run finds 13:30.
`throttle_cancels` answers the first DELETEs of an order with 429s that change
nothing, as many as it says.

An `oco` pair is one reservation, as Alpaca's held_for_orders has it: both legs
can sell the same shares, and only one of them ever will. The nested listing
returns the pair as the take-profit with the stop as its leg, the shape of an
Alpaca OCO order, so a caller can tell which stop goes with which take-profit.
`nests_oco=False` lists them flat, as a caller that cannot see the pairing does.

`lists_behind` is Alpaca's order listing right after a write: eventually
consistent. After a write, the next `lists_behind` listings serve the book as
it stood before the writes the listing has not caught up with (`behind`),
while get_order, the client-id lookup and the holding are current; the listing
after them is current, and stays so until the next write. On 2026-10-05
GOOGL's listing still had three sells working seconds after get_order had read
each one cancelled, and showed the one seller seconds later.
"""

from __future__ import annotations

import dataclasses
import itertools
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta

import httpx

from tradingagents_us.dataflows.alpaca_broker import (
    AlpacaRequestError,
    Clock,
    FillActivity,
    Order,
    Position,
    Session,
)

#: Calls that change something at the broker. Everything else is a read.
WRITES = (
    "close_position",
    "submit_order",
    "replace_order",
    "cancel_order",
    "close_all_positions",
)

#: Order statuses that still reserve shares (Alpaca holds shares for these).
_RESERVING = frozenset(
    {"new", "accepted", "held", "pending_new", "partially_filled", "pending_cancel"}
)


def _broker_error(path: str, status: int, body: str) -> AlpacaRequestError:
    method, _, route = path.partition(" ")
    return AlpacaRequestError(method, route, status, body)


class FakeBroker:
    def __init__(
        self,
        positions: Iterable[Position],
        orders: Iterable[Order] = (),
        fills: Iterable[FillActivity] = (),
        *,
        refuse_sell: Iterable[str] = (),
        reject_sell: Iterable[str] = (),
        lose_sell_reply: Iterable[str] = (),
        exit_status: str = "filled",
        cancel_refused: Iterable[str] = (),
        cancel_stuck: Iterable[str] = (),
        fill_on_cancel: Iterable[str] = (),
        refuse_stop: Iterable[str] = (),
        position_unreadable: Iterable[str] = (),
        lookup_fails: bool = False,
        lookup_fails_after_sell: bool = False,
        sell_in_flight: Mapping[str, int] | None = None,
        slow_cancel: Mapping[str, tuple[str, int]] | None = None,
        oco: Mapping[str, str] | None = None,
        cancel_reply_lost: Iterable[str] = (),
        allow_short: bool = False,
        nests_oco: bool = True,
        stop_reply_error: Exception | None = None,
        throttle_own_cancels: bool = False,
        stop_in_flight: Mapping[str, int] | None = None,
        market_open: bool = False,
        minutes_to_open: float = 15 * 60,
        throttle_cancels: Mapping[str, int] | None = None,
        now: datetime | None = None,
        lists_behind: int = 0,
    ) -> None:
        self.positions: dict[str, Position] = {p.symbol: p for p in positions}
        self.orders: dict[str, Order] = {o.id: o for o in orders}
        self.fills = list(fills)
        self.refuse_sell = set(refuse_sell)
        self.reject_sell = set(reject_sell)
        self.lose_sell_reply = set(lose_sell_reply)
        self.exit_status = exit_status
        self.cancel_refused = set(cancel_refused)
        self.cancel_stuck = set(cancel_stuck)
        self.fill_on_cancel = set(fill_on_cancel)
        self.refuse_stop = set(refuse_stop)
        self.position_unreadable = set(position_unreadable)
        self.lookup_fails = lookup_fails
        self.lookup_fails_after_sell = lookup_fails_after_sell
        #: symbol -> broker calls after a timed-out exit POST before it lands.
        self.sell_in_flight = dict(sell_in_flight or {})
        #: order id -> (status it reads meanwhile, reads before `canceled`).
        self.slow_cancel = dict(slow_cancel or {})
        #: order id -> the sibling a landed cancel takes with it.
        self.oco = dict(oco or {})
        self.cancel_reply_lost = set(cancel_reply_lost)
        self.allow_short = allow_short
        self.nests_oco = nests_oco
        self.stop_reply_error = stop_reply_error
        self.throttle_own_cancels = throttle_own_cancels
        #: symbol -> broker calls after a timed-out stop POST before it lands.
        self.stop_in_flight = dict(stop_in_flight or {})
        self.market_open = market_open
        self.minutes_to_open = minutes_to_open
        self.now = now or datetime.now(UTC)
        #: order id -> 429s still to answer its DELETEs with.
        self.throttle_cancels = dict(throttle_cancels or {})
        self.lists_behind = lists_behind
        #: What a listing behind the book serves, and how many more will.
        self.behind: dict[str, Order] = {}
        self._behind_left = 0
        self._throttled: set[str] = set()
        self._in_flight: list[list] = []
        self._settling: dict[str, int] = {}
        self._sold = False
        self.calls: list[tuple] = []
        #: Orders this broker created, in order: closes and submitted orders.
        self.created: list[Order] = []
        self._ids = itertools.count(1)

    # ---- plumbing -----------------------------------------------------------

    def __enter__(self) -> FakeBroker:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @property
    def writes(self) -> list[tuple]:
        return [c for c in self.calls if c[0] in WRITES]

    def live_sells(self, symbol: str) -> list[Order]:
        return [
            o
            for o in self.orders.values()
            if o.symbol == symbol and o.side == "sell" and o.status in _RESERVING
        ]

    def _reserved(self, symbol: str) -> float:
        """Shares the symbol's live sells hold back, an OCO pair counted once."""
        live = {o.id: o for o in self.live_sells(symbol)}
        by_group: dict[str, float] = {}
        for oid, order in live.items():
            key = self._oco_parent.get(oid, oid)
            by_group[key] = max(by_group.get(key, 0.0), order.qty - order.filled_qty)
        return sum(by_group.values())

    @property
    def _oco_parent(self) -> dict[str, str]:
        """Each order in an OCO pair -> the take-profit that heads the pair."""
        return {**{tp: tp for tp in self.oco}, **{sl: tp for tp, sl in self.oco.items()}}

    def _sell_shares(self, symbol: str, qty: float) -> None:
        pos = self.positions[symbol]
        left = pos.qty - qty
        if left <= 1e-9:
            del self.positions[symbol]
        else:
            self.positions[symbol] = dataclasses.replace(pos, qty=left, market_value=left * 100.5)

    def _new_order(self, symbol: str, qty: float, order_type: str, coid: str, status: str,
                   stop_price: float | None = None) -> Order:
        filled = qty if status == "filled" else 0.0
        order = Order(
            id=f"{order_type}-{symbol}-{next(self._ids)}",
            client_order_id=coid,
            symbol=symbol,
            side="sell",
            qty=qty,
            filled_qty=filled,
            order_type=order_type,  # type: ignore[arg-type]
            status=status,
            # Each later than the last, and on the fake's clock.
            submitted_at=self.now + timedelta(milliseconds=len(self.created) + 1),
            filled_avg_price=100.5 if filled else None,
            stop_price=stop_price,
        )
        self.orders[order.id] = order
        self.created.append(order)
        if filled:
            self._sell_shares(symbol, filled)
        return order

    def _record(self, *call: object) -> None:
        """Log the call, then let time pass: an in-flight exit may land first."""
        self.calls.append(call)
        if call[0] in WRITES and self.lists_behind:
            if not self._behind_left:
                self.behind = dict(self.orders)
            self._behind_left = self.lists_behind
        for pending in list(self._in_flight):
            pending[-1] -= 1
            if pending[-1] <= 0:
                self._in_flight.remove(pending)
                self._land(*pending[:-1])

    def _land(self, symbol: str, qty: float, coid: str, stop_price: float | None) -> None:
        """The broker takes a POST whose reply the client never got.

        Checked against the book as it is when it lands, like the real one: if
        the shares are reserved by then, it is refused and nothing exists. A
        stop (`stop_price` set) lands working; an exit as `exit_status` says.
        """
        pos = self.positions.get(symbol)
        available = (pos.qty if pos else 0.0) - self._reserved(symbol)
        if qty > available + 1e-9 and not (self.allow_short and pos is None):
            return
        if stop_price is not None:
            self._new_order(symbol, qty, "stop", coid, "new", stop_price)
            return
        status = "rejected" if symbol in self.reject_sell else self.exit_status
        self._new_order(symbol, qty, "market", coid, status)

    def land_in_flight(self) -> None:
        """Land every POST still in flight now, as the broker eventually would."""
        pending, self._in_flight = self._in_flight, []
        for symbol, qty, coid, stop_price, _ in pending:
            self._land(symbol, qty, coid, stop_price)

    def _start_cancel(self, order_id: str) -> None:
        order = self.orders[order_id]
        if order_id in self._settling:
            # Already on its way: a second DELETE does not start the clock again.
            return
        if order_id in self.slow_cancel:
            interim, reads = self.slow_cancel[order_id]
            self.orders[order_id] = dataclasses.replace(order, status=interim)
            self._settling[order_id] = reads
        else:
            self.orders[order_id] = dataclasses.replace(order, status="canceled")
        sibling = self.oco.get(order_id)
        if sibling is not None and self.orders[sibling].status in _RESERVING:
            self._start_cancel(sibling)

    # ---- reads --------------------------------------------------------------

    @property
    def next_open(self) -> datetime:
        return self.now + timedelta(minutes=self.minutes_to_open)

    def clock(self) -> Clock:
        self._record("clock")
        return Clock(
            is_open=self.market_open,
            timestamp=self.now.isoformat(),
            next_open=self.next_open.isoformat(),
            next_close=(self.now + timedelta(hours=21, minutes=30)).isoformat(),
        )

    def calendar(self, start: date, end: date) -> list[Session]:
        """A session every day, opening at the next open's time of day, 6.5 hours long."""
        self._record("calendar", start, end)
        sessions = []
        for k in range(-1, (self.next_open.date() - start).days + 2):
            opens = self.next_open - timedelta(days=k)
            if start <= opens.date() <= end:
                sessions.append(Session(opens.date(), opens, opens + timedelta(hours=6.5)))
        return sorted(sessions, key=lambda s: s.open)

    def list_positions(self) -> list[Position]:
        self._record("list_positions")
        return list(self.positions.values())

    def get_position(self, symbol: str) -> Position | None:
        self._record("get_position", symbol)
        if symbol in self.position_unreadable:
            raise httpx.ConnectError("positions endpoint unreachable")
        return self.positions.get(symbol)

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False):
        self._record("list_orders", status)
        orders = self.orders
        if self._behind_left:
            self._behind_left -= 1
            orders = self.behind
        if not (nested and self.nests_oco):
            return list(orders.values())
        legs = set(self.oco.values())
        return [
            dataclasses.replace(o, legs=(orders[self.oco[o.id]],)) if o.id in self.oco else o
            for o in orders.values()
            if o.id not in legs
        ]

    def get_order(self, order_id: str) -> Order:
        self._record("get_order", order_id)
        if order_id in self._settling:
            if self._settling[order_id] > 0:
                self._settling[order_id] -= 1
            else:
                del self._settling[order_id]
                self.orders[order_id] = dataclasses.replace(
                    self.orders[order_id], status="canceled"
                )
        return self.orders[order_id]

    def get_order_by_client_order_id(self, client_order_id: str) -> Order | None:
        self._record("get_order_by_client_order_id", client_order_id)
        if self.lookup_fails or (self.lookup_fails_after_sell and self._sold):
            raise httpx.ReadTimeout("order lookup timed out")
        return next(
            (o for o in self.orders.values() if o.client_order_id == client_order_id), None
        )

    def list_fill_activities(self) -> list[FillActivity]:
        self._record("list_fill_activities")
        return self.fills

    # ---- writes -------------------------------------------------------------

    def cancel_order(self, order_id: str) -> dict:
        self._record("cancel_order", order_id)
        order = self.orders[order_id]
        own = any(o.id == order_id for o in self.created)
        if self.throttle_own_cancels and own and order_id not in self._throttled:
            self._throttled.add(order_id)
            raise _broker_error(f"DELETE /orders/{order_id}", 429, "rate limit exceeded")
        if self.throttle_cancels.get(order_id, 0) > 0:
            self.throttle_cancels[order_id] -= 1
            raise _broker_error(f"DELETE /orders/{order_id}", 429, "rate limit exceeded")
        if order_id in self.cancel_refused or order.status == "pending_cancel":
            raise _broker_error(f"DELETE /orders/{order_id}", 422, "order is not cancelable")
        if order_id in self.cancel_stuck:
            self.orders[order_id] = dataclasses.replace(order, status="pending_cancel")
        elif order_id in self.fill_on_cancel:
            # The stop fired before the cancel landed: its shares are sold.
            left = order.qty - order.filled_qty
            self.orders[order_id] = dataclasses.replace(
                order, status="filled", filled_qty=order.qty
            )
            self._sell_shares(order.symbol, left)
        else:
            self._start_cancel(order_id)
        if order_id in self.cancel_reply_lost:
            raise httpx.ReadTimeout("reply lost after the cancel was accepted")
        return {}

    def submit_order(self, **kw) -> Order:
        self._record("submit_order", kw)
        symbol, qty, order_type = kw["symbol"], float(kw["qty"]), kw.get("order_type", "market")
        coid = kw.get("client_order_id") or f"broker-{next(self._ids)}"
        if any(o.client_order_id == coid for o in self.orders.values()):
            raise _broker_error("POST /orders", 422, "client_order_id must be unique")
        if order_type != "stop" and symbol in self.sell_in_flight:
            # The POST reached the broker but the reply is lost, and the broker
            # has not got round to it yet: nothing exists under the id so far.
            self._sold = True
            self._in_flight.append([symbol, qty, coid, None, self.sell_in_flight[symbol]])
            raise httpx.ReadTimeout("reply lost; the order is still in flight")
        if order_type == "stop" and symbol in self.stop_in_flight:
            stop_price = kw.get("stop_price")
            self._in_flight.append([symbol, qty, coid, stop_price, self.stop_in_flight[symbol]])
            raise httpx.ReadTimeout("reply lost; the stop is still in flight")
        pos = self.positions.get(symbol)
        available = (pos.qty if pos else 0.0) - self._reserved(symbol)
        short_sale = self.allow_short and pos is None
        if kw["side"] == "sell" and qty > available + 1e-9 and not short_sale:
            raise _broker_error(
                "POST /orders", 403,
                f'{{"code":40310000,"held_for_orders":"{self._reserved(symbol):g}",'
                f'"available":"{max(available, 0.0):g}"}}',
            )
        if order_type == "stop":
            if symbol in self.refuse_stop:
                raise _broker_error("POST /orders", 422, "stop refused")
            placed = self._new_order(symbol, qty, "stop", coid, "new", kw.get("stop_price"))
            if self.stop_reply_error is not None:
                raise self.stop_reply_error
            return placed
        self._sold = True
        if symbol in self.refuse_sell:
            raise _broker_error("POST /orders", 422, "market closed for this symbol")
        status = "rejected" if symbol in self.reject_sell else self.exit_status
        order = self._new_order(symbol, qty, order_type, coid, status)
        if symbol in self.lose_sell_reply:
            raise httpx.ReadTimeout("reply lost after the order was accepted")
        return order

    def close_position(self, symbol: str) -> dict:
        self._record("close_position", symbol)
        held = self._reserved(symbol)
        if held > 0:
            raise _broker_error(
                f"DELETE /positions/{symbol}", 403,
                f'{{"code":40310000,"held_for_orders":"{held:g}","available":"0"}}',
            )
        pos = self.positions[symbol]
        order = self._new_order(symbol, pos.qty, "market", "68c3c73e-broker-uuid", "filled")
        return {"id": order.id, "client_order_id": order.client_order_id}

    def replace_order(self, order_id: str, **kw) -> Order:
        self._record("replace_order", order_id, kw)
        old = self.orders[order_id]
        self.orders[order_id] = dataclasses.replace(old, status="replaced")
        new = dataclasses.replace(old, id=f"replaced-{order_id}", stop_price=kw.get("stop_price"))
        self.orders[new.id] = new
        return new

    def close_all_positions(self, cancel_orders: bool = True) -> list:
        self._record("close_all_positions", cancel_orders)
        return []
