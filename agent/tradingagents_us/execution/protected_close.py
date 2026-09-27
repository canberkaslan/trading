"""Close a long position that a stop protects, with no gap and no second seller.

A bare DELETE /positions/{symbol} cannot close a protected position here. Every
bracket entry, and every stop the position pass back-fills, is a GTC sell that
reserves all of the position's shares, and Alpaca refuses the close with
held_for_orders=qty, available=0. That is why the time exit never executed.

Releasing the stop first opens the hazard stop_coverage.py and
position_manager.py both name: two sells on one long lot. A stop left standing
beside our sell, or one that fires while our sell also fills, sells the lot
twice, and the second sale is a SHORT that nothing protects. So the sequence is
fixed, and each step must be confirmed before the next one runs:

  1. every live sell on the symbol is cancelled. A bracket's take-profit goes
     before its own stop, so a refusal there leaves that stop untouched, and
     its stop goes straight after it: cancelling a take-profit takes its paired
     stop with it at the broker;
  2. each cancel is CONFIRMED at the broker. A cancel request is not a cancelled
     order, and one in `pending_cancel` can still fill. A cancel that was sent,
     or may have been, cannot be taken back: it lands unless the order fills
     first. So it is waited out to a terminal status, not given up on;
  3. the holding is read again AFTER the release, and the sell is sized off that
     read and nothing earlier: a stop that fired during the release leaves
     fewer shares, or none;
  4. the sell carries the rule-exit stamp (`derive_exit_client_order_id`), so
     the ledger books it as a time exit and this module can find it again;
  5. it is verified by that stamp, and if it did not become a working order the
     released stops are re-placed in the same pass, sized off the broker's
     orders and holding read again at that moment, never off what this module
     remembers. Where the exit's own POST went unanswered, the stamp is looked
     up once more after the re-arm, because the exit can still land between
     that read and the re-arm. If it did, every sell standing beside it is
     taken back, read off a fresh listing: each re-armed stop carries its own
     client id, so one whose reply was lost is found there too, and one that
     is not there yet may still land, so the outcome does not read as a close.

Whatever stops, the stops that went are put back from the broker's listing,
not from this module's record: a stop that left with its take-profit is as gone
as one this module cancelled. And `unchanged` is counted, not assumed: every
held share a stop covered when the close began is covered again, or the outcome
says how many are not.

When a step cannot be confirmed, the sequence stops there. The outcome says
which acceptable end state the position is in, still protected or on its way
out. When neither could be established it says that too, so the run fails
loudly instead of reading as a close.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Literal

from ..dataflows.alpaca_broker import AlpacaClient, AlpacaRequestError, Order
from ..risk.stop_coverage import LIVE_STATUSES, PROTECTIVE_TYPES, QTY_EPSILON, flatten_orders
from .executor import derive_exit_client_order_id

log = logging.getLogger(__name__)

#: Statuses in which a cancelled order can no longer sell a share. Narrower than
#: stop_coverage.TERMINAL_STATUSES on purpose. `stopped` means a fill is
#: guaranteed but may not be booked yet, so the holding read after it could
#: still count shares that are about to go. `replaced` means a new order took
#: its place and is live. `done_for_day` may resume. None of those is released.
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

#: How long to wait for a cancel to land: polls x interval, about five seconds.
#: Every cancel gets this window, refused or not: a refusal can come from an
#: order that is already on its way out (a bracket's stop, cancelled with its
#: take-profit as an OCO pair, reads `pending_cancel` and refuses a second
#: DELETE), and only its status says which. Where its take-profit went first
#: it may read `held` a while yet, so the pairing decides there (`_release`).
CANCEL_CONFIRM_POLLS = 10
CANCEL_CONFIRM_INTERVAL_S = 0.5

#: Then, for a cancel that was sent or may have been (a transport error can lose
#: the reply to a DELETE that landed) and is still not terminal, a further wait
#: that backs off to about a minute in all. Such a cancel cannot be withdrawn:
#: when it lands, the shares it reserved have no stop. Stopping at five seconds
#: and reporting the lot as protected is how the log came to say `unchanged`
#: about a position whose stop was about to disappear.
CANCEL_SETTLE_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 15.0, 15.0, 15.0)

#: How long to look for an exit whose submit did not come back clean: past
#: AlpacaClient's 15 s request timeout, so a POST the broker was still working
#: on when the reply was lost has shown up by the last lookup. Only that last
#: lookup finding nothing counts as "not placed". A single lookup straight after
#: a timeout can 404 an order that lands a moment later, and a stop re-armed on
#: that answer is a second seller: once the exit fills, a sell stop on a flat
#: book is a short.
EXIT_LOOKUP_DELAYS_S = (1.0, 2.0, 4.0, 8.0, 8.0)

#: Attempts at the broker's truth (the symbol's orders, then its holding) before
#: a re-arm, and the pauses between them.
TRUTH_READ_DELAYS_S = (0.0, 1.0, 2.0)

CloseStatus = Literal[
    "exit_submitted",   # stops released, stamped sell working or filled
    "already_closed",   # nothing held once released: a leg filled meanwhile
    "already_exiting",  # a live or filled exit already carries today's stamp
    "unchanged",        # still held, and every share a stop covered is covered again
    "naked",            # shares a stop covered have none now: page someone
    "unknown",          # a step could not be confirmed: the lot may be uncovered
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

    @property
    def ok(self) -> bool:
        return self.status in CLOSED_STATUSES


@dataclass
class _Release:
    """What the cancel step did: each released order before and after."""

    pairs: list[tuple[Order, Order]] = field(default_factory=list)
    #: The order that would not confirm, and its last known record (None when
    #: it could not be read at all).
    blocker_id: str | None = None
    blocker: Order | None = None
    blocker_error: str = ""
    #: False only when the blocker's cancel was refused outright, nothing
    #: paired with it was released in this pass, and it still reads as
    #: working: then it is still the protection. Otherwise a cancel is on its
    #: way, and the blocker's shares are uncovered when it lands.
    cancel_in_flight: bool = False
    #: The listing's pairing (`_Book.group`): order id -> its pair's id.
    group: Mapping[str, str] = field(default_factory=dict)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(after.id for _, after in self.pairs)

    def pair_released(self, order: Order) -> bool:
        """Whether an order paired with `order` may have been released in this pass.

        Cancelling one leg of a bracket or OCO pair takes the other with it at
        the broker, and so does a fill. Once one leg is gone, the other's
        cancel is on its way however its own DELETE is answered. The listing
        shows the pairing where it nests the legs. Where it shows `order` with
        no partner, a sell of the other kind released before it that shows
        none either may be the partner it does not show.
        """
        key = self.group.get(order.id, order.id)
        released = [after for _, after in self.pairs]
        if any(self.group.get(o.id, o.id) == key for o in released):
            return True
        return self.alone(order.id) and any(
            self.alone(o.id) and _is_protective(o) != _is_protective(order) for o in released
        )

    def alone(self, order_id: str) -> bool:
        """Whether the listing shows no other order in `order_id`'s pair."""
        key = self.group.get(order_id, order_id)
        return not any(root == key for oid, root in self.group.items() if oid != order_id)

    @property
    def protective(self) -> list[Order]:
        return [after for _, after in self.pairs if _is_protective(after)]

    @property
    def rearm_cap(self) -> float:
        """Shares the released stops may cover again, beside a holding read after it.

        What the stops still covered when they died, less what their paired
        legs sold on the way, pair by pair. A bracket's take-profit and stop
        reserve the SAME shares, so a take-profit that filled during the
        release took those shares with it, and re-arming the stop for them
        would sell shares that are gone. A sell paired with no stop (a limit
        placed by hand) reserved shares of its own: what it sold was never a
        stop's, and taking it off the stops' cover leaves their shares naked.
        """
        covered: dict[str, float] = {}
        for order in self.protective:
            key = self.group.get(order.id, order.id)
            covered[key] = covered.get(key, 0.0) + _remaining(order)
        sold: dict[str, float] = {}
        for before, after in self.pairs:
            key = self.group.get(after.id, after.id)
            if not _is_protective(after) and key in covered:
                sold[key] = sold.get(key, 0.0) + max(0.0, after.filled_qty - before.filled_qty)
        return sum(max(0.0, cover - sold.get(key, 0.0)) for key, cover in covered.items())

    @property
    def blind_rearm_cap(self) -> float:
        """`rearm_cap` for a re-arm that no holding read after the release bounds.

        Less what every released sell that is not a stop sold on the way,
        paired or not. The listing's pairing is all that says whose shares
        such a sell took, and a re-arm with nothing else to go on does not rest
        on it: a take-profit whose pairing the listing does not show took its
        stop's shares with it, and a stop put back for them is a short.
        """
        sold = sum(
            max(0.0, after.filled_qty - before.filled_qty)
            for before, after in self.pairs
            if not _is_protective(after)
        )
        return max(0.0, sum(_remaining(o) for o in self.protective) - sold)


@dataclass(frozen=True)
class _Book:
    """The symbol's sells as one listing has them, and which ones pair up.

    A bracket's take-profit and stop, like an OCO order's two legs, can each
    sell the same shares and only one of them ever will: cancelling one takes
    the other with it, and the broker holds the shares back once for the pair.
    The nested listing is the only place the pairing shows, so it is read
    there, before the listing is flattened.
    """

    #: Live sells, in the order they are to be released.
    live: tuple[Order, ...]
    #: Sells in a status that is neither live nor gone, or live in a pair
    #: whose other leg has ended: each may be on its way out.
    unclear: tuple[Order, ...]
    #: Order id -> id of the top-level order it was listed under: the pairing.
    group: Mapping[str, str]
    #: Every sell on the symbol the listing returned, whatever its status.
    records: Mapping[str, Order]

    @property
    def standing(self) -> tuple[Order, ...]:
        return (*self.live, *self.unclear)

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
    def stop_cover(self) -> float:
        """Shares a live stop covers, each pair counted once."""
        return self._per_group([o for o in self.live if _is_protective(o)])

    @property
    def stop_cover_standing(self) -> float:
        """The same, counting stops in doubt (a cancel on its way) as well."""
        return self._per_group([o for o in self.standing if _is_protective(o)])

    def stop_cover_of(self, order_ids: Sequence[str]) -> float:
        """What the standing stops in the same pairs as these orders cover."""
        groups = {self.group.get(oid, oid) for oid in order_ids}
        return self._per_group(
            [o for o in self.standing
             if _is_protective(o) and self.group.get(o.id, o.id) in groups]
        )


@dataclass(frozen=True)
class _Truth:
    """The symbol as the broker has it now, read orders first and holding last.

    That order is what makes it safe to size a re-arm from: a sell that fills
    between the two reads shows up as fewer shares held, never as more room.
    """

    held: float
    side: str | None
    book: _Book

    @property
    def reserving_ids(self) -> frozenset[str]:
        return frozenset(o.id for o in self.book.standing)

    @property
    def room(self) -> float:
        """Shares held long that no live sell reserves: all a new stop may cover."""
        if self.side != "long":
            return 0.0
        return max(0.0, self.held - self.book.reserved)


@dataclass(frozen=True)
class _Sent:
    error: str = ""
    #: True only when the broker answered and turned the order down: then no
    #: order exists under the stamp and nothing needs looking up.
    refused: bool = False


@dataclass(frozen=True)
class _Placed:
    """What a re-arm put back: the new stops, their shares, and what failed."""

    ids: tuple[str, ...] = ()
    qty: float = 0.0
    errors: tuple[str, ...] = ()
    #: Client ids of stops whose POST went unanswered and whose lookup could
    #: not say whether they exist, or, beside an exit that may still land, did
    #: not find them: such a POST can land late as the exit can. Each may be
    #: standing at the broker.
    unresolved: tuple[str, ...] = ()


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


def _is_protective(order: Order) -> bool:
    return order.order_type.lower() in PROTECTIVE_TYPES


def _remaining(order: Order) -> float:
    return max(0.0, order.qty - order.filled_qty)


def _released(order: Order | None) -> bool:
    return order is not None and order.status.lower() in RELEASED_STATUSES


def _working(order: Order | None) -> bool:
    return order is not None and order.status.lower() in LIVE_STATUSES


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


def close_with_protection(
    client: AlpacaClient,
    ticker: str,
    *,
    trade_date: date,
    reason: str = "time",
    sleep: Sleep | None = None,
) -> CloseOutcome:
    """Release the protection, confirm it, sell what is held, verify, re-arm on failure."""
    sleep = sleep or time.sleep
    try:
        stamp, prior = _exit_stamp(client, ticker, trade_date, reason)
        if prior is not None:
            return CloseOutcome(
                ticker, "already_exiting", f"{prior.client_order_id} is {prior.status}",
                exit_order_id=prior.id, client_order_id=prior.client_order_id,
            )
        if stamp is None:
            return CloseOutcome(ticker, "unchanged", "no unused exit id for today")
        book = _read_book(client, ticker)
    except Exception as exc:  # noqa: BLE001 — nothing sent yet, so nothing to undo
        return CloseOutcome(ticker, "unchanged", f"could not read the book: {exc}")
    if book.unclear:
        described = ", ".join(
            f"{o.id}={o.status}" + (", its pair ended" if _working(o) else "")
            for o in book.unclear
        )
        return CloseOutcome(ticker, "unchanged", f"sell order(s) in unclear status: {described}")

    release = _release(client, list(book.live), book.group, sleep)
    ctx = _Ctx(client, ticker, stamp, book, release, sleep)
    if release.blocker_id is not None:
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
    """Back-fill a lot whose exit under `stamp` may still land, and check it did not.

    For a close that ended `unknown` because its exit was sent and never ruled
    out. The lot reads naked, so it needs a stop, but the exit can land between
    the read that found it naked and the stop's POST: in regular hours it fills,
    and the stop stands on a flat book, a short-sale stop nothing withdraws. So
    the stop is placed as a re-arm is, under a client id of its own, and the
    stamp and the book are read once more after it, as `_exit_landed` does
    after a re-arm: an exit that landed takes the stop back off, and once the
    stop stands a later landing is refused, since the stop reserves the shares.

    `unchanged` when the stop stands and no exit is there, `exit_submitted` or
    `already_closed` when the exit took the lot and nothing else stands on it,
    and `naked` or `unknown` otherwise.
    """
    ctx = _Ctx(client, ticker, stamp, _Book((), (), {}, {}), _Release(), sleep or time.sleep)
    coid = f"{stamp}-cover-{uuid.uuid4().hex[:12]}"
    new, error, settled = _place_stop(ctx, qty, stop_price, coid, trust_window=False)
    placed = _Placed(
        ids=(new.id,) if new is not None else (),
        qty=qty if new is not None else 0.0,
        errors=() if new is not None else (f"back-fill: {error}",),
        unresolved=() if settled else (coid,),
    )
    why = f"back-fill beside exit {stamp}, which was never ruled out"
    landed = _exit_landed(ctx, why, placed)
    if landed is not None:
        return landed
    if new is None:
        return _rearmed(ticker, "naked", why, ctx.release, placed)
    return _rearmed(ticker, "unchanged", f"{why}; it is not at the broker", ctx.release, placed)


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


def _read_book(client: AlpacaClient, ticker: str) -> _Book:
    """Every sell on the symbol that still reserves shares, any in doubt, and pairs.

    `status="all"` and nested, for the reason `stop_coverage` gives: a
    bracket's stop rests in `held`, nested under its parent, and the nesting is
    also the only place a stop's take-profit partner shows. An order in a status
    that is neither live nor gone is neither protection nor its absence, and the
    caller acts on none of the symbol's orders while one is standing.

    Nor is a live order whose pair has already ended: a take-profit that
    filled, or a leg the broker cancelled, takes the other leg with it, and
    for a while that leg still reads `held` with its cancel on the way. A
    refused DELETE there is not the protection answering; taken at its word,
    the release cancels every other stop first and ends on a lot that is naked
    once the cascade lands. So it is in doubt like `pending_cancel`.
    """
    top = client.list_orders(status="all", limit=500, nested=True)
    group = {o.id: root.id for root in top for o in flatten_orders([root])}
    mine = [
        o for o in flatten_orders(top) if o.symbol == ticker and o.side.lower() == "sell"
    ]
    ended = {group.get(o.id, o.id) for o in mine if o.status.lower() in PAIR_ENDING_STATUSES}
    working = [o for o in mine if o.qty - o.filled_qty > QTY_EPSILON]
    live = [
        o for o in working
        if o.status.lower() in LIVE_STATUSES and group.get(o.id, o.id) not in ended
    ]
    live_ids = {o.id for o in live}
    unclear = [
        o for o in working
        if o.status.lower() not in _GONE_STATUSES and o.id not in live_ids
    ]
    return _Book(
        live=tuple(_release_order(live, group)),
        unclear=tuple(unclear),
        group=group,
        records={o.id: o for o in mine},
    )


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


def _release(
    client: AlpacaClient, live: list[Order], group: Mapping[str, str], sleep: Sleep
) -> _Release:
    """Cancel each order and wait until the broker says it can no longer sell.

    The order's status decides, never the cancel call's answer. A refusal whose
    order still reads as working after the confirm window is taken at its word:
    that order is still the protection. Anything else means a cancel is on its
    way (sent, possibly sent, or already `pending_cancel`), and it is waited out
    over the longer settle window.

    Except where the order's pair went earlier in this pass, or may have: a
    listing that shows no partner for it is not proof it has none. Its
    take-profit's cancel took it along at the broker, so its cancel is on its
    way even when its own DELETE is refused (a 429 under load) and it still
    reads `held`. That refusal is not taken at its word: the order is waited
    out like any sent cancel, and if it still stands, it is not called the
    protection.
    """
    release = _Release(group=group)
    for order in live:
        trusted = _cancel(client, order) and not release.pair_released(order)
        final, error = _await_release(client, order.id, _CONFIRM_DELAYS_S, sleep)
        if not _released(final) and not (trusted and _working(final)):
            final, error = _await_release(
                client, order.id, CANCEL_SETTLE_DELAYS_S, sleep, last=final
            )
        if not _released(final):
            release.blocker_id, release.blocker, release.blocker_error = order.id, final, error
            release.cancel_in_flight = not (trusted and _working(final))
            return release
        release.pairs.append((order, final))
    return release


def _cancel(client: AlpacaClient, order: Order) -> bool:
    """Send the cancel. True only when the broker refused it outright."""
    try:
        client.cancel_order(order.id)
    except Exception as exc:  # noqa: BLE001 — the order's status decides, not the call
        refused = _refused(exc)
        verdict = "refused" if refused else "unanswered (it may have landed)"
        log.warning("%-6s cancel of %s %s: %s", order.symbol, order.id, verdict, exc)
        return refused
    return False


def _await_release(
    client: AlpacaClient,
    order_id: str,
    delays: Sequence[float],
    sleep: Sleep,
    last: Order | None = None,
) -> tuple[Order | None, str]:
    error = ""
    for delay in delays:
        if delay:
            sleep(delay)
        try:
            last = client.get_order(order_id)
        except Exception as exc:  # noqa: BLE001 — an unreadable order is an unconfirmed one
            error = str(exc)
            continue
        error = ""
        if _released(last):
            return last, ""
    return last, error or f"still {last.status if last else 'unread'}"


def _read_truth(client: AlpacaClient, ticker: str, sleep: Sleep) -> _Truth | str:
    """The symbol's sells, then its holding, as the broker has them now.

    The error text instead when every attempt failed: the caller then has no
    truth to act on and says so.
    """
    error = ""
    for delay in TRUTH_READ_DELAYS_S:
        if delay:
            sleep(delay)
        try:
            book = _read_book(client, ticker)
            position = client.get_position(ticker)
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
    """A cancel did not land: no sell, and put back every stop that went.

    What went is read off the broker, not off this pass's own record: every
    stop the first listing had standing that the fresh listing no longer does,
    and that did not fill. A take-profit's cancel takes its paired stop with it
    at the broker, and a blocker whose cancel has landed since is gone too.
    Sized off the same fresh read, which counts every sell still standing, each
    pair once, so a stop is never stacked on shares another sell reserves.
    """
    client, ticker, book, release = ctx.client, ctx.ticker, ctx.book, ctx.release
    blocker = release.blocker
    state = blocker.status if blocker else "unreadable"
    detail = f"cancel of {release.blocker_id} not confirmed ({state}"
    detail += f": {release.blocker_error})" if release.blocker_error else ")"

    truth = _read_truth(client, ticker, ctx.sleep)
    if isinstance(truth, str):
        # No sell was sent, so the stops this pass saw released are the only
        # sellers there have been, and their own cap is safe to put back. What
        # else went with them, and what still stands, is unknown.
        placed = _rearm(ctx, release.protective, release.blind_rearm_cap)
        status: CloseStatus = "naked" if placed.errors else "unknown"
        why = f"{detail}; holding unreadable, coverage not verified: {truth}"
        return _rearmed(ticker, status, why, release, placed)
    settled = _no_rearm(ticker, truth, detail, release)
    if settled is not None:
        return settled

    gone = _gone_stops(book, truth)
    placed = _rearm(ctx, gone, min(sum(map(_remaining, gone)), truth.room))
    if placed.errors:
        return _rearmed(ticker, "naked", detail, release, placed)
    in_flight = _cancels_in_flight(release, truth)
    suspects, unseen, at_risk = _unseen_cascades(book, truth, release, in_flight)
    if in_flight or suspects:
        # Still standing with a cancel on its way: it cannot be put back
        # beside itself, and when the cancel lands its paired stop goes too.
        after = (
            truth.book.stop_cover_standing - truth.book.stop_cover_of(in_flight)
            - at_risk + placed.qty
        )
        short = max(0.0, min(book.stop_cover, truth.held) - after)
        if in_flight:
            detail += f"; a cancel is on its way ({', '.join(in_flight)})"
        if suspects:
            detail += (
                f"; {unseen} take-profit(s) released with no partner in the listing "
                f"may take a stop among ({', '.join(suspects)}) with them"
            )
        detail += f", and {short:g} shares have no stop when it lands"
        return _rearmed(ticker, "unknown", detail, release, placed)
    if release.blocker_id not in truth.reserving_ids:
        final = truth.book.records.get(release.blocker_id or "")
        detail += f"; it is {final.status if final else 'gone'} since"
    return _verdict(ticker, detail, book, truth, release, placed)


def _gone_stops(book: _Book, truth: _Truth) -> list[Order]:
    """The stops `book` had live that `truth` no longer lists as standing.

    As the fresh listing records them: a cancelled stop for what it still
    covered, a filled one for nothing. One the listing no longer returns at
    all cannot be sized, and is left for `_verdict` to report.
    """
    gone = []
    for order in book.live:
        if not _is_protective(order) or order.id in truth.reserving_ids:
            continue
        final = truth.book.records.get(order.id)
        if _released(final) and _remaining(final) > QTY_EPSILON:
            gone.append(final)
    return gone


def _cancels_in_flight(release: _Release, truth: _Truth) -> list[str]:
    """Orders still standing whose cancel was sent, or may have been."""
    ids = [o.id for o in truth.book.unclear]
    blocker = release.blocker_id
    if release.cancel_in_flight and blocker in truth.reserving_ids and blocker not in ids:
        ids.append(blocker)
    return ids


def _unseen_cascades(
    book: _Book, truth: _Truth, release: _Release, in_flight: Sequence[str]
) -> tuple[list[str], int, float]:
    """Standing stops a released take-profit may be taking with it unseen.

    Where the listing does not show the pairing, every take-profit is released
    before any stop (`_release_order`), and one whose cancel landed takes its
    stop along at the broker, which reads `held` a while yet. A blocker on the
    next take-profit stops the pass there, so that stop is never looked at,
    and counting it as cover says `unchanged` over shares that are naked once
    the cascade lands. Each such take-profit takes one stop at most, and each
    stop of the first listing that has gone since may be the one it took.
    What is left may be any standing stop the listing shows alone, and the
    most it can strip is the largest of them per take-profit unaccounted for.

    Returns (the suspects, how many take-profits are unaccounted for, the
    shares at stake). A nested listing leaves none but where a limit sell
    placed by hand, paired with nothing, went before the blocker: there a stop
    follows its own take-profit, and one whose cancel did not land is the
    blocker. That one errs toward a page, as `pair_released` does.
    """
    released_alone = [
        after for _, after in release.pairs
        if not _is_protective(after) and release.alone(after.id)
    ]
    gone_alone = [
        o for o in book.live
        if _is_protective(o) and release.alone(o.id) and o.id not in truth.reserving_ids
    ]
    unseen = len(released_alone) - len(gone_alone)
    if unseen <= 0:
        return [], 0, 0.0
    suspects = [
        o for o in truth.book.standing
        if _is_protective(o) and o.id not in in_flight and release.pair_released(o)
    ]
    at_risk = sum(sorted((_remaining(o) for o in suspects), reverse=True)[:unseen])
    return [o.id for o in suspects], unseen, at_risk


def _verdict(
    ticker: str, detail: str, book: _Book, truth: _Truth, release: _Release, placed: _Placed
) -> CloseOutcome:
    """`unchanged` only if every held share a stop covered before is covered now.

    Counted, not assumed: the stops still standing, each pair once, plus the
    ones just re-armed, against what the first listing's stops covered or what
    is still held, whichever is less. Anything short of that is shares that
    had a stop when the close began and have none now.
    """
    covered = truth.book.stop_cover + placed.qty
    wanted = min(book.stop_cover, truth.held)
    if covered + QTY_EPSILON < wanted:
        detail += (
            f"; {wanted - covered:g} shares that had a stop have none "
            f"({covered:g} of {truth.held:g} held are covered)"
        )
        return _rearmed(ticker, "naked", detail, release, placed)
    return _rearmed(ticker, "unchanged", detail, release, placed)


def _rearmed(
    ticker: str, status: CloseStatus, detail: str, release: _Release, placed: _Placed
) -> CloseOutcome:
    if placed.errors:
        detail += f"; re-arm failed: {'; '.join(placed.errors)}"
    return CloseOutcome(ticker, status, detail, released=release.ids, rearmed=placed.ids)


def _sell_released(ctx: _Ctx) -> CloseOutcome:
    """Everything is released: size off a fresh read, sell, verify, re-arm on failure."""
    client, ticker, release = ctx.client, ctx.ticker, ctx.release
    try:
        position = client.get_position(ticker)
    except Exception as exc:  # noqa: BLE001 — no holding figure, no sell
        # No exit was sent, so if the holding stays unreadable the released
        # stops' own cap is safe: they are the only sellers there have been.
        return _rearm_outcome(
            ctx, f"holding unreadable: {exc}",
            blind_cap=release.blind_rearm_cap,
            blind_doubt=release.rearm_cap - release.blind_rearm_cap,
        )
    if position is not None and position.side != "long":
        return CloseOutcome(
            ticker, "unknown", f"position is {position.side}, not long: not sold",
            released=release.ids,
        )
    held = position.qty if position is not None else 0.0
    if held <= QTY_EPSILON:
        return CloseOutcome(
            ticker, "already_closed", "nothing held once released: a leg filled meanwhile",
            released=release.ids,
        )

    sent = _submit_exit(client, ticker, held, ctx.stamp)
    if sent.refused:
        # Turned down by the broker: no order exists under the stamp, so there
        # is nothing to look up and nothing a re-arm could stand beside.
        return _rearm_outcome(
            ctx, f"exit {ctx.stamp} refused: {sent.error}",
            blind_cap=min(release.rearm_cap, held),
        )
    return _settle_exit(ctx, held, sent)


def _settle_exit(ctx: _Ctx, held: float, sent: _Sent) -> CloseOutcome:
    """Find out what became of a sell the broker did not refuse, and act on that."""
    ticker, stamp, release = ctx.ticker, ctx.stamp, ctx.release
    exit_order, settled, lookup_error = _find_order(ctx.client, stamp, ctx.sleep)
    if exit_order is None and (not sent.error or not settled):
        # Accepted but not found, or not found and not ruled out: the sell may
        # be working, and a stop beside it is a second seller.
        return CloseOutcome(
            ticker, "unknown",
            f"exit {stamp} unverifiable: {lookup_error or 'accepted, but not found'} "
            f"(submit: {sent.error or 'ok'})",
            client_order_id=stamp, released=release.ids,
        )
    if exit_order is not None and exit_order.status.lower() not in FAILED_EXIT_STATUSES:
        return _exit_submitted(ctx, exit_order, f"sold {held:g} ({exit_order.status})")
    if exit_order is None:
        waited = sum(EXIT_LOOKUP_DELAYS_S)
        why = f"exit {stamp} not at the broker {waited:g}s after its submit failed: {sent.error}"
        return _rearm_outcome(ctx, why, blind_cap=None)
    why = f"exit {stamp} {exit_order.status}: {sent.error}"
    return _rearm_outcome(
        ctx, why, blind_cap=min(release.rearm_cap, held - exit_order.filled_qty)
    )


def _exit_submitted(ctx: _Ctx, exit_order: Order, detail: str) -> CloseOutcome:
    return CloseOutcome(
        ctx.ticker, "exit_submitted", detail,
        exit_order_id=exit_order.id, client_order_id=ctx.stamp, released=ctx.release.ids,
    )


def _submit_exit(client: AlpacaClient, ticker: str, qty: float, stamp: str) -> _Sent:
    """Send the stamped market sell, and say how sure the answer is.

    Only a refusal proves the order is absent. Anything else, a timeout above
    all, can lose the reply to an accepted order, so the caller settles it by
    looking the stamp up.
    """
    try:
        client.submit_order(
            symbol=ticker,
            qty=qty,
            side="sell",
            order_type="market",
            time_in_force="day",
            client_order_id=stamp,
        )
    except Exception as exc:  # noqa: BLE001 — resolved by the lookup that follows
        return _Sent(str(exc), refused=_refused(exc))
    return _Sent()


def _find_order(
    client: AlpacaClient, client_order_id: str, sleep: Sleep
) -> tuple[Order | None, bool, str]:
    """The order under a client id, looked up until found or the window closes.

    For the exit under its stamp, and for a re-armed stop whose POST got no
    answer. Returns (order, settled, error). Settled is True when the order was
    found, or when the last lookup, made after the window, answered that there
    is none. An error on that last lookup leaves it unsettled.
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
            return found, True, ""
        error = ""
    return None, not error, error


def _rearm_outcome(
    ctx: _Ctx, why: str, *, blind_cap: float | None, blind_doubt: float = 0.0
) -> CloseOutcome:
    """Put the released stops back, for what the broker says is held and free now.

    `blind_cap` is what may be re-armed when the broker's truth cannot be read:
    given only where no exit of ours can be working, so the released stops are
    the only sellers there have been. None where an exit may yet land: a stop
    re-armed there without a fresh read could end up on a flat book, and even
    with one, the exit can land between that read and the re-arm, which is
    what `_exit_landed` looks for. `blind_doubt` is what the stops covered that
    `blind_cap` leaves out without knowing those shares are gone: a blind
    re-arm short of it is `unknown`, not `unchanged`.

    Where an exit may yet land, the outcome carries its stamp whatever it
    says. `_exit_landed` closes the window only when a re-armed stop stands:
    it reserves the shares, so a later landing is refused. When nothing was
    re-armed (a naked lot, a stop that fired in the release, a refused re-arm,
    a holding that could not be read), nothing reserves them, and a back-fill
    placed after this close is the next sell the exit can land beside. The
    stamp is what tells the caller to place it through `cover_beside_exit`.
    """
    outcome = _rearm_off_truth(ctx, why, blind_cap, blind_doubt)
    if blind_cap is None and outcome.client_order_id is None:
        outcome = replace(outcome, client_order_id=ctx.stamp)
    return outcome


def _rearm_off_truth(
    ctx: _Ctx, why: str, blind_cap: float | None, blind_doubt: float
) -> CloseOutcome:
    client, ticker, release = ctx.client, ctx.ticker, ctx.release
    truth = _read_truth(client, ticker, ctx.sleep)
    if isinstance(truth, str):
        if blind_cap is None:
            return CloseOutcome(
                ticker, "unknown", f"{why}; nothing re-armed, holding unreadable: {truth}",
                released=release.ids,
            )
        return _rearm_blind(ctx, why, blind_cap, blind_doubt)
    settled = _no_rearm(ticker, truth, why, release)
    if settled is not None:
        return settled
    placed = _rearm(
        ctx, release.protective, min(release.rearm_cap, truth.room),
        beside_exit=blind_cap is None,
    )
    if blind_cap is None:
        landed = _exit_landed(ctx, why, placed)
        if landed is not None:
            return landed
    if placed.errors:
        return _rearmed(ticker, "naked", why, release, placed)
    return _verdict(ticker, why, ctx.book, truth, release, placed)


def _rearm_blind(ctx: _Ctx, why: str, cap: float, doubt: float) -> CloseOutcome:
    """Put the released stops back for `cap` shares, with nothing read to check it by."""
    placed = _rearm(ctx, ctx.release.protective, cap)
    if placed.errors or doubt <= QTY_EPSILON:
        status: CloseStatus = "naked" if placed.errors else "unchanged"
        return _rearmed(ctx.ticker, status, why, ctx.release, placed)
    why += (
        f"; {doubt:g} shares a stop covered were not put back: a sell paired "
        "with no stop sold during the release, and with the holding unreadable it "
        "cannot be told whether it took their shares or its own"
    )
    return _rearmed(ctx.ticker, "unknown", why, ctx.release, placed)


def _exit_landed(ctx: _Ctx, why: str, placed: _Placed) -> CloseOutcome | None:
    """After a re-arm an exit of ours may have met, look the stamp up once more.

    The exit's POST got no answer, and 23 s of lookups found nothing, so the
    stops went back off a fresh read. The exit can still land between that read
    and the re-arm reaching the broker. In regular hours it fills, and the stop
    just placed stands on a flat book: a short-sale stop on a margin account,
    and a state nothing reports. After hours it queues, and the re-arm is
    refused beside it, which is an exit on its way, not a naked lot. Once a
    re-armed stop stands, a later landing is refused (the stop reserves the
    shares the exit was sized for), so this one lookup closes the window.

    When the stamp cannot be looked up, the listing and the holding answer
    instead: the exit is in the listing if it landed, and a flat book means the
    lot is gone whichever sell took it. Either way nothing may stand on it.

    None when the exit is not there, or is dead, and shares are still held: the
    re-arm stands.
    """
    found, error = _lookup_exit(ctx)
    flat = False
    if error:
        truth = _read_truth(ctx.client, ctx.ticker, ctx.sleep)
        if isinstance(truth, str):
            if not placed.ids and not placed.unresolved:
                return None
            return _rearmed(
                ctx.ticker, "unknown",
                f"{why}; re-armed, but neither exit {ctx.stamp} nor the book could be "
                f"read again, so a stop on a flat book is not ruled out: {error}; {truth}",
                ctx.release, placed,
            )
        found = _stamped(truth.book, ctx.stamp)
        flat = truth.side != "long" or truth.held <= QTY_EPSILON
    if not flat and (found is None or found.status.lower() in FAILED_EXIT_STATUSES):
        return None
    return _withdraw(ctx, why, found, placed)


def _stamped(book: _Book, stamp: str) -> Order | None:
    return next((o for o in book.records.values() if o.client_order_id == stamp), None)


def _withdraw(
    ctx: _Ctx, why: str, exit_order: Order | None, placed: _Placed
) -> CloseOutcome:
    """The lot is sold, or selling: take back every other sell standing on it.

    Read off a fresh listing, not off what this close knows it placed. A stop
    whose POST lost its reply stands at the broker all the same, and once the
    exit fills it is a short-sale stop that nothing else would ever withdraw.
    `exit_submitted` is only returned when every one of them came off, and
    every re-arm this close could not settle is accounted for in the listing.
    """
    keep = exit_order.id if exit_order is not None else None
    targets = list(placed.ids)
    listed: set[str] = set()
    book = _read_listing(ctx)
    listing_error = book if isinstance(book, str) else ""
    if not isinstance(book, str):
        listed = {o.client_order_id for o in book.records.values()}
        targets += [
            o.id for o in book.standing
            if o.id != keep and o.client_order_id != ctx.stamp and o.id not in targets
        ]
    taken_back = [(oid, _await_cancel(ctx, oid)) for oid in targets]
    problems = [
        f"{oid}={o.status if o else 'unread'} did not come off cleanly"
        for oid, o in taken_back
        # Filled is not taken back: a stop that sold on a flat book is a short.
        if not _released(o) or (o is not None and o.filled_qty > QTY_EPSILON)
    ]
    problems += [
        f"re-arm {coid} went unanswered and is not in the listing"
        f"{f' ({listing_error})' if listing_error else ''}, so it may yet stand"
        for coid in placed.unresolved
        if coid not in listed
    ]
    problems += [
        f"re-arm {o.id} went unanswered and has sold {o.filled_qty:g} beside it"
        for o in ([] if isinstance(book, str) else book.records.values())
        if o.client_order_id in placed.unresolved and o.filled_qty > QTY_EPSILON
    ]
    if exit_order is not None:
        what = f"exit {exit_order.id} landed after all ({exit_order.status})"
    else:
        what = f"nothing is held, and exit {ctx.stamp} could not be looked up"
    if problems:
        return _rearmed(
            ctx.ticker, "unknown",
            f"{why}; {what}, and a sell beside it may stand on the book: {'; '.join(problems)}",
            ctx.release, placed,
        )
    detail = f"{why}; {what}"
    if targets:
        detail += f", and the stop(s) standing beside it are cancelled: {', '.join(targets)}"
    if exit_order is None:
        return CloseOutcome(ctx.ticker, "already_closed", detail, released=ctx.release.ids)
    return _exit_submitted(ctx, exit_order, detail)


def _read_listing(ctx: _Ctx) -> _Book | str:
    """The symbol's sells, retried like a truth read, or the error text."""
    error = ""
    for delay in TRUTH_READ_DELAYS_S:
        if delay:
            ctx.sleep(delay)
        try:
            return _read_book(ctx.client, ctx.ticker)
        except Exception as exc:  # noqa: BLE001 — retried, then reported
            error = str(exc)
    return error


def _lookup_exit(ctx: _Ctx) -> tuple[Order | None, str]:
    """The exit under the stamp, retried like a truth read: (order, error)."""
    error = ""
    for delay in TRUTH_READ_DELAYS_S:
        if delay:
            ctx.sleep(delay)
        try:
            return ctx.client.get_order_by_client_order_id(ctx.stamp), ""
        except Exception as exc:  # noqa: BLE001 — retried, then reported
            error = str(exc)
    return None, error


def _await_cancel(ctx: _Ctx, order_id: str) -> Order | None:
    """Cancel an order that must not stand, and keep at it until it is gone.

    Unlike the release, a refused DELETE is not taken at its word here. This
    order is a second seller (a stop beside an exit that landed, or on a book
    already flat), and a refusal such as a 429 leaves it working. So while it
    still reads as working, the DELETE is sent again at each poll, over the
    confirm window and then the settle window.
    """
    _send_cancel(ctx, order_id)
    last: Order | None = None
    for delay in (*_CONFIRM_DELAYS_S, *CANCEL_SETTLE_DELAYS_S):
        if delay:
            ctx.sleep(delay)
        try:
            last = ctx.client.get_order(order_id)
        except Exception:  # noqa: BLE001 — an unreadable order is an unconfirmed one
            continue
        if _released(last):
            return last
        if _working(last):
            _send_cancel(ctx, order_id)
    return last


def _send_cancel(ctx: _Ctx, order_id: str) -> None:
    try:
        ctx.client.cancel_order(order_id)
    except Exception as exc:  # noqa: BLE001 — the order's status decides, not the call
        log.warning("%-6s cancel of %s beside the exit: %s", ctx.ticker, order_id, exc)


def _no_rearm(
    ticker: str, truth: _Truth, detail: str, release: _Release
) -> CloseOutcome | None:
    """The outcome when the broker's truth rules a re-arm out, or None.

    Nothing held: closed, unless a sell still stands on the flat book, which is
    a short waiting to happen and not a close. Held short: not this module's
    position to protect.
    """
    if truth.side is None or truth.held <= QTY_EPSILON:
        if truth.reserving_ids:
            standing = ", ".join(sorted(truth.reserving_ids))
            return CloseOutcome(
                ticker, "unknown",
                f"{detail}; nothing held, yet {standing} still stands on the flat book",
                released=release.ids,
            )
        return CloseOutcome(
            ticker, "already_closed", f"{detail}; nothing held, so nothing re-armed",
            released=release.ids,
        )
    if truth.side != "long":
        return CloseOutcome(
            ticker, "unknown", f"{detail}; position is {truth.side}: nothing re-armed",
            released=release.ids,
        )
    return None


def _rearm(
    ctx: _Ctx, released: Sequence[Order], cap: float, *, beside_exit: bool = False
) -> _Placed:
    """Put back each released stop, as a plain GTC stop at the level it stood at.

    Plain `stop` whatever the original type: a stop-limit can be skipped in a
    gap, and a trailing stop's level is where it stood, not a trail to rebuild.
    Never more shares than `cap` in total, and a take-profit is not re-placed:
    beside a stop it would be a second sell on the same shares.

    Each carries a client id made from the exit stamp and the stop it replaces
    (`_rearm_id`), so a POST whose reply is lost is looked up like the exit
    rather than assumed absent: a timeout or a 5xx can hide a stop the broker
    placed, and if the exit then fills, that stop is a short-sale stop that
    nothing knows to withdraw. `beside_exit` when an exit of ours may still
    land: then a lookup that finds nothing does not settle it (`_place_stop`).
    """
    placed: list[str] = []
    errors: list[str] = []
    unresolved: list[str] = []
    remaining = cap
    for order in released:
        qty = min(_remaining(order), remaining)
        if qty <= QTY_EPSILON:
            continue
        if order.stop_price is None:
            errors.append(f"{order.id}: no stop price to re-place")
            continue
        coid = _rearm_id(ctx.stamp, order.id)
        new, error, settled = _place_stop(
            ctx, qty, order.stop_price, coid, trust_window=not beside_exit
        )
        if new is None:
            errors.append(f"{order.id}: {error}")
            if not settled:
                unresolved.append(coid)
            continue
        placed.append(new.id)
        remaining -= qty
    return _Placed(tuple(placed), cap - remaining, tuple(errors), tuple(unresolved))


def _rearm_id(stamp: str, replaced_id: str) -> str:
    """The client id of the stop that re-arms `replaced_id` in the close under `stamp`.

    Unique without a counter: a stop is released once, and a later close
    releases the stop this one placed, which has an id of its own.
    """
    return f"{stamp}-arm-{replaced_id}"


def _place_stop(
    ctx: _Ctx, qty: float, stop_price: float, coid: str, *, trust_window: bool = True
) -> tuple[Order | None, str, bool]:
    """Send one re-armed stop: (order, error, settled).

    Only a refusal proves the stop absent. Anything else is settled by looking
    the client id up, as the exit is, and a stop found working counts as placed.

    Beside an exit that may still land (`trust_window` False), a lookup window
    that finds nothing settles nothing. That exit's own POST is not taken as
    absent on the same window: `_exit_landed` looks for it again because it can
    land later. A stop posted beside it can land as late, after the exit has
    filled, and a sell stop on a flat book is a short-sale stop. Left
    unresolved, it is looked for once more when the exit is found landed, and
    while it cannot be ruled out the outcome is not a close.
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
        ), "", True
    except Exception as exc:  # noqa: BLE001 — refused, or looked up below
        if _refused(exc):
            return None, str(exc), True
        sent = exc
    found, settled, lookup_error = _find_order(ctx.client, coid, ctx.sleep)
    if _working(found):
        return found, "", True
    if found is not None:
        return None, f"{sent} (then {found.status})", True
    if settled:
        late = "" if trust_window else ", and it may land yet"
        waited = sum(EXIT_LOOKUP_DELAYS_S)
        return None, f"{sent} (not at the broker {waited:g}s later{late})", trust_window
    return None, f"{sent} (unresolved: {lookup_error})", False
