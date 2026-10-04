"""The machinery behind test_time_exit_model.py: an adversarial Alpaca, and the explorers.

Read the test module first; it states the invariants and what each run means.
This module has three parts.

`ModelBroker` is a stateful Alpaca for one long lot. It keeps the broker's
rules that make a protected close hard: a live sell reserves its shares, and a
sell for more than is left unreserved is refused (403 held_for_orders) while
the lot is long; on a flat book a sell is accepted as a short sale, as a
margin account takes it. A bracket's take-profit and stop reserve the same
shares once, and a fill or a cancel of one leg takes the other with it.
client_order_id is unique, and looked up by `get_order_by_client_order_id`.

Every call it answers while ARMED is a choice point. The chooser picks an
environment event that happens first (a stop, a take-profit or our exit fills,
fully or half; a request that is still in flight lands) and an outcome for the
call itself: answered, timed out after its effect, timed out without it, timed
out with the effect still in flight (it lands when a later choice says so, or
overnight), a cancel left `pending_cancel` (for two calls, or until a later
choice or the night settles it, landing or not), a bracket sibling cancelled
with its leg, left live, or left pending, a 4xx refusal (429, 422). Choice 0
everywhere is a clean, prompt broker.

`Harness` runs `scripts.manage_positions.main()` itself, with the flags the
daily run passes, against that broker: run 1 on day D at 22:30 UTC plans a
time exit, and the broker is armed from the moment `close_with_protection`
starts until the run ends (the same-run re-cover is under fire too). Or run 1
is that run replayed past 00:00 UTC (`CATCH_UP_AT`), before D+1's open: its
trade date is then D+1's, the same as run 2's. Then the night (whatever is
still in flight lands, a stuck cancel lands or never does), the open (our
queued market exit fills, half-fills, or is rejected, expired or cancelled; a
gap down fills every standing stop or none; or no session at all, an exchange
holiday on a weekday, and a queued exit still waits), and run 2 on D+1 with a
clean broker, in two variants: the lot is still due for its time exit, or it
has moved and only its stops are maintained (the back-fill pass).

The explorers enumerate fault schedules (`explore_bounded`: every schedule with
at most K non-clean choices, the night and the open enumerated in full) or draw
them (`explore_random`, seeded), check the invariants, and keep one minimal
counterexample per violation class (`minimize`, `render`).
"""

from __future__ import annotations

import dataclasses
import itertools
import logging
import random
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as daytime
from types import SimpleNamespace

import httpx

from scripts import manage_positions as mp
from tradingagents_us import storage
from tradingagents_us.dataflows.alpaca_broker import (
    AlpacaRequestError,
    Clock,
    FillActivity,
    Order,
    Position,
    Session,
)
from tradingagents_us.execution import protected_close as pc
from tradingagents_us.execution.executor import derive_exit_client_order_id
from tradingagents_us.risk.position_manager import Bar

SYMBOL = "XOM"
QTY = 10.0
AVG = 100.0
EPS = 1e-9

#: Run 1 is the daily run of a Thursday, after the US close; the open and run 2
#: are Friday's. Entered 50 days before, so the lot is well past its age window.
RUN1_AT = datetime(2026, 10, 1, 22, 30, tzinfo=UTC)
OPEN_AT = datetime(2026, 10, 2, 13, 30, tzinfo=UTC)
RUN2_AT = datetime(2026, 10, 2, 22, 30, tzinfo=UTC)
ENTRY_AT = RUN1_AT - timedelta(days=50)

#: Run 1 replayed past 00:00 UTC, before Friday's open: Thursday's 22:30 run
#: fired late (ai-trader.timer is Persistent=true, so a box that was down at
#: 22:30 runs it at boot), or was rerun by hand. Its trade date is Friday's,
#: which is run 2's too, and its exit still queues for Friday's open.
CATCH_UP_AT = datetime(2026, 10, 2, 0, 30, tzinfo=UTC)

#: A session's regular hours in UTC, as the model's clock and calendar keep
#: them (October: New York is on daylight time).
SESSION_OPEN = daytime(13, 30)
SESSION_CLOSE = daytime(20, 0)

#: The daily run's flags (scripts/daily_run.sh), with --submit.
ARGV = ["--submit", "--backfill-stops", "--refresh-bars"]

#: Mark and tape per variant. `due`: flat for 30+ bars, so the time exit fires.
#: `moved`: up 6% on bars that back it, so only the stop logic runs.
MARK = {"due": 100.5, "moved": 106.0}
TAPE = {"due": 100.0, "moved": 106.0}
RUN2_VARIANTS = ("due", "moved")

#: Seconds one broker round trip takes, for the trace's clock only.
CALL_S = 0.05

#: Statuses in which Alpaca holds a sell's shares back (held_for_orders).
RESERVING = frozenset(
    {"new", "accepted", "pending_new", "held", "partially_filled", "pending_cancel",
     "pending_replace"}
)
CANCELABLE = RESERVING - {"pending_cancel"}
#: A sell in one of these covers its shares and is not on its way out.
COVERING = frozenset({"new", "accepted", "pending_new", "held", "partially_filled"})

READ_OUTCOMES = ("ok", "timeout")
CANCEL_OUTCOMES = (
    "ok", "pending", "pending_stuck", "timeout_applied", "timeout_lost", "timeout_late", "429",
)
SIBLING_OUTCOMES = ("ok+sib_kept", "ok+sib_pending")
DEAD_CANCEL_OUTCOMES = ("ok", "timeout_lost")
SUBMIT_OUTCOMES = ("ok", "timeout_applied", "timeout_lost", "timeout_soon", "timeout_late", "422")

#: Write outcomes the broker settles by itself: a refusal, or a reply lost
#: whether the request was carried out, is still on its way, or never arrived.
#: Not a cancel left pending, nor a bracket sibling left behind: those leave
#: the broker itself in doubt, and the close is right to say it cannot tell.
SETTLED_WRITE_FAULTS = frozenset(
    {"422", "429", "timeout_lost", "timeout_applied", "timeout_soon", "timeout_late"}
)

#: How a queued market exit ends at the open, and whether a gap fills the stops.
#: `half` sells half and expires the rest; `half_done_for_day` and
#: `half_calculated` sell half and leave the rest in an end-of-day state,
#: which Alpaca can report for a day order the session is done with: neither
#: working nor, by its name, gone, and as unable to sell again as `expired`.
OPEN_EXIT_OUTCOMES = (
    "fill", "half", "rejected", "expired", "canceled", "half_done_for_day", "half_calculated",
)
OPEN_GAP = ("hold", "gap")
#: No session before run 2: the daily timer runs Mon-Fri and daily_run.sh skips
#: weekends only, so on a weekday exchange holiday nothing fills, nothing
#: expires, and an exit queued the night before still waits for an open.
OPEN_HOLIDAY = ("holiday", "hold")

#: (environment event or None, outcome of the call).
Choice = tuple[str | None, str]
CLEAN: Choice = (None, "ok")

#: The start books. Each is one 10-share long lot of XOM bought at 100.
BOOKS = ("stop", "bracket", "two_stops", "partial", "queued", "ratcheted")

#: The `queued` book's exit: stamped the trade date before run 1's.
PREV_STAMP = derive_exit_client_order_id(SYMBOL, (RUN1_AT - timedelta(days=1)).date(), "time")


# --------------------------------------------------------------------------- broker


@dataclass(slots=True)
class Rec:
    id: str
    coid: str
    side: str
    type: str
    qty: float
    status: str
    tif: str = "gtc"
    filled: float = 0.0
    stop_price: float | None = None
    limit_price: float | None = None
    #: Listed nested under this order (a bracket's legs under its entry).
    parent: str | None = None
    #: Orders sharing a group reserve the same shares once (bracket/OCO legs).
    group: str | None = None
    role: str = ""
    #: Status before a cancel left it pending, for a cancel that never lands.
    prior: str | None = None
    #: When the broker took it, as Alpaca's `submitted_at` reports it.
    submitted: datetime = ENTRY_AT

    @property
    def remaining(self) -> float:
        return max(0.0, self.qty - self.filled)


@dataclass(slots=True)
class Latent:
    """A request the broker has not carried out yet."""

    kind: str  # "cancel" (sent, not processed) | "pending" (pending_cancel) | "post"
    order_id: str | None = None
    kw: dict | None = None
    #: Calls until it lands on its own; None: only a choice or the night lands it.
    ticks: int | None = None

    def label(self, names: Callable[[str], str]) -> str:
        if self.kind == "post":
            return f"POST {self.kw['order_type']} {self.kw['client_order_id'] or ''}".strip()
        return f"{self.kind} {names(self.order_id or '')}"


def _timeout() -> httpx.ReadTimeout:
    return httpx.ReadTimeout("model: the reply was lost")


def _not_found(path: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", f"https://paper-api.alpaca.markets/v2{path}")
    return httpx.HTTPStatusError(
        "404 Not Found", request=request, response=httpx.Response(404, request=request)
    )


class ModelBroker:
    """One long lot at an Alpaca whose every answer the chooser decides (see module doc)."""

    def __init__(self) -> None:
        self.held = QTY
        self.mark = MARK["due"]
        self.recs: dict[str, Rec] = {}
        self.fills: list[FillActivity] = [
            FillActivity("fill-buy", SYMBOL, "buy", QTY, AVG, ENTRY_AT, "entry-A")
        ]
        self.latent: list[Latent] = []
        self.base_time = RUN1_AT
        self.elapsed = 0.0
        self.armed = False
        self.n = 0
        self.next_id = 1
        #: (index, kind, env options, outcomes, the call as the trace names it).
        self.chooser: Callable[[int, str, tuple[str, ...], tuple[str, ...], str], Choice] = (
            lambda *_: CLEAN
        )
        #: Choice points seen while armed: (index, kind, env options, outcomes).
        self.points: list[tuple[int, str, tuple[str, ...], tuple[str, ...]]] = []
        #: (kind, event, outcome) of every choice that was not clean, as made.
        self.applied: list[tuple[str, str | None, str]] = []
        #: (class, message) for every violation seen, in order.
        self.violations: list[tuple[str, str]] = []
        self.outcomes: list[tuple[str, pc.CloseOutcome]] = []
        self.sold = 0.0
        #: Shares sold by anything but our exit: cover a stop gave up by selling.
        self.sold_by_protection = 0.0
        self.in_run_fill = False
        #: What the clock says during run 1. The daily run is after the close.
        self.market_open = False
        self.tracing = False
        self.events: list[str] = []
        self.stage = "run 1"
        #: Our exits the open refused, expired, cancelled or only half filled:
        #: the lot had neither stop nor exit for that session.
        self.missed_at_open: list[str] = []
        #: Weekdays with no session (`OPEN_HOLIDAY`): the clock and the
        #: calendar skip them.
        self.holidays: set[date] = set()

    # ---- plumbing ----------------------------------------------------------

    def __enter__(self) -> ModelBroker:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def clone(self) -> ModelBroker:
        other = ModelBroker.__new__(ModelBroker)
        other.__dict__.update(self.__dict__)
        other.recs = {k: dataclasses.replace(r) for k, r in self.recs.items()}
        other.fills = list(self.fills)
        other.latent = [dataclasses.replace(lat, kw=dict(lat.kw) if lat.kw else None)
                        for lat in self.latent]
        other.points = []
        other.applied = list(self.applied)
        other.violations = list(self.violations)
        other.outcomes = list(self.outcomes)
        other.events = list(self.events)
        other.missed_at_open = list(self.missed_at_open)
        other.holidays = set(self.holidays)
        other.chooser = lambda *_: CLEAN
        other.armed = False
        return other

    def now(self) -> datetime:
        return self.base_time + timedelta(seconds=self.elapsed)

    def sleep(self, seconds: float) -> None:
        self.elapsed += seconds

    def _new_id(self, role: str) -> str:
        oid = f"{role}-{self.next_id:02d}"
        self.next_id += 1
        assert oid not in self.recs, f"model: order id {oid} reused"
        return oid

    def name(self, order_id: str) -> str:
        rec = self.recs.get(order_id)
        return f"{order_id}" if rec is None else f"{order_id}[{rec.status}]"

    def trace(self, line: str) -> None:
        if self.tracing:
            self.events.append(line)

    def flag(self, cls: str, message: str) -> None:
        self.violations.append((cls, message))
        self.trace(f"     !! {cls}: {message}")

    def _step(self, kind: str, what: str, target: Rec | None = None) -> str:
        """One broker call: time passes, then maybe an event, then the call's outcome."""
        self.elapsed += CALL_S
        self._tick()
        if not self.armed:
            if self.tracing and kind not in ("read",):
                self.events.append(f"     {self.stage}: {what}")
            return "ok"
        idx = self.n
        self.n += 1
        env_opts = self._env_options()
        outs = self._outcomes(kind, target)
        env, out = self.chooser(idx, kind, env_opts, outs, what)
        if env is not None and env not in env_opts:
            env = None
        if out not in outs:
            out = "ok"
        self.points.append((idx, kind, env_opts, outs))
        if (env, out) != CLEAN:
            self.applied.append((kind, env, out))
        if env is not None:
            self.trace(f"#{idx:<3} t={self.elapsed:6.2f}  [event] {self._describe_env(env)}")
            self._env(env)
        self.trace(f"#{idx:<3} t={self.elapsed:6.2f}  {what} -> {out}")
        return out

    def _tick(self) -> None:
        for lat in list(self.latent):
            if lat.ticks is None:
                continue
            lat.ticks -= 1
            if lat.ticks <= 0:
                self.latent.remove(lat)
                self._land(lat)

    def _outcomes(self, kind: str, target: Rec | None) -> tuple[str, ...]:
        if kind == "read":
            return READ_OUTCOMES
        if kind == "submit":
            return SUBMIT_OUTCOMES
        if kind == "cancel":
            if target is None or target.status not in CANCELABLE:
                return DEAD_CANCEL_OUTCOMES
            if self._siblings(target, CANCELABLE):
                return CANCEL_OUTCOMES + SIBLING_OUTCOMES
            return CANCEL_OUTCOMES
        return ("ok",)

    # ---- the book ------------------------------------------------------------

    def add(self, rec: Rec) -> None:
        self.recs[rec.id] = rec

    def sells(self, statuses: frozenset[str] = RESERVING) -> list[Rec]:
        return [
            r for r in self.recs.values()
            if r.side == "sell" and r.status in statuses and r.remaining > EPS
        ]

    def _per_group(self, recs: Iterable[Rec]) -> float:
        by: dict[str, float] = {}
        for r in recs:
            key = r.group or r.id
            by[key] = max(by.get(key, 0.0), r.remaining)
        return sum(by.values())

    def reserved(self) -> float:
        return self._per_group(self.sells())

    def stop_cover(self) -> float:
        return self._per_group(r for r in self.sells(COVERING) if r.type == "stop")

    def exit_cover(self) -> float:
        return self._per_group(r for r in self.sells(COVERING) if r.type == "market")

    def _siblings(self, rec: Rec, statuses: frozenset[str]) -> list[Rec]:
        if rec.group is None:
            return []
        return [
            r for r in self.recs.values()
            if r.group == rec.group and r.id != rec.id and r.side == "sell"
            and r.status in statuses
        ]

    def _fill(self, rec: Rec, qty: float, why: str) -> None:
        qty = min(qty, rec.remaining)
        if qty <= EPS:
            return
        rec.filled += qty
        rec.status = "filled" if rec.remaining <= EPS else "partially_filled"
        self.held -= qty
        self.sold += qty
        if rec.role != "exit":
            self.sold_by_protection += qty
        if self.stage == "run 1":
            self.in_run_fill = True
        price = rec.stop_price or rec.limit_price or self.mark
        self.fills.append(FillActivity(
            f"fill-{self._new_id('f')}", SYMBOL, "sell", qty, price, self.now(), rec.id
        ))
        self.trace(f"     {rec.role} {rec.id} sells {qty:g} ({why}): held {self.held:g}")
        for sib in self._siblings(rec, RESERVING):
            if rec.status == "filled":
                sib.status = "canceled"
            else:
                sib.qty = max(sib.filled, sib.qty - qty)
        if self.held < -EPS:
            self.flag(
                "I1 short",
                f"{rec.role} {rec.id} sold {qty:g} ({why}) and left the account short "
                f"{-self.held:g} at {self.stage}",
            )

    def _cancel_now(self, rec: Rec, siblings: str = "cancel") -> None:
        rec.status = "canceled"
        for sib in self._siblings(rec, RESERVING):
            if siblings == "cancel" or sib.status == "pending_cancel":
                sib.status = "canceled"
            elif siblings == "pending":
                sib.prior, sib.status = sib.status, "pending_cancel"
                self.latent.append(Latent("pending", sib.id))

    def _pend(self, rec: Rec, ticks: int | None) -> None:
        rec.prior, rec.status = rec.status, "pending_cancel"
        self.latent.append(Latent("pending", rec.id, ticks=ticks))

    def _land(self, lat: Latent) -> None:
        if lat.kind == "post":
            try:
                rec = self._accept(lat.kw or {})
            except AlpacaRequestError as exc:
                self.trace(
                    f"     in-flight {lat.label(self.name)} lands: refused {exc.status_code}"
                )
                return
            self.trace(f"     in-flight {lat.label(self.name)} lands: {rec.id} {rec.status}")
            return
        rec = self.recs[lat.order_id or ""]
        # A sent cancel lands on anything still standing; a pending one only on
        # itself (a fill since then has settled it).
        cancelable = RESERVING if lat.kind == "cancel" else frozenset({"pending_cancel"})
        if rec.status in cancelable:
            self._cancel_now(rec)
        self.trace(f"     in-flight {lat.label(self.name)} lands")

    def _accept(self, kw: dict) -> Rec:
        """Alpaca's admission rules for a new order, then the order."""
        coid = kw.get("client_order_id") or f"broker-{self.next_id}"
        if any(r.coid == coid for r in self.recs.values()):
            raise AlpacaRequestError(
                "POST", "/orders", 422,
                '{"code":40010001,"message":"client_order_id must be unique"}',
            )
        qty = float(kw["qty"])
        order_type = kw.get("order_type", "market")
        if kw["side"] == "sell" and self.held > EPS:
            reserved = self.reserved()
            available = self.held - reserved
            if qty > available + EPS:
                raise AlpacaRequestError(
                    "POST", "/orders", 403,
                    f'{{"code":40310000,"message":"insufficient qty available for order",'
                    f'"held_for_orders":"{reserved:g}","available":"{max(available, 0):g}"}}',
                )
        if "-arm-" in coid:
            role = "rearm"
        elif "-cover" in coid:
            role = "cover"
        elif coid.startswith("tr-exit-"):
            role = "exit"
        else:
            role = "backfill" if order_type == "stop" else "sell"
        rec = Rec(
            id=self._new_id(role), coid=coid, side=kw["side"], type=order_type, qty=qty,
            # After the close a market order queues for the open; a GTC stop rests.
            status="accepted" if order_type == "market" else "new",
            tif=kw.get("time_in_force", "day"), stop_price=kw.get("stop_price"),
            limit_price=kw.get("limit_price"), role=role, submitted=self.now(),
        )
        self.recs[rec.id] = rec
        return rec

    # ---- environment events ----------------------------------------------------

    def _candidates(self) -> dict[str, list[Rec]]:
        live = self.sells()
        return {
            "stop": [r for r in live if r.type == "stop"],
            "exit": [r for r in live if r.type == "market"],
            "tp": [r for r in live if r.type == "limit"],
        }

    def _env_options(self) -> tuple[str, ...]:
        """What may happen before a call: a fill only while the clock says open.

        Alpaca triggers stops and fills GTC take-profits in regular hours
        only, and queues a market order sent outside them for the open. A
        model that fills them while its own clock says shut is not one Alpaca
        can be, so fills follow `market_open`, and the explorers run both.
        """
        opts: list[str] = []
        if self.market_open:
            for kind, recs in self._candidates().items():
                for i, r in enumerate(recs):
                    opts.append(f"fill:{kind}#{i}")
                    if kind != "tp" and r.remaining >= 2:
                        opts.append(f"half:{kind}#{i}")
        opts += [f"land:{i}" for i in range(len(self.latent))]
        return tuple(opts)

    def _describe_env(self, env: str) -> str:
        what, _, arg = env.partition(":")
        if what == "land":
            return f"lands: {self.latent[int(arg)].label(self.name)}"
        kind, _, i = arg.partition("#")
        rec = self._candidates()[kind][int(i)]
        return f"{'half of ' if what == 'half' else ''}{rec.role} {rec.id} fills"

    def _env(self, env: str) -> None:
        what, _, arg = env.partition(":")
        if what == "land":
            self._land(self.latent.pop(int(arg)))
            return
        kind, _, i = arg.partition("#")
        rec = self._candidates()[kind][int(i)]
        qty = rec.remaining if what == "fill" else float(int(rec.remaining // 2))
        self._fill(rec, qty, "in the run")

    # ---- reads ---------------------------------------------------------------

    def _order(self, rec: Rec, legs: tuple[Order, ...] = ()) -> Order:
        return Order(
            id=rec.id, client_order_id=rec.coid, symbol=SYMBOL, side=rec.side,  # type: ignore[arg-type]
            qty=rec.qty, filled_qty=rec.filled, order_type=rec.type,  # type: ignore[arg-type]
            status=rec.status, submitted_at=rec.submitted,
            filled_avg_price=self.mark if rec.filled else None,
            limit_price=rec.limit_price, stop_price=rec.stop_price, legs=legs,
        )

    def _position(self) -> Position | None:
        if abs(self.held) <= EPS:
            return None
        return Position(
            symbol=SYMBOL, qty=self.held, side="long" if self.held > 0 else "short",
            avg_entry_price=AVG, market_value=self.held * self.mark,
            unrealized_pl=self.held * (self.mark - AVG), unrealized_plpc=self.mark / AVG - 1.0,
        )

    def _read(self, what: str) -> None:
        if self._step("read", what) == "timeout":
            raise _timeout()

    def _session(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def clock(self) -> Clock:
        """Alpaca's market clock: open or not, and the next open (13:30 UTC, a session day)."""
        self._read("clock()")
        now = self.now()
        nxt = datetime.combine(now.date(), SESSION_OPEN, UTC)
        while nxt <= now or not self._session(nxt.date()):
            nxt += timedelta(days=1)
        return Clock(
            is_open=self.market_open, timestamp=now.isoformat(),
            next_open=nxt.isoformat(),
            next_close=datetime.combine(nxt.date(), SESSION_CLOSE, UTC).isoformat(),
        )

    def calendar(self, start: date, end: date) -> list[Session]:
        """Alpaca's trading calendar: every session day from `start` to `end`, holidays skipped."""
        self._read(f"calendar({start}, {end})")
        days = (start + timedelta(days=k) for k in range((end - start).days + 1))
        return [
            Session(
                d, datetime.combine(d, SESSION_OPEN, UTC), datetime.combine(d, SESSION_CLOSE, UTC)
            )
            for d in days if self._session(d)
        ]

    def list_positions(self) -> list[Position]:
        self._read("list_positions()")
        pos = self._position()
        return [pos] if pos is not None else []

    def get_position(self, symbol: str) -> Position | None:
        self._read(f"get_position({symbol})")
        return self._position() if symbol == SYMBOL else None

    def get_order(self, order_id: str) -> Order:
        self._read(f"get_order({order_id})")
        rec = self.recs.get(order_id)
        if rec is None:
            raise _not_found(f"/orders/{order_id}")
        return self._order(rec)

    def get_order_by_client_order_id(self, client_order_id: str) -> Order | None:
        self._read(f"get_order_by_client_order_id({client_order_id})")
        rec = next((r for r in self.recs.values() if r.coid == client_order_id), None)
        return self._order(rec) if rec is not None else None

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False):
        self._read(f"list_orders({status})")
        if not nested:
            return [self._order(r) for r in self.recs.values()]
        legs: dict[str, list[Rec]] = {}
        for r in self.recs.values():
            if r.parent is not None:
                legs.setdefault(r.parent, []).append(r)
        return [
            self._order(r, tuple(self._order(leg) for leg in legs.get(r.id, ())))
            for r in self.recs.values() if r.parent is None
        ]

    def list_fill_activities(self) -> list[FillActivity]:
        self._read("list_fill_activities()")
        return list(self.fills)

    # ---- writes --------------------------------------------------------------

    def _check_release(self, rec: Rec | None) -> None:
        """I6: a cancel goes to protection only, a stop or a bracket leg."""
        if (
            rec is not None and rec.side == "sell" and rec.type != "stop"
            and rec.group is None and rec.status in CANCELABLE
        ):
            self.flag(
                "I6 a working sell that is neither a stop nor a bracket leg cancelled",
                f"{self.stage}: cancel_order({rec.role} {rec.id}[{rec.status}] "
                f"{rec.type} {rec.remaining:g})",
            )

    def cancel_order(self, order_id: str) -> dict:
        rec = self.recs.get(order_id)
        self._check_release(rec)
        out = self._step("cancel", f"cancel_order({self.name(order_id)})", rec)
        path = f"/orders/{order_id}"
        if rec is None:
            raise AlpacaRequestError("DELETE", path, 404, "order not found")
        if out == "timeout_lost":
            raise _timeout()
        if out == "429":
            raise AlpacaRequestError("DELETE", path, 429, "rate limit exceeded")
        if out == "timeout_late":
            self.latent.append(Latent("cancel", order_id))
            raise _timeout()
        if rec.status not in CANCELABLE:
            if out == "timeout_applied":
                raise _timeout()
            self.trace(f"     -> 422 not cancelable ({rec.status})")
            raise AlpacaRequestError(
                "DELETE", path, 422, f'{{"message":"order is {rec.status}, not cancelable"}}'
            )
        if out == "pending":
            self._pend(rec, ticks=2)
        elif out == "pending_stuck":
            self._pend(rec, ticks=None)
        elif out == "ok+sib_kept":
            self._cancel_now(rec, "kept")
        elif out == "ok+sib_pending":
            self._cancel_now(rec, "pending")
        else:
            self._cancel_now(rec)
        if out == "timeout_applied":
            raise _timeout()
        return {}

    def submit_order(self, **kw) -> Order:
        kw = {"client_order_id": None, "order_type": "market", **kw}
        what = (
            f"submit_order({kw['order_type']} {kw['side']} {float(kw['qty']):g}"
            + (f" @{kw['stop_price']:g}" if kw.get("stop_price") else "")
            + (f" {kw['client_order_id']}" if kw["client_order_id"] else "") + ")"
        )
        out = self._step("submit", what)
        if out == "timeout_lost":
            raise _timeout()
        if out == "422":
            raise AlpacaRequestError("POST", "/orders", 422, '{"message":"model: refused"}')
        if out in ("timeout_soon", "timeout_late"):
            self.latent.append(Latent("post", kw=kw, ticks=2 if out == "timeout_soon" else None))
            raise _timeout()
        try:
            rec = self._accept(kw)
        except AlpacaRequestError as exc:
            self.trace(f"     -> refused {exc.status_code}: {exc.body}")
            if out == "timeout_applied":
                raise _timeout() from None
            raise
        self.trace(f"     -> {rec.id} {rec.status}")
        if out == "timeout_applied":
            raise _timeout()
        return self._order(rec)

    def replace_order(self, order_id: str, **kw) -> Order:
        self._step("replace", f"replace_order({self.name(order_id)}, {kw})")
        old = self.recs[order_id]
        if old.status not in COVERING:
            raise AlpacaRequestError("PATCH", f"/orders/{order_id}", 422, "not replaceable")
        new = dataclasses.replace(
            old, id=self._new_id(old.role), qty=old.remaining, filled=0.0,
            stop_price=kw.get("stop_price", old.stop_price),
            status=old.status if old.status == "held" else "new", submitted=self.now(),
        )
        old.status = "replaced"
        self.recs[new.id] = new
        return self._order(new)

    def close_position(self, symbol: str) -> dict:
        raise AssertionError("the time exit must never DELETE /positions")

    def close_all_positions(self, cancel_orders: bool = True) -> list:
        raise AssertionError("the position pass must never flatten the account")

    # ---- the night and the open ---------------------------------------------------

    def night_choices(self) -> list[tuple[str, ...]]:
        """Every way the night can settle what is still in flight."""
        per = [("lands", "never") if lat.kind == "pending" else ("lands",) for lat in self.latent]
        return list(itertools.product(*per))

    def night(self, choice: Sequence[str]) -> None:
        """After run 1: in-flight requests land, a stuck cancel lands or never does."""
        self.stage = "night"
        for lat, how in zip(list(self.latent), choice, strict=True):
            if how == "never":
                rec = self.recs[lat.order_id or ""]
                if rec.status == "pending_cancel":
                    rec.status = rec.prior or "new"
                self.trace(f"     night: {lat.label(self.name)} never lands, it stands again")
            else:
                self._land(lat)
        self.latent = []

    def open_choices(self) -> list[tuple[str, str]]:
        exits = OPEN_EXIT_OUTCOMES if self._candidates()["exit"] else ("fill",)
        gaps = OPEN_GAP if self._candidates()["stop"] else ("hold",)
        return [*itertools.product(exits, gaps), OPEN_HOLIDAY]

    def open_market(self, choice: tuple[str, str]) -> None:
        """The next session: queued exits execute or die, a gap fills the stops, day orders end.

        Or none (`OPEN_HOLIDAY`): the book run 2 meets is the one the night left.
        """
        how, gap = choice
        self.stage = "open"
        self.base_time, self.elapsed = OPEN_AT, 0.0
        if choice == OPEN_HOLIDAY:
            self.stage = "holiday"
            self.holidays.add(OPEN_AT.date())
            self.trace("     no session: an exchange holiday, every order still as it was")
            return
        for rec in self._candidates()["exit"]:
            if how == "fill":
                self._fill(rec, rec.remaining, "at the open")
            elif how.startswith("half"):
                self._fill(rec, float(int(rec.remaining // 2)), "at the open")
                rec.status = how.removeprefix("half_") if how != "half" else "expired"
                self.trace(f"     {rec.role} {rec.id} ends the session {rec.status}")
            else:
                rec.status = how
                self.trace(f"     {rec.role} {rec.id} {how} at the open")
            if rec.remaining > EPS and rec.status != "filled":
                self.missed_at_open.append(f"{rec.id} {rec.status}")
        if gap == "gap":
            for rec in self._candidates()["stop"]:
                self._fill(rec, rec.remaining, "gap down at the open")
        for rec in self.sells():
            if rec.tif == "day":
                rec.status = "expired"

    # ---- what the state is -------------------------------------------------------

    def kind(self, cover_before: float) -> str:
        """flat | exiting | protected | naked | flat+sell | short | over-reserved.

        `protected`: every share a stop or a working exit of ours covered before
        (`cover_before`) that is still held, and that no stop or take-profit has
        sold since, has a live stop. An exit counts: a lot whose queued exit is
        cancelled and not replaced has lost its way out as surely as one whose
        stop went.
        """
        live = self.sells()
        if self.held < -EPS:
            return "short"
        if self.held <= EPS:
            return "flat+sell" if live else "flat"
        if self.reserved() > self.held + EPS:
            return "over-reserved"
        stops, exits = self.stop_cover(), self.exit_cover()
        if exits > EPS and stops + exits >= self.held - EPS:
            return "exiting"
        wanted = min(max(0.0, cover_before - self.sold_by_protection), self.held)
        if stops >= wanted - EPS:
            return "protected"
        return "naked"

    def settled_writes(self, since: int = 0) -> int | None:
        """How many writes went wrong in a run, when nothing else did; None otherwise.

        Nothing else: every read answered, no fill or late landing chosen, and
        each write that went wrong did so in a way the broker settles by itself
        (`SETTLED_WRITE_FAULTS`). Such a schedule hides nothing a careful
        close cannot read back, which is what I8 holds it to. Run 1's choices
        are `applied` from the start; a later run's from `since` on.
        """
        applied = self.applied[since:]
        for kind, env, out in applied:
            if env is not None or kind not in ("submit", "cancel"):
                return None
            if out not in SETTLED_WRITE_FAULTS:
                return None
        return len(applied)

    def describe(self) -> str:
        live = ", ".join(
            f"{r.role}:{r.id}[{r.status}] {r.remaining:g}"
            + (f"@{r.stop_price:g}" if r.stop_price else "")
            for r in self.sells()
        ) or "none"
        return f"held {self.held:g}, live sells: {live}"

    def key(self) -> tuple:
        """The state run 2 depends on: the holding and every sell, in the order sent.

        Dead sells too, not only those standing: run 2 reads them (an exit the
        open refused is reported, a bracket leg whose pair ended is in doubt),
        and which came after which.
        """
        group_names: dict[str, int] = {}
        sells = []
        for r in sorted(
            (r for r in self.recs.values() if r.side == "sell"), key=lambda r: (r.submitted, r.id)
        ):
            g = group_names.setdefault(r.group, len(group_names)) if r.group else None
            sells.append((r.type, r.role, r.status, round(r.remaining, 6), round(r.filled, 6),
                          r.stop_price, r.limit_price, r.tif, g, r.parent is not None))
        return (round(self.held, 6), tuple(sells))


def build_book(name: str) -> ModelBroker:
    """A 10-share long lot of XOM and its protection, as each kind of book has it."""
    b = ModelBroker()
    if name == "stop":
        b.add(Rec("stop-A", "coid-stop-A", "sell", "stop", 10.0, "new", stop_price=90.0,
                  role="stop"))
    elif name == "bracket":
        b.add(Rec("entry-A", "tr-buy-XOM-20260812", "buy", "market", 10.0, "filled",
                  filled=10.0, tif="gtc", role="entry"))
        b.add(Rec("tp-A", "coid-tp-A", "sell", "limit", 10.0, "new", limit_price=120.0,
                  parent="entry-A", group="entry-A", role="tp"))
        b.add(Rec("sl-A", "coid-sl-A", "sell", "stop", 10.0, "held", stop_price=90.0,
                  parent="entry-A", group="entry-A", role="sl"))
    elif name == "two_stops":
        b.add(Rec("stop-A", "coid-stop-A", "sell", "stop", 6.0, "new", stop_price=90.0,
                  role="stop"))
        b.add(Rec("stop-B", "coid-stop-B", "sell", "stop", 4.0, "new", stop_price=88.0,
                  role="stop"))
    elif name == "partial":
        b.add(Rec("stop-A", "coid-stop-A", "sell", "stop", 6.0, "new", stop_price=90.0,
                  role="stop"))
    elif name == "queued":
        # What the run after a time exit finds when no session came between (a
        # weekday exchange holiday, or a rerun past 00:00 UTC): that close
        # released the stop, and its exit still waits for an open.
        b.add(Rec("exit-prev", PREV_STAMP, "sell", "market", 10.0, "accepted", tif="day",
                  role="exit"))
    elif name == "ratcheted":
        # A bracket whose stop the position pass ratcheted (RatchetStop, through
        # replace_order). The old leg reads `replaced` under the filled entry;
        # its replacement keeps the take-profit's OCO link at the broker, but
        # the listing shows it at the top level, paired with nothing, as
        # tests/broker_fake.py lists a replacement. Which shape Alpaca's nested
        # listing has is not known here, and `replace_order` below keeps the
        # other one, so the start book carries this one.
        b.add(Rec("entry-A", "tr-buy-XOM-20260812", "buy", "market", 10.0, "filled",
                  filled=10.0, tif="gtc", role="entry"))
        b.add(Rec("tp-A", "coid-tp-A", "sell", "limit", 10.0, "new", limit_price=120.0,
                  parent="entry-A", group="entry-A", role="tp"))
        b.add(Rec("sl-A", "coid-sl-A", "sell", "stop", 10.0, "replaced", stop_price=90.0,
                  parent="entry-A", group="entry-A", role="sl"))
        b.add(Rec("sl-A2", "coid-sl-A2", "sell", "stop", 10.0, "held", stop_price=92.0,
                  group="entry-A", role="sl"))
    else:
        raise ValueError(name)
    return b


# --------------------------------------------------------------------------- harness


class _ClockDT(datetime):
    """`datetime` for scripts.manage_positions, whose `now()` is the run's time."""

    current: datetime = RUN1_AT

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206 — datetime's own signature
        return cls.current


class _NoRepo:
    """The bar cache is never read: every held name gets this pass's fresh bars."""

    def __init__(self, engine=None) -> None:  # noqa: ANN001
        pass

    def session(self):  # noqa: ANN201
        import contextlib

        return contextlib.nullcontext()


def _weekdays_until(day: date, n: int) -> list[date]:
    out: list[date] = []
    d = day
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def fresh_bars(at: datetime, variant: str) -> dict[str, tuple[list[Bar], list[date]]]:
    """45 daily bars up to the last session closed by `at`.

    A run past 00:00 UTC has no bar for its own UTC date: that session is
    still to come.
    """
    close = TAPE[variant]
    last = at.date() if at.time() >= SESSION_CLOSE else at.date() - timedelta(days=1)
    days = _weekdays_until(last, 45)
    return {SYMBOL: ([Bar(high=close + 1, low=close - 1, close=close) for _ in days], days)}


@dataclass(frozen=True)
class Run2:
    """What run 2 did: its exit code, the lot's kind after it, the book, what it flagged."""

    rc: int
    kind: str
    state: str
    flagged: tuple[tuple[str, str], ...]
    #: The outcome of each time exit run 2 closed: what it said to the log.
    closes: tuple[pc.CloseOutcome, ...] = ()
    #: `ModelBroker.settled_writes` of run 2: 0 for the clean broker run 2
    #: meets unless `Harness.run2_chooser` arms it.
    writes: int | None = 0


@dataclass(frozen=True)
class Scenario:
    """Everything that decides one end-to-end run: replayable."""

    book: str
    faults: tuple[tuple[int, Choice], ...]
    night: tuple[str, ...] | None = None
    opening: tuple[str, str] | None = None
    variant: str | None = None


@dataclass
class Finding:
    cls: str
    message: str
    scenario: Scenario


@dataclass
class Stats:
    schedules: int = 0
    scenarios: int = 0
    seconds: float = 0.0
    findings: dict[str, list[Finding]] = field(default_factory=dict)

    def add(self, finding: Finding) -> None:
        self.findings.setdefault(finding.cls, []).append(finding)

    def merge(self, other: Stats) -> None:
        self.schedules += other.schedules
        self.scenarios += other.scenarios
        self.seconds += other.seconds
        for cls, found in other.findings.items():
            self.findings.setdefault(cls, []).extend(found)

    @property
    def violations(self) -> int:
        return sum(len(v) for v in self.findings.values())


def _replay(faults: dict[int, Choice]):
    def choose(idx: int, kind: str, env_opts, outs, what: str = "") -> Choice:
        return faults.get(idx, CLEAN)

    return choose


def cover_wanted(book: str) -> float:
    """What the start book covers: shares a live stop, or a working exit of ours, holds back."""
    b = build_book(book)
    return b.stop_cover() + b.exit_cover()


class Harness:
    """Runs manage_positions.main() against a ModelBroker; see the module doc."""

    def __init__(self, monkeypatch) -> None:  # noqa: ANN001 — pytest.MonkeyPatch
        self.broker: ModelBroker = build_book("stop")
        self.variant = "due"
        self.arming = False
        #: When run 1 starts: the scheduled 22:30 UTC, or `CATCH_UP_AT`.
        self.run1_at = RUN1_AT
        #: Run 1 in the session instead of after the close: the clock says
        #: open, and the model may fill a stop, a take-profit or an exit.
        self.market_open = False
        #: Arms run 2 too, from its first close on: called once per run 2 for
        #: the chooser that run meets. None: run 2 meets a clean broker.
        self.run2_chooser: Callable[[], Callable[..., Choice]] | None = None
        #: Each chooser `run2_chooser` made, in the order the runs met them.
        self.run2_choosers: list[Callable[..., Choice]] = []
        self._run2_memo: dict[tuple, Run2] = {}
        real_close = mp.close_with_protection
        real_cover = getattr(mp, "cover_beside_exit", None)

        def close(client, ticker, **kw):  # noqa: ANN001, ANN202
            if self.arming:
                client.armed = True
                client.trace(f"     -- close_with_protection({ticker}) starts; broker armed --")
            try:
                out = real_close(client, ticker, **kw)
            except Exception as exc:
                # manage_positions reports a close that died as uncovered (rc 3);
                # the invariants judge it as a close that established nothing.
                client.outcomes.append(("close", pc.CloseOutcome(ticker, "unknown", repr(exc))))
                client.trace(f"     == close raised: {exc!r}")
                raise
            client.outcomes.append(("close", out))
            client.trace(f"     == close outcome: {out.status}: {out.detail}")
            return out

        def cover(client, ticker, **kw):  # noqa: ANN001, ANN202
            assert real_cover is not None
            out = real_cover(client, ticker, **kw)
            client.outcomes.append(("cover", out))
            client.trace(f"     == cover_beside_exit outcome: {out.status}: {out.detail}")
            return out

        monkeypatch.setattr(mp, "AlpacaClient", lambda: self.broker)
        monkeypatch.setattr(mp, "datetime", _ClockDT)
        monkeypatch.setattr(
            mp, "_refresh_held",
            lambda client, today: (fresh_bars(_ClockDT.current, self.variant), []),
        )
        monkeypatch.setattr(mp, "_kill_switch", lambda: "RUN")
        monkeypatch.setattr(mp, "close_with_protection", close)
        monkeypatch.setattr(mp, "cover_beside_exit", cover, raising=False)
        monkeypatch.setattr(storage, "TradeLogRepository", _NoRepo)
        # Every wait the close makes passes on the model's clock, not the wall's.
        # A lambda, not `self.broker.sleep`: the broker changes with every run.
        monkeypatch.setattr(time, "sleep", lambda s: self.broker.sleep(s))  # noqa: PLW0108
        monkeypatch.setattr(time, "monotonic", lambda: 1_000.0 + self.broker.elapsed)
        # A fixed suffix for ids minted with uuid4, so traces replay identically.
        monkeypatch.setattr(
            pc, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="c0ffee" * 6)),
            raising=False,
        )

    # ---- one end-to-end scenario family ----------------------------------------------

    def _main(self, broker: ModelBroker, at: datetime, variant: str, arm: bool) -> int:
        self.broker, self.variant, self.arming = broker, variant, arm
        _ClockDT.current = at
        broker.base_time, broker.elapsed, broker.mark = at, 0.0, MARK[variant]
        previous = logging.root.manager.disable
        if not broker.tracing:
            logging.disable(logging.CRITICAL)
        try:
            return mp.main(list(ARGV))
        finally:
            logging.disable(previous)
            broker.armed = False
            self.arming = False

    def run2(self, b: ModelBroker, variant: str, trace: bool = False) -> Run2:
        """Run 2 on `b`: its exit code, the lot's kind, the book, what it flagged, its closes."""
        # The calendar too: whether an exit met an open is read off it.
        key = (variant, b.key(), self.run1_at, frozenset(b.holidays))
        armed = self.run2_chooser is not None
        if not trace and not armed and key in self._run2_memo:
            return self._run2_memo[key]
        r = b.clone()
        r.tracing, r.stage = trace, f"run 2 ({variant})"
        r.events = []
        if self.run2_chooser is not None:
            r.chooser = self.run2_chooser()
            self.run2_choosers.append(r.chooser)
        rc = self._main(r, RUN2_AT, variant, arm=armed)
        closes = tuple(o for kind, o in r.outcomes[len(b.outcomes):] if kind == "close")
        result = Run2(
            rc, r.kind(QTY), r.describe(), tuple(r.violations[len(b.violations):]), closes,
            r.settled_writes(since=len(b.applied)),
        )
        if trace:
            b.events.extend(r.events)
        elif not armed:
            self._run2_memo[key] = result
        return result

    def evaluate(
        self,
        scenario: Scenario,
        stats: Stats,
        chooser=None,  # noqa: ANN001
        trace: bool = False,
        pick: random.Random | None = None,
    ) -> ModelBroker:
        """Run one fault schedule and check every night, open and run-2 variant it allows.

        The night, the open and the variant are enumerated in full unless the
        scenario pins them, or drawn once each when `pick` is given. Returns the
        run-1 broker (with the trace when asked).
        """
        b = build_book(scenario.book)
        b.tracing = trace
        b.chooser = chooser or _replay(dict(scenario.faults))
        b.market_open = self.market_open
        rc1 = self._main(b, self.run1_at, "due", arm=True)
        b.market_open = False
        b.trace(f"     == run 1 rc={rc1}; {b.describe()}; in flight: "
                f"{[lat.label(b.name) for lat in b.latent] or 'nothing'}")
        stats.schedules += 1
        if chooser is not None:
            # A random or scripted chooser decided as it went: pin what it
            # chose, for replay.
            scenario = dataclasses.replace(scenario, faults=tuple(sorted(chooser.made.items())))
        close = next((o for kind, o in b.outcomes if kind == "close"), None)
        # A fill during run 1 means the run met a trading market: the daily run
        # is after the close, so such a class needs a run in market hours.
        tag = " [run 1 in market hours]" if b.in_run_fill else ""

        def add(cls: str, msg: str, sc: Scenario) -> None:
            stats.add(Finding(cls + tag, msg, sc))

        for cls, msg in b.violations:
            add(cls, msg, scenario)
        if close is None:
            stats.add(Finding("harness: no time exit in run 1", f"rc={rc1}", scenario))
            return b
        after = _After(
            self, scenario, close, rc1, cover_wanted(scenario.book), add, stats, pick,
            b.settled_writes(),
        )
        nights = [scenario.night] if scenario.night is not None else b.night_choices()
        if pick is not None and scenario.night is None:
            nights = [pick.choice(nights)]
        for night in nights:
            n = after.night(b, night)
            if trace:
                b.events = n.events
        return b

    @staticmethod
    def _check_run1(
        close: pc.CloseOutcome, rc1: int, e1: str, n: ModelBroker
    ) -> list[tuple[str, str]]:
        """I1, I3, I4 and I5 once run 1's requests have all landed (or never will)."""
        out: list[tuple[str, str]] = []
        state = f"{n.describe()} (close: {close.status}; rc={rc1})"
        if e1 == "short":
            return out  # flagged as I1 at the fill that made it
        if e1 == "flat+sell":
            out.append(("I1 sell standing on a flat book after run 1", state))
        if e1 == "over-reserved":
            out.append(("I3 two live sells for the same shares after run 1", state))
        if close.ok and e1 not in ("flat", "exiting"):
            out.append((f"I4 close says {close.status} but the lot is {e1}", state))
        if close.status == "unchanged" and e1 not in ("protected", "flat", "exiting"):
            out.append((f"I4 close says unchanged but the lot is {e1}", state))
        if e1 not in ("flat", "exiting", "protected") and rc1 != mp.EXIT_UNCOVERED:
            out.append((f"I5 {e1} after run 1, reported as rc={rc1}", state))
        return out

    @staticmethod
    def _check_precision(
        close: pc.CloseOutcome, rc1: int, e1: str, n: ModelBroker, writes: int | None,
        full: bool,
    ) -> list[tuple[str, str]]:
        """I8: what a schedule of refused or lost writes, every read answered, must end in.

        I4 and I5 accept `naked` with rc 3 anywhere, so on their own they
        cannot tell a close that puts the stop back from one that does not.
        With at most two such writes, every one of them read back, the lot
        ends the night flat, exiting or protected: the re-arm and the same-run
        re-cover between them put back what went. With one, the pass also
        knows it did, and exits 0 or 1, not 3: on a book that was fully
        covered at the start (`full`), since a back-fill of shares that were
        already naked is not the time exit's to vouch for.
        """
        if writes is None or writes > 2 or e1 == "short":
            return []
        state = f"{n.describe()} (close: {close.status}; rc={rc1})"
        out: list[tuple[str, str]] = []
        if e1 not in ("flat", "exiting", "protected"):
            out.append((f"I8 {e1} after run 1 with {writes} refused or lost write(s)", state))
        if writes <= 1 and full and rc1 == mp.EXIT_UNCOVERED:
            out.append((f"I8 rc=3 after run 1 with {writes} refused or lost write(s)", state))
        return out

    @staticmethod
    def _check_run2(r2: Run2, variant: str, o: ModelBroker) -> list[tuple[str, str]]:
        """I1, I2, I3 and I7 after the next daily run, which met the book `o` left.

        I2 holds a run 2 that met a clean broker. One armed with faults
        (`Harness.run2_chooser`) is held to I8 instead: with at most two
        settled write faults, a lot run 2 found protected it leaves flat,
        exiting or protected.
        """
        out: list[tuple[str, str]] = []
        rc2, e2, state = r2.rc, r2.kind, r2.state
        tail = f"{state} (run 2 {variant}, rc={rc2})"
        missed = o.missed_at_open and o.held > EPS
        if missed and rc2 == 0:
            out.append((
                f"I7 an exit the open did not fill went unreported by run 2 ({variant})",
                f"{', '.join(o.missed_at_open)}; {tail}",
            ))
        if missed and r2.closes and not any(c.missed_exits for c in r2.closes):
            out.append((
                f"I7 run 2's close did not name the exit the open did not fill ({variant})",
                f"{', '.join(o.missed_at_open)}; close: "
                f"{'; '.join(f'{c.status}: {c.detail}' for c in r2.closes)}; {tail}",
            ))
        if e2 == "short":
            pass  # flagged as I1 at the fill that made it
        elif e2 == "flat+sell":
            out.append(("I1 sell standing on a flat book after run 2", tail))
        elif e2 == "over-reserved":
            out.append(("I3 two live sells for the same shares after run 2", tail))
        elif e2 not in ("flat", "exiting", "protected"):
            if r2.writes == 0:
                out.append((f"I2 {e2} after run 2 ({variant})", tail))
            elif r2.writes is not None and r2.writes <= 2 and o.kind(QTY) == "protected":
                out.append((
                    f"I8 run 2 ends {e2} with {r2.writes} settled write fault(s) ({variant})",
                    tail,
                ))
        return out


@dataclass
class _After:
    """What follows run 1 for one schedule: the night, the open, run 2, checked as it goes."""

    harness: Harness
    scenario: Scenario
    close: pc.CloseOutcome
    rc1: int
    wanted: float
    add: Callable[[str, str, Scenario], None]
    stats: Stats
    pick: random.Random | None
    #: `ModelBroker.settled_writes` of run 1.
    writes: int | None = None

    def night(self, b: ModelBroker, choice: tuple[str, ...]) -> ModelBroker:
        """Settle what run 1 left in flight one way, check, then every open after it."""
        n = b.clone()
        settled = choice if len(choice) == len(n.latent) else tuple("lands" for _ in n.latent)
        n.night(settled)
        sc = dataclasses.replace(self.scenario, night=settled)
        e1 = n.kind(self.wanted)
        n.trace(f"     == after the night: {e1}; {n.describe()}")
        precision = Harness._check_precision(
            self.close, self.rc1, e1, n, self.writes, self.wanted >= QTY - EPS
        )
        for cls, msg in (*Harness._check_run1(self.close, self.rc1, e1, n), *precision):
            self.add(cls, msg, sc)
        openings = [sc.opening] if sc.opening is not None else n.open_choices()
        if self.pick is not None and sc.opening is None:
            openings = [self.pick.choice(openings)]
        last = n
        for opening in openings:
            last = self.open(n, sc, opening)
        return last

    def open(self, n: ModelBroker, sc: Scenario, choice: tuple[str, str]) -> ModelBroker:
        """One open after that night, then run 2 in each variant."""
        o = n.clone()
        opening = choice if choice in o.open_choices() else o.open_choices()[0]
        o.open_market(opening)
        sc = dataclasses.replace(sc, opening=opening)
        for cls, msg in o.violations[len(n.violations):]:
            self.add(cls, msg, sc)
        o.trace(f"     == after the open ({opening[0]}, {opening[1]}): {o.describe()}")
        for variant in [sc.variant] if sc.variant else RUN2_VARIANTS:
            self.stats.scenarios += 1
            r2 = self.harness.run2(o, variant, trace=o.tracing)
            for cls, msg in (*r2.flagged, *Harness._check_run2(r2, variant, o)):
                self.add(cls, msg, dataclasses.replace(sc, variant=variant))
            o.trace(f"     == run 2 ({variant}) rc={r2.rc}: {r2.kind}; {r2.state}")
        return o


# --------------------------------------------------------------------------- explorers


def _options(env_opts: tuple[str, ...], outs: tuple[str, ...]) -> list[Choice]:
    return [(e, "ok") for e in env_opts] + [(None, o) for o in outs[1:]]


def explore_bounded(
    harness: Harness, book: str, max_faults: int, window: int | None = None
) -> Stats:
    """Every schedule with at most `max_faults` non-clean choices, at any call.

    A later fault comes after an earlier one, within `window` calls of it when
    given. Each schedule is checked under every night, open and run-2 variant.
    """
    stats = Stats()
    start = time.perf_counter()
    base = harness.evaluate(Scenario(book, ()), stats)
    frontier = [((), base.points, -1)]
    for depth in range(max_faults):
        nxt = []
        for faults, points, last in frontier:
            for idx, _kind, env_opts, outs in points:
                if idx <= last:
                    continue
                if window is not None and last >= 0 and idx - last > window:
                    break
                for choice in _options(env_opts, outs):
                    sched = (*faults, (idx, choice))
                    b = harness.evaluate(Scenario(book, sched), stats)
                    if depth + 1 < max_faults:
                        nxt.append((sched, b.points, idx))
        frontier = nxt
    stats.seconds = time.perf_counter() - start
    return stats


class _RandomChooser:
    """Each call: an event with probability p_env, a fault with probability p_fault."""

    def __init__(self, rng: random.Random, p_env: float, p_fault: float) -> None:
        self.rng, self.p_env, self.p_fault = rng, p_env, p_fault
        self.made: dict[int, Choice] = {}

    def __call__(self, idx: int, kind: str, env_opts, outs, what: str = "") -> Choice:  # noqa: ANN001
        env = self.rng.choice(env_opts) if env_opts and self.rng.random() < self.p_env else None
        out = self.rng.choice(outs[1:]) if self.rng.random() < self.p_fault else "ok"
        if env is not None or out != "ok":
            self.made[idx] = (env, out)
        return env, out


class Scripted:
    """Faults picked by what each call is: a schedule too deep to explore, told as a story.

    Each rule is (text the call's trace line contains, outcome[, times]), and
    waits for the rule before it to be spent: so ("list_orders", "timeout", 3)
    after a re-arm's refusal times out the next three listings after it, not
    the first three of the run. Every other call is answered cleanly.
    """

    def __init__(self, *rules: tuple[str, str] | tuple[str, str, int]) -> None:
        self.rules: list[list] = [[r[0], r[1], r[2] if len(r) > 2 else 1] for r in rules]
        self.made: dict[int, Choice] = {}

    def __call__(self, idx: int, kind: str, env_opts, outs, what: str = "") -> Choice:  # noqa: ANN001
        if not self.rules or self.rules[0][0] not in what:
            return CLEAN
        rule = self.rules[0]
        rule[2] -= 1
        if rule[2] <= 0:
            self.rules.pop(0)
        self.made[idx] = (None, rule[1])
        return None, rule[1]


class Streak:
    """One outcome on consecutive calls of one kind: a broker that keeps failing one way.

    From call `start` on, each call of `kind` whose outcomes include `outcome`
    gets it, `length` times; every other call is answered cleanly. The
    explorers above bound the faults per schedule (two anywhere, three within
    ten calls, a fault rate per call), and the release retries a DELETE at
    every read until the broker answers: the end of its confirm window, and
    what it decides there off the DELETEs it noted as possibly landed, takes
    ten failures in a row to reach.
    """

    def __init__(self, start: int, kind: str, outcome: str, length: int) -> None:
        self.start, self.kind, self.outcome, self.left = start, kind, outcome, length
        self.made: dict[int, Choice] = {}

    def __call__(self, idx: int, kind: str, env_opts, outs, what: str = "") -> Choice:  # noqa: ANN001
        if idx < self.start or kind != self.kind or self.left <= 0 or self.outcome not in outs:
            return CLEAN
        self.left -= 1
        self.made[idx] = (None, self.outcome)
        return None, self.outcome


#: How many calls in a row a streak runs: past the confirm window's ten reads,
#: and past the settle window's seven more.
STREAK_LENGTHS = (3, 10, 20)


def explore_streaks(
    harness: Harness, book: str, lengths: Sequence[int] = STREAK_LENGTHS
) -> Stats:
    """Every streak: from each call of the clean run, each fault its kind allows, each length.

    Each schedule is checked under every night, open and run-2 variant.
    """
    stats = Stats()
    start = time.perf_counter()
    base = harness.evaluate(Scenario(book, ()), stats)
    for idx, kind, _env_opts, outs in base.points:
        for outcome in outs[1:]:
            for length in lengths:
                harness.evaluate(
                    Scenario(book, ()), stats, chooser=Streak(idx, kind, outcome, length)
                )
    stats.seconds = time.perf_counter() - start
    return stats


def explore_random(
    harness: Harness, books: Sequence[str], seed: int, n: int,
    p_env: float = 0.06, p_fault: float = 0.12,
) -> Stats:
    """`n` random schedules, each with one random night, open and run-2 variant."""
    stats = Stats()
    rng = random.Random(seed)
    start = time.perf_counter()
    for _ in range(n):
        book = rng.choice(books)
        chooser = _RandomChooser(random.Random(rng.random()), p_env, p_fault)
        # The night and the open are drawn after run 1, off the state it left.
        pick = random.Random(rng.random())
        scenario = Scenario(book, (), variant=pick.choice(RUN2_VARIANTS))
        harness.evaluate(scenario, stats, chooser=chooser, pick=pick)
    stats.seconds = time.perf_counter() - start
    return stats



# --------------------------------------------------------------------------- reporting


def reproduces(harness: Harness, scenario: Scenario, cls: str) -> bool:
    stats = Stats()
    harness.evaluate(scenario, stats)
    return cls in stats.findings


def minimize(harness: Harness, finding: Finding) -> Scenario:
    """Drop faults one at a time while the same violation class still shows (1-minimal)."""
    sc = finding.scenario
    changed = True
    while changed:
        changed = False
        for i in range(len(sc.faults)):
            trial = dataclasses.replace(sc, faults=sc.faults[:i] + sc.faults[i + 1:])
            if reproduces(harness, trial, finding.cls):
                sc, changed = trial, True
                break
        defaults = {
            "night": (), "opening": ("fill", "hold"), "variant": "moved",
        }
        for attr, default in defaults.items():
            if getattr(sc, attr) != default:
                trial = dataclasses.replace(sc, **{attr: default})
                if reproduces(harness, trial, finding.cls):
                    sc, changed = trial, True
    return sc


def render(harness: Harness, finding: Finding, scenario: Scenario) -> str:
    """The minimal counterexample as a step-by-step trace."""
    stats = Stats()
    b = harness.evaluate(scenario, stats, trace=True)
    faults = ", ".join(
        f"#{i} {env + ' then ' if env else ''}{out}" for i, (env, out) in scenario.faults
    ) or "none (a clean broker)"
    lines = [
        f"VIOLATION {finding.cls}",
        f"  {finding.message}",
        f"  book={scenario.book}  faults: {faults}",
        f"  night={scenario.night}  open={scenario.opening}  run 2={scenario.variant}",
        *(f"  {e}" for e in b.events),
    ]
    return "\n".join(lines)


def report(harness: Harness, stats: Stats, label: str = "") -> str:
    """The exploration's size, and one minimal counterexample per violation class."""
    head = (
        f"{label}{stats.schedules} fault schedules, {stats.scenarios} end-to-end scenarios "
        f"(night x open x run-2 variant), {stats.seconds:.1f}s; "
        f"{stats.violations} violations in {len(stats.findings)} classes"
    )
    lines = [head, *(f"  {len(f):6d}  {cls}" for cls, f in sorted(stats.findings.items()))]
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        for _cls, found in sorted(stats.findings.items()):
            first = min(found, key=lambda f: (len(f.scenario.faults), f.scenario.faults))
            lines += ["", "=" * 100, render(harness, first, minimize(harness, first))]
    finally:
        logging.disable(previous)
    return "\n".join(lines)


def main() -> None:
    """`python -m tests.time_exit_model [K] [window] [random-per-seed]`: explore and report."""
    import sys

    import pytest

    k = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    window = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] != "none" else None
    per_seed = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    with pytest.MonkeyPatch.context() as monkeypatch:
        harness = Harness(monkeypatch)
        stats = Stats()
        for book in BOOKS:
            stats.merge(explore_bounded(harness, book, k, window))
        for seed in range(per_seed and 4):
            stats.merge(explore_random(harness, BOOKS, seed, per_seed))
        print(report(harness, stats))


if __name__ == "__main__":
    main()
