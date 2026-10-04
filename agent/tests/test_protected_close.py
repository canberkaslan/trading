"""close_with_protection, one ambiguous broker state at a time.

The property under test is the one stop_coverage.py and position_manager.py
both name: two sells on one long lot become a SHORT after a gap down. Every
test below breaks one step of guard -> release -> sell -> verify -> re-arm ->
verdict against a broker fake that reserves shares the way Alpaca does, and
checks that the lot ends in one of three states:

  * still protected: held, and under exactly as much stop as before;
  * on its way out: held, with the stamped market exit queued for the whole
    lot and no other sell standing beside it; or
  * fully closed: nothing held, and nothing standing that could sell a share.

Where the broker's state cannot be established or must not exist, the outcome
says `naked` or `unknown`, both of which page, and never reads as a close.

The close acts only while the market is shut, so the fake here queues the exit
(`exit_status="accepted"`) as Alpaca does after the close, and no stop fires.
A few tests keep the fake's fill-on-submit to show what the verdict says if
that precondition were ever broken.
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from tests.broker_fake import FakeBroker
from tradingagents_us.dataflows.alpaca_broker import (
    AlpacaClient,
    AlpacaRequestError,
    Clock,
    Order,
    Position,
)
from tradingagents_us.execution.executor import derive_exit_client_order_id
from tradingagents_us.execution.protected_close import (
    CANCEL_CONFIRM_INTERVAL_S,
    CANCEL_CONFIRM_POLLS,
    CANCEL_SETTLE_DELAYS_S,
    MAX_CLIENT_ID_LEN,
    MIN_TIME_TO_OPEN,
    _fit,
    _rearm_id,
    close_with_protection,
    cover_beside_exit,
    exit_stamp_date,
)

DAY = date(2026, 9, 28)
STAMP = derive_exit_client_order_id("XOM", DAY, "time")
YESTERDAY_STAMP = derive_exit_client_order_id("XOM", DAY - timedelta(days=1), "time")

#: The short confirm window every cancel gets, then the settle window after it.
CONFIRM_SLEEPS = [CANCEL_CONFIRM_INTERVAL_S] * (CANCEL_CONFIRM_POLLS - 1)
FULL_CANCEL_WAIT = CONFIRM_SLEEPS + list(CANCEL_SETTLE_DELAYS_S)

#: How long AlpacaClient waits for a reply before it raises a timeout.
CLIENT_TIMEOUT_S = inspect.signature(AlpacaClient.__init__).parameters["timeout_s"].default


def _position(qty: float = 10.0, side: str = "long") -> Position:
    return Position(
        symbol="XOM",
        qty=qty,
        side=side,
        avg_entry_price=100.0,
        market_value=qty * 100.5,
        unrealized_pl=5.0,
        unrealized_plpc=0.005,
    )


def _order(
    oid: str,
    order_type: str,
    *,
    qty: float = 10.0,
    status: str = "new",
    stop_price: float | None = None,
    coid: str | None = None,
    filled_qty: float = 0.0,
    submitted_at: datetime | None = None,
) -> Order:
    return Order(
        id=oid,
        client_order_id=coid or f"coid-{oid}",
        symbol="XOM",
        side="sell",
        qty=qty,
        filled_qty=filled_qty,
        order_type=order_type,  # type: ignore[arg-type]
        status=status,
        submitted_at=submitted_at or datetime.now(UTC),
        filled_avg_price=None,
        stop_price=stop_price,
    )


def _stop(oid: str = "stop-xom", qty: float = 10.0, price: float = 90.0, **kw) -> Order:
    return _order(oid, "stop", qty=qty, stop_price=price, **kw)


def _bracket() -> list[Order]:
    """A bracket's two legs: the take-profit works, the stop rests in `held`.

    Listed stop first, so the take-profit-first release is the code's doing and
    not the fixture's.
    """
    return [_stop(status="held"), _order("tp-xom", "limit")]


def _shut(orders: list[Order] | None = None, qty: float = 10.0, **kw) -> FakeBroker:
    """A broker after the close: a market exit queues for the open, nothing fills."""
    kw.setdefault("exit_status", "accepted")
    return FakeBroker([_position(qty=qty)], orders or [], **kw)


def _two_brackets(**kw) -> FakeBroker:
    """20 shares bought as two brackets of 10: two take-profits, two held stops.

    Each take-profit is an OCO pair with its own stop, so cancelling one takes
    that stop with it, and the pair reserves its ten shares once.
    """
    return _shut(
        [
            _stop("sl-a", status="held", price=90.0),
            _stop("sl-b", status="held", price=88.0),
            _order("tp-a", "limit"),
            _order("tp-b", "limit"),
        ],
        qty=20.0,
        oco={"tp-a": "sl-a", "tp-b": "sl-b"},
        **kw,
    )


def _stop_cover(fake: FakeBroker) -> float:
    return sum(o.qty - o.filled_qty for o in fake.live_sells("XOM") if o.order_type == "stop")


def _close(fake: FakeBroker, sleeps: list[float] | None = None):
    record = sleeps if sleeps is not None else []
    return close_with_protection(fake, "XOM", trade_date=DAY, sleep=record.append)


def _sells(fake: FakeBroker) -> list[dict]:
    return [c[1] for c in fake.writes if c[0] == "submit_order" and c[1]["order_type"] == "market"]


def _rearms(fake: FakeBroker) -> list[tuple[float, float]]:
    return [
        (c[1]["qty"], c[1]["stop_price"])
        for c in fake.writes
        if c[0] == "submit_order" and c[1]["order_type"] == "stop"
    ]


def _cancels(fake: FakeBroker) -> list[str]:
    return [c[1] for c in fake.writes if c[0] == "cancel_order"]


def _assert_protected_once(fake: FakeBroker, qty: float = 10.0) -> None:
    """Held, and every held share under exactly one working stop, nothing else."""
    assert fake.positions["XOM"].qty == qty
    assert _stop_cover(fake) == qty
    assert [o for o in fake.live_sells("XOM") if o.order_type != "stop"] == []


def _assert_exiting_once(fake: FakeBroker, qty: float = 10.0) -> None:
    """Held, with the stamped exit queued for the whole lot and nothing beside it."""
    assert fake.positions["XOM"].qty == qty
    (working,) = fake.live_sells("XOM")
    assert working.order_type == "market" and working.client_order_id.startswith(STAMP)
    assert working.qty - working.filled_qty == qty


def _assert_closed_clean(fake: FakeBroker) -> None:
    assert "XOM" not in fake.positions
    assert fake.live_sells("XOM") == [], "a sell left standing on a closed lot opens a short"


def _assert_one_seller(fake: FakeBroker, qty: float = 10.0) -> None:
    """The three acceptable ends: protected once, exiting once, or closed clean."""
    if "XOM" not in fake.positions:
        _assert_closed_clean(fake)
    elif any(o.order_type == "market" for o in fake.live_sells("XOM")):
        _assert_exiting_once(fake, qty)
    else:
        _assert_protected_once(fake, qty)


class _ClockDown(FakeBroker):
    def clock(self) -> Clock:
        self._record("clock")
        raise httpx.ConnectError("clock unreachable")


class TestTheGuard:
    """GUARD: the close acts only while nothing but its own requests can move the lot."""

    def test_an_open_market_defers_the_close_and_sends_nothing(self) -> None:
        fake = _shut([_stop()], market_open=True)

        outcome = _close(fake)

        assert outcome.status == "deferred" and not outcome.ok
        assert "market is open" in outcome.detail
        assert fake.calls == [("clock",)]
        _assert_protected_once(fake)

    def test_the_last_minutes_before_the_open_defer_it_too(self) -> None:
        minutes = MIN_TIME_TO_OPEN.total_seconds() / 60 - 1
        fake = _shut([_stop()], minutes_to_open=minutes)

        outcome = _close(fake)

        assert outcome.status == "deferred"
        assert fake.writes == []

    def test_an_unreadable_clock_sends_nothing(self) -> None:
        fake = _ClockDown([_position()], [_stop()])

        outcome = _close(fake)

        assert outcome.status == "unchanged" and "clock unreachable" in outcome.detail
        assert fake.writes == []
        _assert_protected_once(fake)


class TestTheHappyPath:
    def test_release_confirm_read_sell_then_the_verdict_reads_the_book_again(self) -> None:
        fake = _shut([_stop()])

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok
        assert outcome.client_order_id == STAMP and outcome.exit_order_id
        steps = [c[:2] if c[0] != "submit_order" else ("submit_order",) for c in fake.calls]
        assert steps == [
            ("clock",),
            ("get_order_by_client_order_id", STAMP),
            ("list_orders", "all"),
            ("cancel_order", "stop-xom"),
            ("get_order", "stop-xom"),
            ("get_position", "XOM"),
            ("submit_order",),
            # The verdict: orders first, holding last.
            ("list_orders", "all"),
            ("get_position", "XOM"),
        ]
        assert _sells(fake) == [
            {"symbol": "XOM", "qty": 10.0, "side": "sell", "order_type": "market",
             "time_in_force": "day", "client_order_id": STAMP}
        ]
        _assert_exiting_once(fake)

    def test_a_bracket_releases_its_take_profit_before_its_stop(self) -> None:
        fake = _shut(_bracket())

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert _cancels(fake) == ["tp-xom", "stop-xom"]
        _assert_exiting_once(fake)

    def test_an_unprotected_position_is_sold_with_nothing_to_release(self) -> None:
        fake = _shut()

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [c[0] for c in fake.writes] == ["submit_order"]
        _assert_exiting_once(fake)

    def test_an_exit_that_fills_at_once_is_a_close(self) -> None:
        fake = _shut([_stop()], exit_status="filled")

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok
        _assert_closed_clean(fake)


class TestBeforeAnythingIsReleased:
    """STAMP and BLOCKED: what is already on the book decides before any write."""

    def test_an_exit_already_working_under_todays_stamp_sends_nothing(self) -> None:
        fake = _shut([_order("exit-1", "market", status="accepted", coid=STAMP)])

        outcome = _close(fake)

        assert outcome.status == "already_exiting" and outcome.ok
        assert fake.writes == []

    def test_todays_exit_in_a_status_that_is_neither_pages(self) -> None:
        fake = _shut([_order("exit-1", "market", status="pending_cancel", coid=STAMP)])

        outcome = _close(fake)

        assert outcome.status == "unknown" and "pending_cancel" in outcome.detail
        assert fake.writes == []

    @pytest.mark.parametrize("coid", [YESTERDAY_STAMP, f"{YESTERDAY_STAMP}-r2"])
    @pytest.mark.parametrize("status", ["accepted", "new", "pending_new"])
    def test_an_exit_an_earlier_day_queued_and_still_working_sends_nothing(
        self, coid: str, status: str
    ) -> None:
        # A weekday exchange holiday (or a rerun past 00:00 UTC): no session
        # has come since yesterday's close queued it, and that close released
        # the stop. The lot is on its way out at the next open. It used to be
        # cancelled as a take-profit and sent again, and a cancel that stuck
        # left the lot with neither exit nor stop, reported as `unchanged`.
        prior = _order("exit-y", "market", status=status, coid=coid,
                       submitted_at=datetime.now(UTC) - timedelta(days=1))
        fake = _shut([prior])

        outcome = _close(fake)

        assert outcome.status == "already_exiting" and outcome.ok
        assert outcome.exit_order_id == "exit-y" and outcome.client_order_id == coid
        assert fake.writes == []

    def test_an_earlier_exit_whose_cancel_is_on_its_way_pages(self) -> None:
        # Not cancelled by any close (none releases an exit), yet in
        # pending_cancel: once it lands the lot has neither exit nor stop.
        prior = _order("exit-y", "market", status="pending_cancel", coid=YESTERDAY_STAMP,
                       submitted_at=datetime.now(UTC) - timedelta(days=1))
        fake = _shut([prior])

        outcome = _close(fake)

        assert outcome.status == "naked", outcome.detail
        assert "exit-y=pending_cancel" in outcome.detail
        assert fake.writes == []

    def test_a_market_sell_no_bracket_can_have_is_left_standing(self) -> None:
        # Another writer's sell, queued for the open beside a stop on the rest
        # of the lot. No bracket has a market leg: it is not a take-profit, and
        # cancelling it is not the release's to do. Nothing is sent, and the
        # stop still covers what it covered.
        council = _order("sell-1", "market", qty=4.0, status="accepted",
                         coid="tr-XOM-20260928-SELL")
        fake = _shut([_stop(qty=6.0), council])

        outcome = _close(fake)

        assert outcome.status == "unchanged", outcome.detail
        assert "sell-1=market accepted" in outcome.detail
        assert fake.writes == []

    @pytest.mark.parametrize("status", ["stopped", "pending_cancel", "pending_replace"])
    def test_a_stop_in_doubt_blocks_the_close_and_pages(self, status: str) -> None:
        # `stopped`: its fill is on its way; `pending_cancel`: a cancel from an
        # earlier pass is on its way; `pending_replace`: a ratchet is. Acting
        # beside any of them is a guess. Nothing is sent, and since the stop
        # may stop covering the lot any moment, the outcome pages.
        fake = _shut([_stop(status=status)])

        outcome = _close(fake)

        assert outcome.status == "naked" and not outcome.ok
        assert f"stop-xom={status}" in outcome.detail
        assert fake.writes == []

    def test_a_take_profit_in_doubt_counts_its_own_stop_as_on_its_way_out(self) -> None:
        # The take-profit's cancel is on its way, and it takes its stop along.
        fake = _shut(
            [_stop(status="held"), _order("tp-xom", "limit", status="pending_cancel")],
            oco={"tp-xom": "stop-xom"},
        )

        outcome = _close(fake)

        assert outcome.status == "naked", outcome.detail
        assert "stop-xom=held" in outcome.detail
        assert fake.writes == []

    def test_a_stop_whose_take_profit_already_filled_is_left_alone(self) -> None:
        # Its pair ended, so its cancel is on its way at the broker. The other
        # stop M covers the lot on its own: nothing to page about.
        filled_tp = _order("tp1", "limit", status="filled", filled_qty=10.0)
        fake = _shut(
            [_stop("M", price=85.0), filled_tp, _stop("sl1", status="held")],
            oco={"tp1": "sl1"},
        )

        outcome = _close(fake)

        assert outcome.status == "unchanged", outcome.detail
        assert "sl1" in outcome.detail
        assert fake.writes == []

    def test_an_unreadable_book_sends_nothing(self) -> None:
        fake = _shut([_stop()], lookup_fails=True)

        outcome = _close(fake)

        assert outcome.status == "unchanged"
        assert fake.writes == []
        _assert_protected_once(fake)

    def test_no_unused_exit_id_left_today_sends_nothing(self) -> None:
        dead = [
            _order(f"x{n}", "market", status="rejected", coid=STAMP if n == 1 else f"{STAMP}-r{n}")
            for n in range(1, 6)
        ]
        fake = _shut([_stop(), *dead])

        outcome = _close(fake)

        assert outcome.status == "unchanged" and "no unused exit id" in outcome.detail
        assert fake.writes == []


class TestTheRelease:
    """RELEASE: a cancel pending, refused, lost, or taking its pair along."""

    def test_a_refused_take_profit_cancel_leaves_the_stop_untouched(self) -> None:
        fake = _shut(_bracket(), cancel_refused={"tp-xom"})

        outcome = _close(fake)

        assert outcome.status == "unchanged" and not outcome.ok
        assert set(_cancels(fake)) == {"tp-xom"}
        assert {o.id for o in fake.live_sells("XOM")} == {"tp-xom", "stop-xom"}

    def test_a_stop_that_refuses_every_cancel_is_still_the_protection(self) -> None:
        # Refused, and still working after the window every cancel gets: only
        # then is the refusal taken at its word. The DELETE went again at each
        # read, as one throttled by a 429 would need.
        fake = _shut([_stop()], cancel_refused={"stop-xom"})
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "unchanged"
        assert _cancels(fake) == ["stop-xom"] * (CANCEL_CONFIRM_POLLS + 1)
        assert sleeps == CONFIRM_SLEEPS
        _assert_protected_once(fake)

    def test_a_throttled_cancel_is_sent_again_until_it_lands(self) -> None:
        fake = _shut([_stop()], throttle_cancels={"stop-xom": 3})

        outcome = _close(fake)

        assert outcome.status == "exit_submitted", outcome.detail
        assert _cancels(fake) == ["stop-xom"] * 4
        _assert_exiting_once(fake)

    def test_a_cancel_left_pending_is_waited_out_then_paged_with_its_shares(self) -> None:
        # pending_cancel can still be anything, so nothing is sold or re-armed
        # beside it. The cancel cannot be taken back either: when it lands the
        # lot has no stop. So it is waited for, and then the outcome pages.
        fake = _shut([_stop()], cancel_stuck={"stop-xom"})
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "naked" and not outcome.ok
        assert "10 shares that had a stop have no working one" in outcome.detail
        assert "stop-xom=pending_cancel" in outcome.detail
        assert fake.writes == [("cancel_order", "stop-xom")]
        assert sleeps == FULL_CANCEL_WAIT

    def test_a_cancel_accepted_but_slow_to_land_is_waited_for_and_the_lot_sold(self) -> None:
        fake = _shut([_stop()], slow_cancel={"stop-xom": ("new", 15)})

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [s["qty"] for s in _sells(fake)] == [10.0]
        _assert_exiting_once(fake)

    def test_a_cancel_accepted_and_never_seen_to_land_is_not_called_protected(self) -> None:
        fake = _shut([_stop()], slow_cancel={"stop-xom": ("new", 10**6)})

        outcome = _close(fake)

        assert outcome.status == "naked" and "stop-xom=new" in outcome.detail
        # Nothing sold or stacked beside a stop that still reserves the shares.
        assert _sells(fake) == [] and _rearms(fake) == []

    def test_a_cancel_whose_reply_was_lost_is_read_not_taken_as_refused(self) -> None:
        fake = _shut(
            [_stop()],
            cancel_reply_lost={"stop-xom"},
            slow_cancel={"stop-xom": ("pending_cancel", 3)},
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        _assert_exiting_once(fake)

    def test_a_bracket_stop_already_cancelling_with_its_take_profit_is_polled(self) -> None:
        fake = _shut(
            _bracket(), oco={"tp-xom": "stop-xom"},
            slow_cancel={"stop-xom": ("pending_cancel", 3)},
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert _cancels(fake) == ["tp-xom", "stop-xom"]
        _assert_exiting_once(fake)

    @pytest.mark.parametrize("nests_oco", [True, False], ids=["nested", "flat-listing"])
    def test_a_bracket_stop_refusing_its_own_cancel_after_its_take_profit_is_waited_out(
        self, nests_oco: bool
    ) -> None:
        # Its take-profit's cancel takes it along, but it reads `held` past the
        # confirm window and refuses its own DELETE. That refusal is not the
        # protection answering: it is waited out with the cascade.
        fake = _shut(
            _bracket(), oco={"tp-xom": "stop-xom"},
            slow_cancel={"stop-xom": ("held", 12)}, cancel_refused={"stop-xom"},
            nests_oco=nests_oco,
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted", outcome.detail
        _assert_exiting_once(fake)

    @pytest.mark.parametrize("nests_oco", [True, False], ids=["nested", "flat-listing"])
    def test_a_bracket_stop_whose_cascade_never_shows_is_not_called_protected(
        self, nests_oco: bool
    ) -> None:
        fake = _shut(
            _bracket(), oco={"tp-xom": "stop-xom"},
            slow_cancel={"stop-xom": ("held", 10**6)}, cancel_refused={"stop-xom"},
            nests_oco=nests_oco,
        )
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "naked", outcome.detail
        assert "10 shares that had a stop have no working one" in outcome.detail
        assert _sells(fake) == [] and _rearms(fake) == []
        assert sleeps[: len(FULL_CANCEL_WAIT)] == FULL_CANCEL_WAIT

    def test_a_refusal_after_another_pairs_take_profit_is_still_taken_at_its_word(
        self,
    ) -> None:
        # A stop on shares of its own, beside a bracket whose legs went first.
        # The listing pairs that take-profit with its own stop, so it cannot
        # be this one's partner: the refusal stands, and the bracket's stop
        # goes back.
        fake = _shut(
            [_stop("ms", qty=5.0, price=91.0), *_bracket()], qty=15.0,
            oco={"tp-xom": "stop-xom"}, cancel_refused={"ms"},
        )
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "unchanged", outcome.detail
        assert sleeps == CONFIRM_SLEEPS, "the confirm window only, no settle window"
        assert _rearms(fake) == [(10.0, 90.0)]
        _assert_protected_once(fake, qty=15.0)

    def test_a_second_stop_that_lands_late_is_waited_for_and_the_lot_sold_once(self) -> None:
        fake = _shut(
            [_stop("stop-a", qty=6.0, price=90.0), _stop("stop-b", qty=4.0, price=88.0)],
            slow_cancel={"stop-b": ("pending_cancel", 12)},
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert _rearms(fake) == []
        assert [s["qty"] for s in _sells(fake)] == [10.0]
        _assert_exiting_once(fake)

    def test_a_second_stop_that_never_lands_is_reported_with_its_shares(self) -> None:
        fake = _shut(
            [_stop("stop-a", qty=6.0, price=90.0), _stop("stop-b", qty=4.0, price=88.0)],
            cancel_stuck={"stop-b"},
        )

        outcome = _close(fake)

        assert outcome.status == "naked"
        assert _sells(fake) == []
        # The first stop is back. The second still reserves its four shares.
        assert _rearms(fake) == [(6.0, 90.0)]
        assert sum(o.qty for o in fake.live_sells("XOM")) == 10.0
        assert "4 shares that had a stop have no working one" in outcome.detail
        assert "stop-b=pending_cancel" in outcome.detail

    def test_a_refusal_on_a_second_bracket_puts_back_the_first_brackets_stop(self) -> None:
        # Cancelling TP-A takes SL-A with it. TP-B then refuses its cancel and
        # still works, so SL-B with it is still the protection of its ten.
        fake = _two_brackets(cancel_refused={"tp-b"})

        outcome = _close(fake)

        assert outcome.status == "unchanged", outcome.detail
        assert _sells(fake) == []
        assert _rearms(fake) == [(10.0, 90.0)]
        assert _stop_cover(fake) == 20.0

    def test_each_take_profit_is_followed_by_its_own_stop(self) -> None:
        fake = _two_brackets()

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert _cancels(fake) == ["tp-a", "sl-a", "tp-b", "sl-b"]
        _assert_exiting_once(fake, qty=20.0)

    def test_a_second_brackets_cancel_still_on_its_way_is_reported_not_called_covered(
        self,
    ) -> None:
        # TP-B's cancel was accepted and never seen to land, while SL-B went
        # with it at once. SL-A is put back. SL-B's ten cannot be while TP-B
        # still reserves them: the broker refuses that stop, and says so.
        fake = _two_brackets(slow_cancel={"tp-b": ("new", 10**6)})

        outcome = _close(fake)

        assert outcome.status == "naked", outcome.detail
        assert [(o.qty, o.stop_price) for o in fake.live_sells("XOM")
                if o.order_type == "stop"] == [(10.0, 90.0)]
        assert "10 shares that had a stop have no working one" in outcome.detail
        assert "tp-b=new" in outcome.detail and "held_for_orders" in outcome.detail
        assert fake._reserved("XOM") == 20.0

    def test_a_pairing_the_listing_does_not_show_errs_toward_a_page(self) -> None:
        # Flat listing: which stop goes with which take-profit is invisible, so
        # SL-B may be the stop a released take-profit takes along. It is not
        # counted as cover, and the outcome pages; SL-A goes back all the same,
        # and the broker's reservation keeps it off shares SL-B still holds.
        fake = _two_brackets(cancel_refused={"tp-b"}, nests_oco=False)

        outcome = _close(fake)

        assert outcome.status == "naked", outcome.detail
        assert _rearms(fake) == [(10.0, 90.0)]
        assert _stop_cover(fake) == 20.0
        assert fake._reserved("XOM") <= 20.0

    def test_a_stop_a_released_take_profit_took_along_unseen_is_not_counted(self) -> None:
        # The same flat listing, with TP-A's cascade still on its way: SL-A
        # reads `held` a while after its take-profit went.
        fake = _two_brackets(
            cancel_refused={"tp-b"}, nests_oco=False, slow_cancel={"sl-a": ("held", 30)}
        )

        outcome = _close(fake)

        assert outcome.status == "naked", outcome.detail
        assert "sl-a=held" in outcome.detail
        assert _sells(fake) == []

    def test_a_stop_that_fired_during_the_release_leaves_nothing_to_sell(self) -> None:
        # Only in regular hours, which the guard keeps the close out of; if it
        # happens all the same, the lot is gone and nothing is left behind.
        fake = _shut([_stop()], fill_on_cancel={"stop-xom"})

        outcome = _close(fake)

        assert outcome.status == "already_closed" and outcome.ok
        assert _sells(fake) == [] and _rearms(fake) == []
        _assert_closed_clean(fake)

    def test_the_sell_is_sized_off_the_holding_read_after_the_release(self) -> None:
        fake = _shut(
            [_stop("stop-a", qty=6.0), _stop("stop-b", qty=4.0)], fill_on_cancel={"stop-a"}
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [s["qty"] for s in _sells(fake)] == [4.0]
        _assert_exiting_once(fake, qty=4.0)


class _ReArmRefused(FakeBroker):
    """Every re-armed stop is turned down with a 4xx (its level is at the market now)."""

    def submit_order(self, **kw) -> Order:
        if kw.get("order_type") == "stop" and "-arm-" in (kw.get("client_order_id") or ""):
            self._record("submit_order", kw)
            raise AlpacaRequestError("POST", "/orders", 422, "stop price must be below market")
        return super().submit_order(**kw)


class _HoldingUnreadableOnce(FakeBroker):
    """The sizing read of the holding fails once; every later read answers."""

    failed = False

    def get_position(self, symbol: str) -> Position | None:
        if not self.failed and any(c[0] == "cancel_order" for c in self.calls):
            self.failed = True
            self._record("get_position", symbol)
            raise httpx.ConnectError("positions endpoint unreachable")
        return super().get_position(symbol)


class TestTheSell:
    """SELL, VERIFY and RE-ARM: a sell refused, rejected, or whose reply was lost."""

    def test_a_refused_sell_re_arms_the_stop_at_its_level_under_its_own_id(self) -> None:
        fake = _shut([_stop()], refuse_sell={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "unchanged" and not outcome.ok
        assert _rearms(fake) == [(10.0, 90.0)]
        (rearm,) = [c[1] for c in fake.writes
                    if c[0] == "submit_order" and c[1]["order_type"] == "stop"]
        assert rearm["client_order_id"] == _rearm_id(STAMP, "stop-xom")
        assert outcome.rearmed and outcome.released == ("stop-xom",)
        _assert_protected_once(fake)

    def test_a_sell_rejected_in_its_reply_re_arms_the_stop(self) -> None:
        fake = _shut([_stop()], reject_sell={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "unchanged"
        assert "rejected" in outcome.detail
        _assert_protected_once(fake)

    def test_a_retry_after_a_rejected_exit_uses_a_fresh_id(self) -> None:
        # Alpaca refuses a reused client_order_id even for a dead order.
        fake = _shut([_stop()], reject_sell={"XOM"})
        _close(fake)
        fake.reject_sell.clear()

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert outcome.client_order_id == f"{STAMP}-r2"
        _assert_exiting_once(fake)

    def test_a_lost_reply_is_settled_by_the_stamp_not_by_re_arming(self) -> None:
        fake = _shut([_stop()], lose_sell_reply={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert _rearms(fake) == []
        _assert_exiting_once(fake)

    def test_an_exit_whose_stamp_cannot_be_looked_up_is_found_in_the_listing(self) -> None:
        fake = _shut([_stop()], lose_sell_reply={"XOM"}, lookup_fails_after_sell=True)

        outcome = _close(fake)

        assert outcome.status == "exit_submitted", outcome.detail
        assert _rearms(fake) == []
        _assert_exiting_once(fake)

    def test_a_refused_exit_is_re_armed_even_when_its_stamp_cannot_be_looked_up(self) -> None:
        # A 4xx is the broker's answer: no order exists under the stamp.
        fake = _shut([_stop()], refuse_sell={"XOM"}, lookup_fails_after_sell=True)

        outcome = _close(fake)

        assert outcome.status == "unchanged"
        _assert_protected_once(fake)

    def test_an_exit_landing_during_its_lookups_is_found_not_re_armed_over(self) -> None:
        fake = _shut([_stop()], sell_in_flight={"XOM": 2})

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.client_order_id == STAMP
        assert _rearms(fake) == []
        _assert_exiting_once(fake)

    def test_an_exit_never_found_is_re_armed_and_can_no_longer_land(self) -> None:
        # The POST timed out and the lookups never found it. The stop goes back
        # off a fresh read, and once it stands it reserves the shares: should
        # the exit land after all, the broker turns it down.
        fake = _shut([_stop()], sell_in_flight={"XOM": 10**6})
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "unchanged", outcome.detail
        assert outcome.client_order_id is None, "nothing left for a back-fill to look for"
        assert sum(sleeps) > CLIENT_TIMEOUT_S
        _assert_protected_once(fake)
        fake.land_in_flight()
        _assert_protected_once(fake)

    @pytest.mark.parametrize("lands_after", range(1, 16))
    def test_an_exit_landing_at_any_point_leaves_exactly_one_seller(
        self, lands_after: int
    ) -> None:
        # Lands during the lookups, on the fresh read, on the re-arm's own POST
        # or after it. Whichever of the two the broker takes first reserves the
        # shares and the other is refused, and the verdict reads which.
        fake = _shut([_stop()], sell_in_flight={"XOM": lands_after})

        outcome = _close(fake)

        _assert_one_seller(fake)
        exiting = any(o.order_type == "market" for o in fake.live_sells("XOM"))
        assert outcome.status == ("exit_submitted" if exiting else "unchanged"), outcome.detail
        fake.land_in_flight()
        _assert_one_seller(fake)

    @pytest.mark.parametrize(
        "error",
        [httpx.ReadTimeout("reply lost"), AlpacaRequestError("POST", "/orders", 504, "gateway")],
        ids=["timeout", "504"],
    )
    @pytest.mark.parametrize("lands_after", [7, 8, 9, 10, 10**6])
    def test_a_re_arm_whose_reply_is_lost_too_still_leaves_one_seller(
        self, lands_after: int, error: Exception
    ) -> None:
        fake = _shut([_stop()], sell_in_flight={"XOM": lands_after}, stop_reply_error=error)

        outcome = _close(fake)
        fake.land_in_flight()

        _assert_one_seller(fake)
        if "XOM" in fake.positions and not any(
            o.order_type == "market" for o in fake.live_sells("XOM")
        ):
            assert outcome.status == "unchanged", "the stop is there; only its reply was lost"

    @pytest.mark.parametrize("stop_lands_after", [1, 3, 9])
    @pytest.mark.parametrize("exit_lands_after", [7, 9, 11, 10**6])
    def test_an_exit_and_a_re_arm_both_landing_late_leave_one_seller(
        self, exit_lands_after: int, stop_lands_after: int
    ) -> None:
        fake = _shut(
            [_stop()],
            sell_in_flight={"XOM": exit_lands_after},
            stop_in_flight={"XOM": stop_lands_after},
        )

        outcome = _close(fake)
        fake.land_in_flight()

        _assert_one_seller(fake)
        if outcome.ok:
            _assert_exiting_once(fake)

    def test_a_naked_lot_whose_exit_was_never_found_hands_its_stamp_on(self) -> None:
        # No stop to put back, so nothing reserves the shares and the exit can
        # still land: the stamp tells the caller to back-fill beside it.
        fake = _shut(sell_in_flight={"XOM": 10**6})

        outcome = _close(fake)

        assert outcome.status == "unknown" and "may still land" in outcome.detail
        assert outcome.client_order_id == STAMP and outcome.rearmed == ()

    def test_a_re_arm_the_broker_refuses_is_naked_and_hands_the_stamp_on(self) -> None:
        fake = _ReArmRefused([_position()], [_stop()], exit_status="accepted",
                             sell_in_flight={"XOM": 10**6})

        outcome = _close(fake)

        assert outcome.status == "naked" and "stop price must be below market" in outcome.detail
        assert outcome.client_order_id == STAMP

    def test_a_failed_re_arm_after_a_refused_sell_is_naked(self) -> None:
        fake = _shut([_stop()], refuse_sell={"XOM"}, refuse_stop={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "naked" and not outcome.ok
        assert "stop refused" in outcome.detail

    def test_a_holding_unreadable_once_re_arms_off_the_next_read(self) -> None:
        fake = _HoldingUnreadableOnce([_position()], [_stop()], exit_status="accepted")

        outcome = _close(fake)

        assert outcome.status == "unchanged", outcome.detail
        assert _sells(fake) == []
        _assert_protected_once(fake)

    def test_a_holding_never_readable_puts_the_stop_back_unchecked_and_pages(self) -> None:
        # No exit was sent and nothing fills while the market is shut, so the
        # released stop was the lot's only seller and goes back as it was. The
        # end state cannot be read, so the outcome does not claim it.
        fake = _shut([_stop()], position_unreadable={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "unknown" and "could not be read" in outcome.detail
        assert _sells(fake) == [] and _rearms(fake) == [(10.0, 90.0)]
        assert _stop_cover(fake) == 10.0

    def test_a_short_is_never_sold(self) -> None:
        fake = FakeBroker([_position(side="short")])

        outcome = _close(fake)

        assert outcome.status == "unknown"
        assert fake.writes == []


class TestTheVerdict:
    """VERDICT: the post-condition is read from the broker, whatever the path."""

    def test_a_partly_filled_stop_releases_and_sells_only_what_is_held(self) -> None:
        # A stop that sold 4 of its 10 earlier in the session: 6 are held.
        fake = _shut([_stop(status="partially_filled", filled_qty=4.0)], qty=6.0)

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [s["qty"] for s in _sells(fake)] == [6.0]
        _assert_exiting_once(fake, qty=6.0)

    def test_a_stop_on_a_book_an_exit_emptied_is_never_called_a_close(self) -> None:
        # The precondition broken on purpose: the lost exit lands on the
        # re-arm's POST and FILLS, as only regular hours allow, and a margin
        # account takes the re-armed stop as a short-sale stop. The verdict
        # reads the flat book and pages instead of reporting a close.
        fake = FakeBroker(
            [_position()], [_stop()], sell_in_flight={"XOM": 9}, allow_short=True
        )

        outcome = _close(fake)

        assert outcome.status == "unknown" and not outcome.ok
        assert "still stands on the flat book" in outcome.detail

    def test_an_exit_that_sells_less_than_is_held_is_not_a_close(self) -> None:
        # Something bought between the sizing read and the verdict: the exit
        # covers 10 of 15, and the other 5 have nothing.
        class _BoughtMeanwhile(FakeBroker):
            def submit_order(self, **kw) -> Order:
                order = super().submit_order(**kw)
                if kw.get("order_type") == "market":
                    self.positions["XOM"] = _position(qty=15.0)
                return order

        fake = _BoughtMeanwhile([_position()], [_stop()], exit_status="accepted")

        outcome = _close(fake)

        assert outcome.status == "naked" and "sells 10 of 15" in outcome.detail


class TestAnExitThatDidNotSellAtTheOpen:
    """A queued exit rejected, cancelled or expired at the open is named the next run."""

    def _yesterday(self, status: str, filled: float = 0.0) -> Order:
        return _order(
            "exit-y", "market", status=status, coid=YESTERDAY_STAMP, filled_qty=filled,
            submitted_at=datetime.now(UTC) - timedelta(days=1),
        )

    @pytest.mark.parametrize(("status", "filled"), [("canceled", 0.0), ("expired", 4.0),
                                                     ("rejected", 0.0)])
    def test_it_is_sold_again_and_the_miss_is_named(self, status: str, filled: float) -> None:
        held = 10.0 - filled
        fake = _shut([self._yesterday(status, filled)], qty=held)

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert outcome.missed_exits == (
            f"{YESTERDAY_STAMP} {status}, {filled:g} of 10 sold",
        )
        assert YESTERDAY_STAMP in outcome.detail
        _assert_exiting_once(fake, qty=held)

    def test_an_earlier_exit_that_sold_is_not_a_miss(self) -> None:
        fake = _shut([self._yesterday("filled", 10.0)])

        outcome = _close(fake)

        assert outcome.missed_exits == ()

    def test_an_exit_its_own_close_re_armed_beside_is_not_a_miss(self) -> None:
        # Rejected the night it was sent, and that close put the stop back:
        # the lot was never without one, and that night's run already paged.
        rearm = _stop("rearm-y", coid=_rearm_id(YESTERDAY_STAMP, "old-stop"))
        fake = _shut([self._yesterday("rejected"), rearm])

        outcome = _close(fake)

        assert outcome.missed_exits == ()

    def test_a_re_armed_stop_of_an_earlier_close_is_not_an_exit(self) -> None:
        rearm = _stop(coid=_rearm_id(YESTERDAY_STAMP, "old-stop"), status="canceled")
        fake = _shut([rearm, _stop("stop-2")])

        outcome = _close(fake)

        assert outcome.missed_exits == ()


class TestABackFillBesideAnExitNeverFound:
    """cover_beside_exit: a naked lot whose exit POST may still land.

    The exit is sent here with its reply lost, as the close sent it, and lands
    `lands_after` broker calls later: on the back-fill's own POST at 2 (the
    clock read is the first).
    """

    def _in_flight(self, lands_after: int, **kw) -> FakeBroker:
        kw.setdefault("exit_status", "accepted")
        fake = FakeBroker([_position()], sell_in_flight={"XOM": lands_after}, **kw)
        with pytest.raises(httpx.ReadTimeout):
            fake.submit_order(symbol="XOM", qty=10.0, side="sell", order_type="market",
                              time_in_force="day", client_order_id=STAMP)
        return fake

    def _cover(self, fake: FakeBroker):
        return cover_beside_exit(
            fake, "XOM", stamp=STAMP, qty=10.0, stop_price=90.0, sleep=lambda _: None
        )

    def test_an_exit_that_never_lands_leaves_the_back_fill_standing(self) -> None:
        fake = self._in_flight(10**6)

        outcome = self._cover(fake)

        assert outcome.status == "unchanged", outcome.detail
        (stop,) = fake.live_sells("XOM")
        assert stop.client_order_id == f"{STAMP}-cover"
        fake.land_in_flight()
        _assert_protected_once(fake)

    @pytest.mark.parametrize("reply", [None, httpx.ReadTimeout("reply lost")],
                             ids=["answered", "reply-lost"])
    def test_an_exit_landing_on_the_back_fill_is_the_only_seller(self, reply) -> None:
        fake = self._in_flight(2, stop_reply_error=reply)

        outcome = self._cover(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        _assert_exiting_once(fake)

    @pytest.mark.parametrize("stop_lands_after", [1, 5, 9])
    def test_a_back_fill_and_an_exit_both_landing_late_leave_one_seller(
        self, stop_lands_after: int
    ) -> None:
        fake = self._in_flight(3, stop_in_flight={"XOM": stop_lands_after})

        self._cover(fake)
        fake.land_in_flight()

        _assert_one_seller(fake)

    def test_with_the_market_open_nothing_is_placed(self) -> None:
        fake = self._in_flight(10**6, market_open=True)

        outcome = self._cover(fake)

        assert outcome.status == "unknown" and outcome.client_order_id == STAMP
        assert [c for c in fake.writes if c[1].get("order_type") == "stop"] == []


class TestClientIds:
    """Every order the close places has an id it can be found by, within Alpaca's cap."""

    def test_an_exit_stamp_of_any_day_is_read_back_and_nothing_else(self) -> None:
        assert exit_stamp_date(STAMP, "XOM") == DAY
        assert exit_stamp_date(f"{YESTERDAY_STAMP}-r3", "XOM") == DAY - timedelta(days=1)
        assert exit_stamp_date(_rearm_id(STAMP, "stop-xom"), "XOM") is None
        assert exit_stamp_date(_fit(f"{STAMP}-cover"), "XOM") is None
        assert exit_stamp_date(STAMP, "XO") is None
        assert exit_stamp_date("tr-XOM-20260928-SELL", "XOM") is None

    def test_a_re_arm_id_is_the_same_for_the_same_stamp_and_stop(self) -> None:
        uuid = "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415"
        assert _rearm_id(STAMP, uuid) == _rearm_id(STAMP, uuid)
        assert _rearm_id(STAMP, uuid) != _rearm_id(STAMP, "another-stop")
        assert _rearm_id(STAMP, uuid) != _rearm_id(f"{STAMP}-r2", uuid)

    def test_the_longest_stamp_and_an_alpaca_order_id_fit(self) -> None:
        stamp = f"{derive_exit_client_order_id('GOOGL', DAY, 'time')}-r5"
        rearm = _rearm_id(stamp, "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415")
        assert len(rearm) <= MAX_CLIENT_ID_LEN and rearm.startswith(f"{stamp}-arm-")
        assert len(_fit(f"{stamp}-cover")) <= MAX_CLIENT_ID_LEN

    def test_an_id_too_long_for_the_broker_becomes_a_digest_of_itself(self) -> None:
        long_id = "x" * (MAX_CLIENT_ID_LEN + 1)
        assert _fit(long_id) == _fit(long_id) and len(_fit(long_id)) <= MAX_CLIENT_ID_LEN


class TestThroughTheRealClient:
    """The refusal/timeout split, with AlpacaClient's own exceptions.

    The fake raises what the client raises, and these pin that it does: a 4xx
    from Alpaca must read as a refusal, and a transport timeout must not.
    """

    _NOW = "2026-09-28T22:30:00Z"

    def _order(self, oid: str, otype: str, status: str, **extra: str) -> dict:
        return {"id": oid, "client_order_id": extra.pop("coid", f"coid-{oid}"),
                "symbol": "XOM", "side": "sell", "qty": "10", "filled_qty": "0",
                "type": otype, "status": status, "submitted_at": self._NOW, **extra}

    def _client(self, monkeypatch: pytest.MonkeyPatch, handler) -> AlpacaClient:
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_API_SECRET", "s")
        client = AlpacaClient(base_url="https://paper-api.alpaca.markets/v2")
        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        return client

    def _broker(self, on_exit_post, on_lookup_after_exit):
        """A one-stop book after the close whose exit POST and stamp lookups are scripted."""
        state: dict = {"stop": "new", "exit_posted": False, "lookups": 0, "posts": [],
                       "orders": {}}

        def lookup(_: httpx.Request) -> httpx.Response:
            if not state["exit_posted"]:
                return httpx.Response(404, json={"message": "order not found"})
            state["lookups"] += 1
            reply = on_lookup_after_exit(state["lookups"])
            if reply.status_code == 200:
                state["orders"]["exit"] = reply.json()
            return reply

        def stop(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=self._order("stop-xom", "stop", state["stop"], stop_price="90")
            )

        def cancel(_: httpx.Request) -> httpx.Response:
            state["stop"] = "canceled"
            return httpx.Response(204)

        def post(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            state["posts"].append(body["type"])
            if body["type"] == "market":
                state["exit_posted"] = True
                return on_exit_post(request)
            placed = self._order("rearm-1", "stop", "new", coid=body["client_order_id"],
                                 stop_price=body["stop_price"])
            state["orders"]["rearm"] = placed
            return httpx.Response(200, json=placed)

        def listing(r: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[stop(r).json(), *state["orders"].values()])

        clock = {"is_open": False, "timestamp": "2026-09-28T18:30:00.123456789-04:00",
                 "next_open": "2026-09-29T09:30:00-04:00",
                 "next_close": "2026-09-29T16:00:00-04:00"}
        position = {
            "symbol": "XOM", "qty": "10", "side": "long", "avg_entry_price": "100",
            "market_value": "1005", "unrealized_pl": "5", "unrealized_plpc": "0.005",
        }
        routes = {
            ("GET", "/v2/clock"): lambda _: httpx.Response(200, json=clock),
            ("GET", "/v2/orders:by_client_order_id"): lookup,
            ("GET", "/v2/orders"): listing,
            ("GET", "/v2/orders/stop-xom"): stop,
            ("DELETE", "/v2/orders/stop-xom"): cancel,
            ("GET", "/v2/positions/XOM"): lambda _: httpx.Response(200, json=position),
            ("POST", "/v2/orders"): post,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            route = routes.get((request.method, request.url.path))
            if route is None:
                return httpx.Response(500, json={"message": f"unscripted {request.url}"})
            return route(request)

        return handler, state

    def test_a_4xx_on_the_exit_is_a_refusal_and_the_stop_goes_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        handler, state = self._broker(
            on_exit_post=lambda _: httpx.Response(
                403, json={"code": 40310000, "message": "insufficient qty available"}
            ),
            on_lookup_after_exit=lambda _: httpx.Response(500, json={"message": "internal"}),
        )

        outcome = close_with_protection(
            self._client(monkeypatch, handler), "XOM", trade_date=DAY, sleep=lambda _: None
        )

        assert outcome.status == "unchanged", outcome.detail
        assert state["posts"] == ["market", "stop"]

    def test_a_timeout_on_the_exit_is_not_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def timed_out(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("no reply", request=request)

        def lands_on_the_second_lookup(n: int) -> httpx.Response:
            if n < 2:
                return httpx.Response(404, json={"message": "order not found"})
            return httpx.Response(200, json=self._order("exit-1", "market", "accepted", coid=STAMP))

        handler, state = self._broker(timed_out, lands_on_the_second_lookup)

        outcome = close_with_protection(
            self._client(monkeypatch, handler), "XOM", trade_date=DAY, sleep=lambda _: None
        )

        assert outcome.status == "exit_submitted", outcome.detail
        assert state["posts"] == ["market"], "no stop re-armed beside a sell that landed"


class TestGetPosition:
    """AlpacaClient.get_position: the fresh read the sell is sized from."""

    @pytest.fixture()
    def client(self, monkeypatch: pytest.MonkeyPatch) -> AlpacaClient:
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_API_SECRET", "s")
        return AlpacaClient(base_url="https://paper-api.alpaca.markets/v2")

    def _serve(self, client: AlpacaClient, status: int, body: dict) -> list[str]:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(status, json=body)

        client._http = httpx.Client(transport=httpx.MockTransport(handler))
        return seen

    def test_no_position_is_none_not_an_error(self, client: AlpacaClient) -> None:
        seen = self._serve(client, 404, {"code": 40410000, "message": "position does not exist"})

        assert client.get_position("XOM") is None
        assert seen == ["/v2/positions/XOM"]

    def test_a_position_is_parsed(self, client: AlpacaClient) -> None:
        self._serve(client, 200, {
            "symbol": "XOM", "qty": "7", "side": "long", "avg_entry_price": "100",
            "market_value": "703.5", "unrealized_pl": "3.5", "unrealized_plpc": "0.005",
        })

        pos = client.get_position("XOM")

        assert pos is not None and (pos.symbol, pos.qty, pos.side) == ("XOM", 7.0, "long")

    def test_any_other_failure_raises(self, client: AlpacaClient) -> None:
        self._serve(client, 500, {"message": "internal"})

        with pytest.raises(httpx.HTTPStatusError):
            client.get_position("XOM")

