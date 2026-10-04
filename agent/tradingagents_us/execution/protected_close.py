"""Close a long position that a stop protects, with no gap and no second seller.

A bare DELETE /positions/{symbol} cannot close a protected position here. Every
bracket entry, and every stop the position pass back-fills, is a GTC sell that
reserves all of the position's shares, and Alpaca refuses the close with
held_for_orders=qty, available=0. So the stop is released first, and that opens
the hazard stop_coverage.py and position_manager.py both name: two sells on one
long lot. A stop left standing beside the exit, or one that fires while the exit
also fills, sells the lot twice, and the second sale is a SHORT nothing protects.

The first version of this module set out to win every race in that sequence: a
stop firing mid-release, an exit landing after its lookups, a re-armed stop on a
book the exit had already emptied. Each fix opened another interleaving, and
three review rounds never converged. This one removes the races instead.

It acts only while the market is shut (`_market_shut`). Outside regular hours a
stop cannot trigger, a GTC take-profit cannot fill, and a market sell queues
for the open, so nothing this module did not send can change the holding while
it works. What is left in doubt is only its own requests: a cancel that has not
landed, a POST whose reply was lost. Neither is assumed. Each is settled by
reading the broker (the order's status, the client id it went under, the
listing, the holding), and where no read settles it the step stops there, says
so, and the next daily run reads the book again.

Between two sellers the broker is the arbiter. Alpaca refuses a sell for more
than the holding less what open sells reserve, so an exit and a stop sized off
the same holding cannot both stand: whichever lands first reserves the shares
and the other is refused, whatever order lost replies come back in. With no
fill possible meanwhile, the holding the exit was sized from is the holding at
the open, so the lot is sold once and never short.

One call is one step, each state decided off the broker as it is then:

  GUARD    the market is open, or opens within MIN_TIME_TO_OPEN: nothing sent.
  STAMP    an exit under today's stamp works or filled, or one of this lot's
           exits from an earlier trade date still works (queued for an open
           that has not come: an exchange holiday, a rerun past 00:00 UTC):
           nothing sent.
  BLOCKED  a sell on the symbol is neither working nor gone (pending_cancel,
           stopped, a leg whose pair ended), or works and is not one the
           release may cancel (a market sell: no bracket has one as a leg):
           nothing sent. It may still sell, or be about to stop protecting,
           and acting beside it is a guess.
  RELEASE  each working stop and take-profit is cancelled, a take-profit
           before its own stop, and confirmed by its status, with the DELETE
           sent again while it still works. One that will not go ends the
           release there (ABORT). Nothing else is ever released: an exit
           already queued is the lot's way out, and cancelling it trades that
           for a cancel that may stick and a resend.
  SELL     the holding read after the release, sold at market under the stamp.
  VERIFY   a sell the broker did not refuse is settled by its reply, or by
           looking its stamp up.
  RE-ARM   no working exit (refused, dead, never found, or ABORT): the stops
           that went are put back off a fresh read, each under a client id made
           from the stamp and the stop it replaces, for no more than they
           covered; the broker refuses any share another sell still reserves.
           An exit never found may still land; the re-armed stop then reserves
           the shares, and the broker refuses the exit.
  VERDICT  the book read again: the exit working or filled with nothing beside
           it; or still held, with every share a stop covered before covered
           again by a stop no cancel is on its way to; or how many are not, and
           what is in flight. An exit of this lot standing at the start
           counts as cover the verdict holds the end state to, as a stop does.

Every path past STAMP ends in the verdict, so an outcome is what the broker
shows afterwards, never what this module meant to do.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Literal

from ..dataflows.alpaca_broker import AlpacaClient, AlpacaRequestError, Order
from ..risk.stop_coverage import PROTECTIVE_TYPES, QTY_EPSILON, flatten_orders
from .executor import derive_exit_client_order_id

log = logging.getLogger(__name__)

#: Statuses in which an order works and can be cancelled. Narrower than
#: stop_coverage.LIVE_STATUSES on purpose, like manage_positions' own set: this
#: one gates a write. `pending_replace` and the rest are in doubt here.
WORKING_STATUSES = frozenset({"new", "accepted", "held", "pending_new", "partially_filled"})

#: Statuses in which a cancelled order can no longer sell a share. `stopped`
#: means a fill is guaranteed but may not be booked yet, `replaced` that a new
#: order took its place, `done_for_day` that it may resume: none is released.
RELEASED_STATUSES = frozenset({"canceled", "expired", "rejected", "filled"})

#: Dead orders a listing can safely ignore: released, or superseded by an order
#: that appears in the same listing under its own id.
_GONE_STATUSES = RELEASED_STATUSES | {"replaced"}

#: An exit in one of these never became a working sell.
FAILED_EXIT_STATUSES = frozenset({"canceled", "expired", "rejected"})

#: A leg in one of these ends its bracket or OCO pair, and the broker cancels
#: the other leg. Not `rejected` (it never stood) nor `replaced` (its successor
#: stands in its place).
PAIR_ENDING_STATUSES = frozenset({"filled", "canceled", "expired"})

#: Exit ids tried per symbol per day: the stamp, then `-r2` .. `-rN`. Alpaca
#: refuses a reused client_order_id even for a cancelled order, so a retry after
#: a failed exit needs a fresh suffix. The classifier reads only the reason.
MAX_EXIT_ATTEMPTS = 5

#: How long before the open a close may still start. A close takes a couple of
#: minutes at worst (every wait below, end to end), and it has to be over before
#: anything can fill: that is the whole safety argument.
MIN_TIME_TO_OPEN = timedelta(minutes=30)

#: How long to wait for a cancel to land: polls x interval, about five seconds.
#: Every cancel gets this window, refused or not: a refusal can come from an
#: order already on its way out (a bracket's stop, cancelled with its
#: take-profit, reads `pending_cancel` and refuses a second DELETE), and only
#: its status says which.
CANCEL_CONFIRM_POLLS = 10
CANCEL_CONFIRM_INTERVAL_S = 0.5

#: Then, for a cancel that may land (sent and not refused, or one its pair's
#: cancel takes along) and is still not terminal, a further wait that backs off
#: to about a minute in all. A cancel cannot be withdrawn, and when it lands the
#: shares it reserved have no stop, so it is waited for rather than given up on
#: at five seconds. Past this window the verdict counts it as on its way out.
CANCEL_SETTLE_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 15.0, 15.0, 15.0)

#: How long to look for an order whose submit did not come back clean: past
#: AlpacaClient's 15 s request timeout, so a POST the broker was still working
#: on when its reply was lost has usually shown up by the last lookup. Nothing
#: is decided on that, though: a re-arm after it is safe whether or not the
#: exit lands later, because the broker lets only one of the two stand.
EXIT_LOOKUP_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 8.0)

#: Attempts at the broker's truth (the symbol's orders, then its holding), and
#: the pauses between them.
TRUTH_READ_DELAYS_S = (0.0, 1.0, 2.0)

#: Alpaca caps a client_order_id's length (48, as executor.derive_client_order_id
#: notes). A re-arm id carries the stop it replaces as a short digest: the
#: stop's 36-character order id beside the stamp came to 71 characters.
MAX_CLIENT_ID_LEN = 48

CloseStatus = Literal[
    "exit_submitted",   # the stamped exit works or filled, and nothing stands beside it
    "already_closed",   # nothing held, and nothing standing on the flat book
    "already_exiting",  # this lot's exit (today's, or an earlier day's still queued) works
    "unchanged",        # still held, and every share a stop covered is covered again
    "deferred",         # the market is open or about to open: nothing sent
    "naked",            # shares a stop (or exit) covered have none, now or once a cancel lands
    "unknown",          # the end state could not be read, or is one that must not exist
]

#: The outcomes in which the position is closed or on its way out.
CLOSED_STATUSES: frozenset[str] = frozenset(
    {"exit_submitted", "already_closed", "already_exiting"}
)

Sleep = Callable[[float], None]

_CONFIRM_DELAYS_S = (0.0,) + (CANCEL_CONFIRM_INTERVAL_S,) * (CANCEL_CONFIRM_POLLS - 1)


@dataclass(frozen=True)
class CloseOutcome:
    ticker: str
    status: CloseStatus
    detail: str
    exit_order_id: str | None = None
    client_order_id: str | None = None
    released: tuple[str, ...] = ()
    rearmed: tuple[str, ...] = ()
    #: Earlier time exits of this lot that ended without selling it (refused,
    #: cancelled or expired at the open): the lot had neither stop nor exit
    #: from then until this run.
    missed_exits: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in CLOSED_STATUSES


@dataclass(frozen=True)
class _Book:
    """The symbol's sells as one listing has them, and which ones pair up.

    A bracket's take-profit and stop, like an OCO order's two legs, can each
    sell the same shares and only one of them ever will: cancelling one takes
    the other with it, and the broker holds the shares back once for the pair.
    The nested listing is the only place the pairing shows, so it is read
    there, before the listing is flattened.
    """

    #: Working sells, in the order they are to be released.
    live: tuple[Order, ...]
    #: Sells in a status that is neither working nor gone, or working in a
    #: pair whose other leg has ended: each may be on its way out.
    unclear: tuple[Order, ...]
    #: Order id -> id of the top-level order it was listed under: the pairing.
    group: Mapping[str, str]
    #: Every sell on the symbol the listing returned, whatever its status.
    records: Mapping[str, Order]

    #: Working sells the release never cancels: this lot's time exits, from
    #: any trade date, and any other sell no bracket or OCO pair can have as a
    #: leg (`_releasable`).
    kept: tuple[Order, ...] = ()
    #: Ids of this lot's time exits in the listing, whatever their status.
    exits: frozenset[str] = frozenset()

    @property
    def standing(self) -> tuple[Order, ...]:
        return (*self.live, *self.unclear, *self.kept)

    def _per_group(self, orders: Sequence[Order]) -> float:
        by_group: dict[str, float] = {}
        for order in orders:
            key = self.group.get(order.id, order.id)
            by_group[key] = max(by_group.get(key, 0.0), _remaining(order))
        return sum(by_group.values())

    @property
    def reserved(self) -> float:
        """Shares the standing sells hold back, each pair counted once."""
        return self._per_group(self.standing)

    @property
    def cover_standing(self) -> float:
        """Shares a standing stop or exit of this lot holds back, in doubt or not, each pair once.

        An exit counts with the stops: a lot whose queued exit goes, with no
        stop to take its place, has lost its way out as surely as its stop.
        """
        return self._per_group(
            [o for o in self.standing if _is_protective(o) or o.id in self.exits]
        )

    def queued_exit(self) -> Order | None:
        """This lot's exit from an earlier trade date, still working, if there is one."""
        queued = [o for o in self.kept if o.id in self.exits]
        return max(queued, key=lambda o: o.submitted_at) if queued else None

    def cover(self, in_flight: frozenset[str]) -> float:
        """Shares a working stop covers that no cancel is on its way to."""
        return self._per_group(
            [o for o in self.live if _is_protective(o) and o.id not in in_flight]
        )

    def stamped(self, stamp: str) -> Order | None:
        return next((o for o in self.records.values() if o.client_order_id == stamp), None)


_NO_BOOK = _Book((), (), {}, {})


@dataclass(frozen=True)
class _Truth:
    """The symbol as the broker has it now, read orders first and holding last.

    That order is what makes it safe to size a re-arm from: a sell that fills
    between the two reads shows up as fewer shares held, never as shares whose
    stop has gone.
    """

    held: float
    side: str | None
    book: _Book


@dataclass
class _Release:
    """What the cancel step did: each released order before and after."""

    group: Mapping[str, str] = field(default_factory=dict)
    pairs: list[tuple[Order, Order]] = field(default_factory=list)
    #: Orders sent a DELETE that was not refused outright: each may be
    #: cancelled, now or later, whatever its status read last.
    sent: dict[str, Order] = field(default_factory=dict)
    #: The order that would not confirm, and its last known record (None when
    #: it could not be read at all).
    blocker_id: str | None = None
    blocker: Order | None = None
    blocker_error: str = ""

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(after.id for _, after in self.pairs)

    @property
    def protective(self) -> list[Order]:
        return [after for _, after in self.pairs if _is_protective(after)]

    @property
    def touched(self) -> dict[str, Order]:
        """Every order this pass released or may yet cancel."""
        return {**self.sent, **{after.id: after for _, after in self.pairs}}


@dataclass(frozen=True)
class _Placed:
    """What a re-arm put back: the new stops, their shares, and what failed."""

    ids: tuple[str, ...] = ()
    qty: float = 0.0
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Ctx:
    """One close in progress: where it goes, and what it has done so far."""

    client: AlpacaClient
    ticker: str
    stamp: str
    #: The listing the release worked from: what stood before anything moved.
    book: _Book
    release: _Release
    sleep: Sleep
    #: Shares a stop (or an exit of this lot) covered when the close began,
    #: in doubt or not: what the verdict holds the end state to, capped by
    #: what is still held.
    wanted: float
    missed: tuple[str, ...] = ()
    reason: str = "time"


def _is_protective(order: Order) -> bool:
    return order.order_type.lower() in PROTECTIVE_TYPES


def exit_stamp_date(client_order_id: str, ticker: str, reason: str = "time") -> date | None:
    """The trade date of `ticker`'s exit under `client_order_id`, or None if it is no exit.

    The stamp `derive_exit_client_order_id` makes, or a retry of it (`-rN`),
    for any date. A stop re-armed or back-filled under a stamp (`-arm-...`,
    `-cover`) is a stop, not an exit, and is None here.
    """
    stem = derive_exit_client_order_id(ticker, date(2000, 1, 1), reason)[: -len("20000101")]
    match = re.fullmatch(rf"{re.escape(stem)}(\d{{8}})(?:-r\d+)?", client_order_id)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _releasable(order: Order, exits: frozenset[str]) -> bool:
    """Whether the release may cancel this working sell: protection, or a take-profit.

    A stop, or a limit sell: a bracket's or an OCO's take-profit holds the
    shares back with its stop, and goes first so the stop can. The listing
    may not show which stop it pairs with (`_at_risk`), so a limit sell is
    released whether the listing shows its pair or not. Never this lot's time
    exit, from whatever trade date, nor a market sell, which no bracket has
    as a leg: either is the lot's way out at the next open, and cancelling it
    swaps that for a cancel that may stick and a resend.
    """
    if order.id in exits:
        return False
    return _is_protective(order) or order.order_type.lower() == "limit"


def _remaining(order: Order) -> float:
    return max(0.0, order.qty - order.filled_qty)


def _released(order: Order | None) -> bool:
    return order is not None and order.status.lower() in RELEASED_STATUSES


def _working(order: Order | None) -> bool:
    return order is not None and order.status.lower() in WORKING_STATUSES


def _refused(exc: Exception) -> bool:
    """Whether a failed write provably changed nothing at the broker.

    Only a 4xx says so: Alpaca read the request and turned it down. A timeout
    or a dropped connection can lose the reply to a request that was carried
    out, and a 5xx can come from a gateway in front of a backend that took it.
    A duplicate client_order_id is a 4xx that proves the opposite: an order
    under that id already exists.
    """
    return (
        isinstance(exc, AlpacaRequestError)
        and 400 <= exc.status_code < 500
        and "client_order_id" not in exc.body
    )


def _market_shut(client: AlpacaClient) -> str:
    """'' when the market is shut and stays shut long enough; why not, otherwise.

    This is the precondition every step below rests on. Alpaca triggers stops
    and fills GTC take-profits in regular hours only, and queues a market
    order sent outside them for the open. So while it is shut, the holding
    changes only at the open, and the broker's share reservation alone decides
    which of two sells stands. The daily run is at 22:30 UTC, after every
    close of the year, so this costs it nothing; a pass run by hand during the
    session defers its time exits to the next run instead of racing the tape.
    """
    clock = client.clock()
    if clock.is_open:
        return f"the market is open (closes {clock.next_close})"
    left = _instant(clock.next_open) - _instant(clock.timestamp)
    if left < MIN_TIME_TO_OPEN:
        return f"the market opens at {clock.next_open}, under {MIN_TIME_TO_OPEN} from now"
    return ""


def _instant(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def close_with_protection(
    client: AlpacaClient,
    ticker: str,
    *,
    trade_date: date,
    reason: str = "time",
    sleep: Sleep | None = None,
) -> CloseOutcome:
    """One step of the machine in the module docstring: release, sell, verify, re-arm, verdict."""
    sleep = sleep or time.sleep
    try:
        start = _start(client, ticker, trade_date, reason)
    except Exception as exc:  # noqa: BLE001 — nothing sent yet, so nothing to undo
        return CloseOutcome(ticker, "unchanged", f"could not read the book: {exc}")
    if isinstance(start, CloseOutcome):
        return start
    stamp, book = start
    ctx = _Ctx(
        client, ticker, stamp, book, _Release(group=book.group), sleep,
        wanted=book.cover_standing,
        missed=_missed_exits(book, ticker, trade_date, reason),
        reason=reason,
    )
    if book.unclear or book.kept:
        return _settle(ctx, _blocked(book))

    ctx = replace(ctx, release=_release(client, book, sleep))
    if ctx.release.blocker_id is not None:
        return _abandon(ctx)
    return _sell_released(ctx)


def cover_beside_exit(
    client: AlpacaClient,
    ticker: str,
    *,
    stamp: str,
    qty: float,
    stop_price: float,
    sleep: Sleep | None = None,
) -> CloseOutcome:
    """Back-fill a lot whose exit under `stamp` was sent and never found.

    For a close that ended `unknown` because its exit may still land and no
    stop reserved the shares to stop it. The stop goes under a client id made
    from the stamp, so a lost reply is looked up rather than guessed at, and
    the verdict then reads the book: whichever of the two the broker took
    first stands, and the other was refused. While the market is shut that is
    all there is to it. With it open, nothing is placed.

    `unchanged` when the stop stands (the exit can no longer land),
    `exit_submitted` when the exit got there first, and `naked` or `unknown`
    otherwise.
    """
    sleep = sleep or time.sleep
    ctx = _Ctx(client, ticker, stamp, _NO_BOOK, _Release(), sleep, wanted=qty)
    try:
        shut = _market_shut(client)
    except Exception as exc:  # noqa: BLE001 — no clock, no write
        shut = f"the clock could not be read: {exc}"
    if shut:
        return CloseOutcome(
            ticker, "unknown", f"back-fill beside exit {stamp} not placed: {shut}",
            client_order_id=stamp,
        )
    new, error = _place_stop(ctx, qty, stop_price, _fit(f"{stamp}-cover"))
    placed = (
        _Placed((new.id,), qty) if new is not None else _Placed(errors=(f"back-fill: {error}",))
    )
    return _settle(
        ctx, f"back-fill beside exit {stamp}, which was never found",
        exit_open=True, placed=placed,
    )


def _start(
    client: AlpacaClient, ticker: str, trade_date: date, reason: str
) -> tuple[str, _Book] | CloseOutcome:
    """GUARD and STAMP: the exit id and the book to work from, or why not to start.

    An exit an earlier run queued that still works is this lot's exit as much
    as today's would be: no session has come to execute it (a weekday
    exchange holiday, or a rerun past 00:00 UTC), and the lot is on its way
    out at the next one.
    """
    shut = _market_shut(client)
    if shut:
        return CloseOutcome(ticker, "deferred", f"nothing sent: {shut}")
    stamp, prior = _exit_stamp(client, ticker, trade_date, reason)
    if prior is not None:
        return _prior_exit(ticker, prior)
    if stamp is None:
        return CloseOutcome(ticker, "unchanged", "no unused exit id for today")
    book = _read_book(client, ticker, reason)
    queued = book.queued_exit()
    if queued is not None:
        return _prior_exit(ticker, queued)
    return stamp, book


def _exit_stamp(
    client: AlpacaClient, ticker: str, trade_date: date, reason: str
) -> tuple[str | None, Order | None]:
    """The exit id to use, or the exit already standing under today's stamp."""
    base = derive_exit_client_order_id(ticker, trade_date, reason)
    for attempt in range(1, MAX_EXIT_ATTEMPTS + 1):
        key = base if attempt == 1 else f"{base}-r{attempt}"
        found = client.get_order_by_client_order_id(key)
        if found is None:
            return key, None
        if found.status.lower() not in FAILED_EXIT_STATUSES | {"replaced"}:
            return None, found
    return None, None


def _prior_exit(ticker: str, prior: Order) -> CloseOutcome:
    """This lot's exit is already there: on its way out, or in a status that is neither."""
    kw = {"exit_order_id": prior.id, "client_order_id": prior.client_order_id}
    if _working(prior) or prior.status.lower() == "filled":
        return CloseOutcome(
            ticker, "already_exiting", f"{prior.client_order_id} is {prior.status}", **kw
        )
    return CloseOutcome(
        ticker, "unknown",
        f"today's exit {prior.client_order_id} is {prior.status}: neither working nor gone",
        **kw,
    )


def _missed_exits(
    book: _Book, ticker: str, trade_date: date, reason: str
) -> tuple[str, ...]:
    return missed_exits(book.records.values(), ticker, trade_date, reason)


def missed_exits(
    orders: Iterable[Order], ticker: str, trade_date: date, reason: str = "time"
) -> tuple[str, ...]:
    """This lot's last earlier exit, when it ended without selling the lot and nothing followed.

    An exit sent after the close queues for the open, in place of the stop it
    released. If the broker rejects, cancels or expires it there, the lot
    spends the session with neither, and the next run only finds it naked:
    sold again or covered by then, but the gap happened, and only this says so.

    Named once: by the first run that reads the book after it. That run puts
    something in its place, a stop or a new exit of this lot, and once one
    was placed after it the exit is old news. So is one whose own close
    re-armed a stop under its stamp: it died that night, the lot kept its
    stop, and that close already said so.

    `orders` is any listing of the account's orders, in any status; only
    `ticker`'s sells count.
    """
    sells = [o for o in orders if o.symbol == ticker and o.side.lower() == "sell"]
    earlier = [
        o for o in sells
        if not _is_protective(o)
        and exit_stamp_date(o.client_order_id, ticker, reason) not in (None, trade_date)
    ]
    if not earlier:
        return ()
    last = max(earlier, key=lambda o: o.submitted_at)
    replaced_since = any(
        o.submitted_at > last.submitted_at
        and (_is_protective(o) or exit_stamp_date(o.client_order_id, ticker, reason))
        for o in sells
    )
    if (
        last.status.lower() in FAILED_EXIT_STATUSES
        and _remaining(last) > QTY_EPSILON
        and not replaced_since
    ):
        return (f"{last.client_order_id} {last.status}, {last.filled_qty:g} of {last.qty:g} sold",)
    return ()


def _read_book(client: AlpacaClient, ticker: str, reason: str = "time") -> _Book:
    """Every sell on the symbol that still reserves shares, any in doubt, and pairs.

    `status="all"` and nested, for the reason `stop_coverage` gives: a
    bracket's stop rests in `held`, nested under its parent, and the nesting is
    also the only place a stop's take-profit partner shows. An order in a status
    that is neither working nor gone is neither protection nor its absence.

    Nor is a working order whose pair has already ended: a take-profit that
    filled, or a leg the broker cancelled, takes the other leg with it, and
    for a while that leg still reads `held` with its cancel on the way.

    A working sell the release may not cancel (`_releasable`) is `kept`.
    """
    top = client.list_orders(status="all", limit=500, nested=True)
    group = {o.id: root.id for root in top for o in flatten_orders([root])}
    mine = [
        o for o in flatten_orders(top) if o.symbol == ticker and o.side.lower() == "sell"
    ]
    exits = frozenset(
        o.id for o in mine
        if not _is_protective(o) and exit_stamp_date(o.client_order_id, ticker, reason)
    )
    ended = {group.get(o.id, o.id) for o in mine if o.status.lower() in PAIR_ENDING_STATUSES}
    working = [o for o in mine if _remaining(o) > QTY_EPSILON]
    active = [o for o in working if _working(o) and group.get(o.id, o.id) not in ended]
    active_ids = {o.id for o in active}
    unclear = [
        o for o in working
        if o.status.lower() not in _GONE_STATUSES and o.id not in active_ids
    ]
    return _Book(
        live=tuple(_release_order([o for o in active if _releasable(o, exits)], group)),
        unclear=tuple(unclear),
        group=group,
        records={o.id: o for o in mine},
        kept=tuple(o for o in active if not _releasable(o, exits)),
        exits=exits,
    )


def _blocked(book: _Book) -> str:
    """Why nothing is sent: the sells in doubt, and those the release may not cancel."""
    parts = []
    if book.unclear:
        parts.append("sell order(s) in doubt: " + ", ".join(
            f"{o.id}={o.status}" + (", its pair ended" if _working(o) else "")
            for o in book.unclear
        ))
    if book.kept:
        parts.append("a sell the release may not cancel stands: " + ", ".join(
            f"{o.id}={o.order_type} {o.status} ({o.client_order_id})" for o in book.kept
        ))
    return "nothing sent, " + "; ".join(parts)


def _release_order(live: list[Order], group: Mapping[str, str]) -> list[Order]:
    """Take-profit legs first, each followed at once by its own paired stop.

    A take-profit goes before its stop so that a refusal there leaves the stop
    untouched. Its own stop goes straight after it, not after every other
    take-profit: cancelling a take-profit takes its paired stop with it, and a
    refusal on the NEXT pair must not find a released stop that this pass never
    recorded. Stops with no take-profit go last. Where the listing shows no
    pairing, this is every take-profit, then every stop.
    """
    ordered: dict[str, Order] = {}
    for tp in (o for o in live if not _is_protective(o)):
        ordered[tp.id] = tp
        for o in live:
            if _is_protective(o) and group.get(o.id) == group.get(tp.id):
                ordered.setdefault(o.id, o)
    for o in live:
        ordered.setdefault(o.id, o)
    return list(ordered.values())


def _at_risk(
    order: Order, group: Mapping[str, str], doubtful: Mapping[str, Order], *, known: bool
) -> bool:
    """Whether a cancel may be on its way to `order`: its own, or its pair's.

    `doubtful` is every order this pass released or sent a DELETE that was not
    refused, and every one in a status that is neither working nor gone.
    Cancelling one leg of a bracket or OCO pair takes the other with it at the
    broker, and the nested listing shows the pairing. Where it shows `order`
    with no partner, an order of the other kind that it shows with none either
    may be the partner it does not show. That last only for an order the close
    found standing (`known`): one placed since was paired with nothing.
    """
    key = group.get(order.id, order.id)
    if any(group.get(d, d) == key for d in doubtful):
        return True

    def alone(order_id: str) -> bool:
        root = group.get(order_id, order_id)
        return not any(r == root for oid, r in group.items() if oid != order_id)

    return known and alone(order.id) and any(
        alone(d) and _is_protective(o) != _is_protective(order) for d, o in doubtful.items()
    )


def _in_flight(ctx: _Ctx, book: _Book) -> frozenset[str]:
    """Standing orders a cancel is, or may be, on its way to: none of them is cover."""
    doubtful = {**ctx.release.touched, **{o.id: o for o in book.unclear}}
    group = {**ctx.book.group, **book.group}
    return frozenset(
        o.id for o in book.standing
        if o.id in doubtful
        or _at_risk(o, group, doubtful, known=o.id in ctx.book.records)
    )


def _release(client: AlpacaClient, book: _Book, sleep: Sleep) -> _Release:
    """Cancel each working sell in turn, and stop at the first that will not go."""
    release = _Release(group=book.group)
    for order in book.live:
        final, error = _cancel_until_released(client, order, release, sleep)
        if not _released(final):
            release.blocker_id, release.blocker, release.blocker_error = order.id, final, error
            return release
        release.pairs.append((order, final))
    return release


def _cancel_until_released(
    client: AlpacaClient, order: Order, release: _Release, sleep: Sleep
) -> tuple[Order | None, str]:
    """Cancel `order`, and keep at it until the broker says it can no longer sell.

    The order's status decides, never the DELETE's answer, and the DELETE is
    sent again at each read that finds it still working: a 429, or a request
    lost before it arrived, leaves it working, and one more DELETE costs
    nothing. Over the confirm window first. An order read working after it,
    every DELETE refused and nothing released that could take it along
    (`_at_risk`), is taken at its word: it is still the protection, and the
    release ends with it in place. Anything else may be cancelled yet, so it is
    waited out over the settle window; if it is still not gone, the release
    ends there too, and the verdict counts it as on its way out.
    """
    _send_cancel(client, order, release)
    last: Order | None = None
    error = ""
    for n, delay in enumerate((*_CONFIRM_DELAYS_S, *CANCEL_SETTLE_DELAYS_S)):
        if n == len(_CONFIRM_DELAYS_S) and _working(last) and not _at_risk(
            order, release.group, release.touched, known=True
        ):
            break
        if delay:
            sleep(delay)
        try:
            last = client.get_order(order.id)
        except Exception as exc:  # noqa: BLE001 — an unreadable order is an unconfirmed one
            error = str(exc)
            continue
        error = ""
        if _released(last):
            return last, ""
        if _working(last):
            _send_cancel(client, last, release)
    return last, error or f"still {last.status if last else 'unread'}"


def _send_cancel(client: AlpacaClient, order: Order, release: _Release) -> None:
    """Send one DELETE, and note the order if it may have been cancelled by it."""
    try:
        client.cancel_order(order.id)
    except Exception as exc:  # noqa: BLE001 — the order's status decides, not the call
        if _refused(exc):
            log.warning("%-6s cancel of %s refused: %s", order.symbol, order.id, exc)
            return
        log.warning("%-6s cancel of %s unanswered, it may land: %s", order.symbol, order.id, exc)
    release.sent[order.id] = order


def _read_truth(ctx: _Ctx) -> _Truth | str:
    """The symbol's sells, then its holding, as the broker has them now.

    The error text instead when every attempt failed: the caller then has no
    truth to act on and says so.
    """
    error = ""
    for delay in TRUTH_READ_DELAYS_S:
        if delay:
            ctx.sleep(delay)
        try:
            book = _read_book(ctx.client, ctx.ticker, ctx.reason)
            position = ctx.client.get_position(ctx.ticker)
        except Exception as exc:  # noqa: BLE001 — retried, then reported
            error = str(exc)
            continue
        return _Truth(
            held=position.qty if position is not None else 0.0,
            side=position.side if position is not None else None,
            book=book,
        )
    return error


def _abandon(ctx: _Ctx) -> CloseOutcome:
    """A cancel did not land: nothing is sold, and every stop that went goes back."""
    release = ctx.release
    state = release.blocker.status if release.blocker else "unreadable"
    why = f"nothing sold, cancel of {release.blocker_id} not confirmed ({state}"
    why += f": {release.blocker_error})" if release.blocker_error else ")"
    return _put_back(ctx, why)


def _sell_released(ctx: _Ctx) -> CloseOutcome:
    """Everything is released: sell what is held, settle the sell, re-arm if it is not working."""
    client, stamp = ctx.client, ctx.stamp
    try:
        position = client.get_position(ctx.ticker)
    except Exception as exc:  # noqa: BLE001 — no holding figure, no sell
        return _put_back(ctx, f"holding unreadable, nothing sold: {exc}")
    if position is None or position.qty <= QTY_EPSILON or position.side != "long":
        # Nothing to sell; the verdict says whether that is a close or worse.
        held_now = f"{position.qty:g} {position.side}" if position else "nothing"
        return _settle(ctx, f"{held_now} held once released, not sold")

    held = position.qty
    reply, error, refused = _submit_exit(ctx, held)
    if refused:
        # Turned down by the broker: no order exists under the stamp.
        return _put_back(ctx, f"exit {stamp} refused: {error}")
    if reply is not None:
        return _settle_exit(ctx, reply, f"sold {held:g}")
    found, lookup_error = _find_order(client, stamp, ctx.sleep)
    if found is not None:
        return _settle_exit(ctx, found, f"sold {held:g}, found by its stamp ({error})")
    waited = sum(EXIT_LOOKUP_DELAYS_S)
    why = f"exit {stamp} unanswered ({error}) and not found {waited:g}s later"
    if lookup_error:
        why += f" ({lookup_error})"
    return _put_back(ctx, why, exit_open=True)


def _settle_exit(ctx: _Ctx, exit_order: Order, why: str) -> CloseOutcome:
    """VERIFY with the exit in hand: a dead one re-arms, any other is read in the verdict."""
    if exit_order.status.lower() in FAILED_EXIT_STATUSES:
        return _put_back(ctx, f"exit {ctx.stamp} {exit_order.status}", exit_order=exit_order)
    return _settle(ctx, why, exit_order=exit_order)


def _submit_exit(ctx: _Ctx, qty: float) -> tuple[Order | None, str, bool]:
    """Send the stamped market sell: (the broker's reply, error, refused).

    Only a refusal proves the order absent. Anything else, a timeout above all,
    can lose the reply to an accepted order, so the caller settles it by
    looking the stamp up. A day order sent after the close queues for the open.
    """
    try:
        return ctx.client.submit_order(
            symbol=ctx.ticker,
            qty=qty,
            side="sell",
            order_type="market",
            time_in_force="day",
            client_order_id=ctx.stamp,
        ), "", False
    except Exception as exc:  # noqa: BLE001 — resolved by the lookup that follows
        return None, str(exc), _refused(exc)


def _find_order(
    client: AlpacaClient, client_order_id: str, sleep: Sleep
) -> tuple[Order | None, str]:
    """The order under a client id, looked up until found or the window closes.

    For the exit under its stamp, and for a re-armed stop whose POST got no
    answer. Returns (order, error of the last lookup). Not found is not proof
    of absence, and nothing below treats it as such: it only means no reply
    and no record yet.
    """
    error = ""
    for delay in (0.0, *EXIT_LOOKUP_DELAYS_S):
        if delay:
            sleep(delay)
        try:
            found = client.get_order_by_client_order_id(client_order_id)
        except Exception as exc:  # noqa: BLE001 — looked up again, then reported
            error = str(exc)
            continue
        if found is not None:
            return found, ""
        error = ""
    return None, error


def _put_back(
    ctx: _Ctx, why: str, *, exit_order: Order | None = None, exit_open: bool = False
) -> CloseOutcome:
    """No working exit: put the stops that went back, then read the verdict.

    Sized off a fresh read, never off what this close remembers. Where the
    read fails, the stops this close released go back for what they covered,
    unchecked: no fill can have touched the lot while the market is shut, so
    those stops were the only sellers it had, and a stop the holding cannot
    take is refused, not stacked. Where an exit may still land
    (`exit_open`), the same holds: the broker lets the exit or the stop stand,
    never both. And where the fresh read shows the exit standing after all,
    nothing goes back beside it.
    """
    truth = _read_truth(ctx)
    if isinstance(truth, str):
        cover = sum(_remaining(o) for o in ctx.release.protective)
        placed = _place_stops(ctx, ctx.release.protective, cover)
        why += f"; the book was unreadable, so the released stops went back unchecked: {truth}"
        return _settle(ctx, why, exit_order=exit_order, exit_open=exit_open, placed=placed)
    landed = truth.book.stamped(ctx.stamp)
    if _working(landed):
        return _settle(ctx, f"{why}; exit {ctx.stamp} is there after all", exit_order=landed)
    placed = _rearm(ctx, truth)
    return _settle(ctx, why, exit_order=exit_order, exit_open=exit_open, placed=placed)


def _rearm(ctx: _Ctx, truth: _Truth) -> _Placed:
    """Put back the stops that went, for the shares they covered that nothing covers now.

    Plain GTC `stop` at the level each stood at, whatever its type: a
    stop-limit can be skipped in a gap, and a trailing stop's level is where it
    stood, not a trail to rebuild. A take-profit is not re-placed: beside a
    stop it would be a second sell on the same shares. What went is read off
    the broker: every stop of the first listing that the fresh one no longer
    shows standing, so a stop that left with its take-profit is as gone as one
    this close cancelled.

    Sized by what is missing, not by what this module thinks is free: the
    broker refuses a sell for more than the holding less what open sells
    reserve, so a stop cannot be stacked on shares another sell holds, however
    this listing pairs them. Only a long holding gets one: on a flat or short
    book a sell stop is a short sale, not protection.
    """
    if truth.side != "long":
        return _Placed()
    in_flight = _in_flight(ctx, truth.book)
    need = min(ctx.wanted, truth.held) - truth.book.cover(in_flight)
    standing = {o.id for o in truth.book.standing}
    gone = [
        truth.book.records.get(o.id, o) for o in ctx.book.live
        if _is_protective(o) and o.id not in standing
    ]
    gone = [o for o in gone if o.status.lower() != "filled"]
    return _place_stops(ctx, gone, max(0.0, need))


def _place_stops(ctx: _Ctx, released: Sequence[Order], cap: float) -> _Placed:
    """One stop per released stop, at its level, never more than `cap` shares in all."""
    placed: list[str] = []
    errors: list[str] = []
    remaining = cap
    for order in released:
        qty = min(_remaining(order), remaining)
        if qty <= QTY_EPSILON:
            continue
        if order.stop_price is None:
            errors.append(f"{order.id}: no stop price to re-place")
            continue
        new, error = _place_stop(ctx, qty, order.stop_price, _rearm_id(ctx.stamp, order.id))
        if new is None:
            errors.append(f"{order.id}: {error}")
            continue
        placed.append(new.id)
        remaining -= qty
    return _Placed(tuple(placed), cap - remaining, tuple(errors))


def _rearm_id(stamp: str, replaced_id: str) -> str:
    """The client id of the stop that re-arms `replaced_id` in the close under `stamp`.

    Deterministic, so a POST whose reply is lost is found by looking it up, and
    a close run twice re-arms under the same id and is told it exists. Unique
    without a counter: a stop is released once, and a later close releases the
    stop this one placed, which has an id of its own.
    """
    return _fit(f"{stamp}-arm-{_digest(replaced_id)}")


def _digest(text: str, n: int = 8) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def _fit(client_id: str) -> str:
    """`client_id`, or a digest of it where it is longer than the broker takes.

    Either way the same input gives the same id, which is all a lookup needs.
    """
    if len(client_id) <= MAX_CLIENT_ID_LEN:
        return client_id
    return f"tr-{_digest(client_id, 32)}"


def _place_stop(
    ctx: _Ctx, qty: float, stop_price: float, coid: str
) -> tuple[Order | None, str]:
    """Send one stop under `coid`: (order, error).

    Only a refusal proves the stop absent. Anything else is settled by looking
    the client id up, and a stop found working counts as placed. One not found
    is reported as not placed, which errs toward a page: should it land later,
    it is a stop on shares nothing else reserves, or the broker refuses it.
    """
    try:
        return ctx.client.submit_order(
            symbol=ctx.ticker,
            qty=qty,
            side="sell",
            order_type="stop",
            time_in_force="gtc",
            stop_price=stop_price,
            client_order_id=coid,
        ), ""
    except Exception as exc:  # noqa: BLE001 — refused, or looked up below
        if _refused(exc):
            return None, str(exc)
        sent = exc
    found, lookup_error = _find_order(ctx.client, coid, ctx.sleep)
    if _working(found):
        return found, ""
    if found is not None:
        return None, f"{sent} (then {found.status})"
    return None, f"{sent} (not found{f': {lookup_error}' if lookup_error else ''})"


def _settle(
    ctx: _Ctx,
    why: str,
    *,
    exit_order: Order | None = None,
    exit_open: bool = False,
    placed: _Placed | None = None,
) -> CloseOutcome:
    """Read the book once more and say what state the lot is in: the post-condition."""
    placed = placed or _Placed()
    if placed.errors:
        why += f"; re-arm failed: {'; '.join(placed.errors)}"
    truth = _read_truth(ctx)
    if isinstance(truth, str):
        return _outcome(
            ctx, "unknown", f"{why}; the end state could not be read: {truth}", placed,
            client_order_id=ctx.stamp if exit_open or exit_order else None,
        )
    return _verdict(ctx, why, truth, exit_order, exit_open, placed)


def _verdict(
    ctx: _Ctx,
    why: str,
    truth: _Truth,
    exit_order: Order | None,
    exit_open: bool,
    placed: _Placed,
) -> CloseOutcome:
    """The lot as the fresh read shows it, held to what it was when the close began.

    Acceptable ends: nothing held and nothing standing; the stamped exit
    working for the whole holding with no other sell beside it; or still held,
    with every share a stop covered at the start covered by a working stop no
    cancel is on its way to. Anything else is `naked` (shares lost their stop,
    or will when a cancel in flight lands) or `unknown` (a state that must not
    exist, or an exit that may still land on shares nothing reserves).
    """
    exit_now = truth.book.stamped(ctx.stamp) or exit_order
    exit_kw = (
        {"exit_order_id": exit_now.id, "client_order_id": ctx.stamp} if exit_now else {}
    )
    if truth.side is not None and truth.side != "long" and truth.held > QTY_EPSILON:
        return _outcome(ctx, "unknown", f"{why}; position is {truth.side}, not long", placed)
    if truth.held <= QTY_EPSILON:
        return _flat_verdict(ctx, why, truth.book, exit_now, placed, exit_kw)
    if exit_now is not None and _working(exit_now):
        return _exit_verdict(ctx, why, truth, exit_now, placed, exit_kw)
    return _held_verdict(ctx, why, truth, exit_open, placed)


def _flat_verdict(
    ctx: _Ctx, why: str, book: _Book, exit_now: Order | None, placed: _Placed,
    exit_kw: dict[str, str],
) -> CloseOutcome:
    """Nothing held: closed, unless a sell still stands, which is a short waiting to happen."""
    if book.standing:
        standing = ", ".join(sorted(o.id for o in book.standing))
        return _outcome(
            ctx, "unknown", f"{why}; nothing held, yet {standing} still stands on the flat book",
            placed, **exit_kw,
        )
    if exit_now is not None and exit_now.filled_qty > QTY_EPSILON:
        return _outcome(ctx, "exit_submitted", why, placed, **exit_kw)
    return _outcome(ctx, "already_closed", f"{why}; nothing held", placed)


def _exit_verdict(
    ctx: _Ctx, why: str, truth: _Truth, exit_now: Order, placed: _Placed,
    exit_kw: dict[str, str],
) -> CloseOutcome:
    """The exit works: on its way out, if it is the only seller and sells the whole lot."""
    others = sorted(o.id for o in truth.book.standing if o.client_order_id != ctx.stamp)
    if others:
        return _outcome(
            ctx, "unknown",
            f"{why}; exit {exit_now.id} works beside {', '.join(others)}: two sellers",
            placed, **exit_kw,
        )
    if _remaining(exit_now) + QTY_EPSILON < truth.held:
        return _outcome(
            ctx, "naked",
            f"{why}; exit {exit_now.id} sells {_remaining(exit_now):g} of {truth.held:g} held, "
            "and nothing covers the rest", placed, **exit_kw,
        )
    return _outcome(
        ctx, "exit_submitted", f"{why}; exit {exit_now.id} {exit_now.status}", placed, **exit_kw
    )


def _held_verdict(
    ctx: _Ctx, why: str, truth: _Truth, exit_open: bool, placed: _Placed
) -> CloseOutcome:
    """Still held and no exit works: every share a stop covered must be covered again.

    Counted, not assumed: working stops no cancel is on its way to, each pair
    once, against what the first listing's stops and this lot's exits covered
    or what is still held, whichever is less. And an exit that may still land is ruled out only
    by a sell that reserves the shares: the broker refuses it beside one.
    """
    book, held = truth.book, truth.held
    in_flight = _in_flight(ctx, book)
    covered = book.cover(in_flight)
    wanted = min(ctx.wanted, held)
    may_land = exit_open and book.reserved <= QTY_EPSILON
    hand_on = {"client_order_id": ctx.stamp} if may_land else {}
    if covered + QTY_EPSILON < wanted:
        detail = (
            f"{why}; {wanted - covered:g} shares that had a stop have no working one "
            f"({covered:g} of {held:g} held covered)"
        )
        pending = ", ".join(
            f"{oid}={book.records[oid].status}" for oid in sorted(in_flight)
            if oid in book.records
        )
        if pending:
            detail += f"; on its way out: {pending}"
        return _outcome(ctx, "naked", detail, placed, **hand_on)
    if may_land:
        return _outcome(
            ctx, "unknown",
            f"{why}; nothing reserves the {held:g} shares, so exit {ctx.stamp} may still land",
            placed, **hand_on,
        )
    return _outcome(ctx, "unchanged", f"{why}; {covered:g} of {held:g} held covered", placed)


def _outcome(
    ctx: _Ctx, status: CloseStatus, detail: str, placed: _Placed, **kw: str | None
) -> CloseOutcome:
    if ctx.missed:
        detail += f"; an earlier exit did not sell the lot: {'; '.join(ctx.missed)}"
    return CloseOutcome(
        ctx.ticker, status, detail,
        released=ctx.release.ids, rearmed=placed.ids, missed_exits=ctx.missed, **kw,  # type: ignore[arg-type]
    )
