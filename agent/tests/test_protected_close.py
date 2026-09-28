"""close_with_protection, one failure point at a time.

The property under test is the one stop_coverage.py and position_manager.py
both name: two sells on one long lot become a SHORT after a gap down. Every
test below breaks one step of cancel -> confirm -> read -> sell -> verify ->
re-arm against a broker fake that reserves shares the way Alpaca does, and
checks that the position ends up in one of two states:

  * still protected: held, and covered by no more stop than it was before; or
  * fully closed: nothing held, and no sell order left standing that could
    sell a share it does not have.

When the broker's state cannot be established, the outcome says `unknown` and
nothing further is sent: guessing there is how the second seller appears.

The fake refuses a sell on a flat symbol unless a test sets `allow_short`, which
accepts it as a margin account does. Those tests are the ones that can see the
double-covered end state: a sell stop left standing after the lot is gone.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from datetime import UTC, date, datetime

import httpx
import pytest

from tests.broker_fake import FakeBroker
from tradingagents_us.dataflows.alpaca_broker import (
    AlpacaClient,
    AlpacaRequestError,
    Order,
    Position,
)
from tradingagents_us.execution.executor import derive_exit_client_order_id
from tradingagents_us.execution.protected_close import (
    CANCEL_CONFIRM_INTERVAL_S,
    CANCEL_CONFIRM_POLLS,
    CANCEL_SETTLE_DELAYS_S,
    close_with_protection,
    cover_beside_exit,
)

DAY = date(2026, 9, 28)
STAMP = derive_exit_client_order_id("XOM", DAY, "time")

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
) -> Order:
    return Order(
        id=oid,
        client_order_id=coid or f"coid-{oid}",
        symbol="XOM",
        side="sell",
        qty=qty,
        filled_qty=0.0,
        order_type=order_type,  # type: ignore[arg-type]
        status=status,
        submitted_at=datetime.now(UTC),
        filled_avg_price=None,
        stop_price=stop_price,
    )


def _stop(oid: str = "stop-xom", qty: float = 10.0, price: float = 90.0, **kw) -> Order:
    return _order(oid, "stop", qty=qty, stop_price=price, **kw)


def _bracket() -> list[Order]:
    """A bracket's two legs: the take-profit works, the stop rests in `held`.

    Both reserve the same ten shares, which is what makes the release order and
    the re-arm cap matter. Listed stop first, so the take-profit-first release
    is the code's doing and not the fixture's.
    """
    return [_stop(status="held"), _order("tp-xom", "limit")]


def _two_brackets(**kw) -> FakeBroker:
    """20 shares bought as two brackets of 10: two take-profits, two held stops.

    Each take-profit is an OCO pair with its own stop, so cancelling one takes
    that stop with it, and the pair reserves its ten shares once. Listed stops
    first, then take-profits, so any release order is the code's doing.
    """
    return FakeBroker(
        [_position(qty=20.0)],
        [
            _stop("sl-a", status="held", price=90.0),
            _stop("sl-b", status="held", price=88.0),
            _order("tp-a", "limit"),
            _order("tp-b", "limit"),
        ],
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


def _assert_protected_once(fake: FakeBroker, qty: float = 10.0) -> None:
    """Held, and every held share under exactly one live stop."""
    assert fake.positions["XOM"].qty == qty
    stops = [o for o in fake.live_sells("XOM") if o.order_type == "stop"]
    assert sum(o.qty - o.filled_qty for o in stops) == qty
    assert [o for o in fake.live_sells("XOM") if o.order_type != "stop"] == []


def _assert_closed_clean(fake: FakeBroker) -> None:
    assert "XOM" not in fake.positions
    assert fake.live_sells("XOM") == [], "a sell left standing on a closed lot opens a short"


class TestTheHappyPath:
    def test_cancel_is_confirmed_and_the_holding_read_before_the_stamped_sell(self) -> None:
        fake = FakeBroker([_position()], [_stop()])

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok
        assert outcome.client_order_id == STAMP
        steps = [c[:2] if c[0] != "submit_order" else ("submit_order",) for c in fake.calls]
        assert steps == [
            ("get_order_by_client_order_id", STAMP),
            ("list_orders", "all"),
            ("cancel_order", "stop-xom"),
            ("get_order", "stop-xom"),
            ("get_position", "XOM"),
            ("submit_order",),
            ("get_order_by_client_order_id", STAMP),
        ]
        assert _sells(fake) == [
            {"symbol": "XOM", "qty": 10.0, "side": "sell", "order_type": "market",
             "time_in_force": "day", "client_order_id": STAMP}
        ]
        _assert_closed_clean(fake)

    def test_a_bracket_releases_its_take_profit_before_its_stop(self) -> None:
        fake = FakeBroker([_position()], _bracket())

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        cancels = [c[1] for c in fake.writes if c[0] == "cancel_order"]
        assert cancels == ["tp-xom", "stop-xom"]
        _assert_closed_clean(fake)

    def test_an_unprotected_position_is_sold_with_nothing_to_release(self) -> None:
        fake = FakeBroker([_position()])

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [c[0] for c in fake.writes] == ["submit_order"]
        _assert_closed_clean(fake)

    def test_an_exit_queued_for_the_open_counts_as_on_its_way_out(self) -> None:
        # The daily run is after the close: the market sell is accepted, not filled.
        fake = FakeBroker([_position()], [_stop()], exit_status="accepted")

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        (working,) = fake.live_sells("XOM")
        assert working.client_order_id == STAMP and working.order_type == "market"


class TestBeforeAnythingIsReleased:
    def test_an_exit_already_standing_under_todays_stamp_sends_nothing(self) -> None:
        fake = FakeBroker(
            [_position()], [_order("exit-1", "market", status="accepted", coid=STAMP)]
        )

        outcome = _close(fake)

        assert outcome.status == "already_exiting" and outcome.ok
        assert fake.writes == []

    def test_a_sell_in_an_unclear_status_blocks_the_whole_close(self) -> None:
        # `stopped` means the stop's fill is guaranteed and on its way: selling
        # beside it is the second seller.
        fake = FakeBroker([_position()], [_stop(status="stopped")])

        outcome = _close(fake)

        assert outcome.status == "unchanged" and not outcome.ok
        assert "stopped" in outcome.detail
        assert fake.writes == []

    def test_an_unreadable_book_sends_nothing(self) -> None:
        fake = FakeBroker([_position()], [_stop()], lookup_fails=True)

        outcome = _close(fake)

        assert outcome.status == "unchanged"
        assert fake.writes == []
        _assert_protected_once(fake)


class TestTheRelease:
    def test_a_refused_take_profit_cancel_leaves_the_stop_untouched(self) -> None:
        fake = FakeBroker([_position()], _bracket(), cancel_refused={"tp-xom"})

        outcome = _close(fake)

        assert outcome.status == "unchanged" and not outcome.ok
        assert fake.writes == [("cancel_order", "tp-xom")]
        assert {o.id for o in fake.live_sells("XOM")} == {"tp-xom", "stop-xom"}

    def test_a_stop_that_refuses_its_cancel_is_still_the_protection(self) -> None:
        # Refused, and still reading as working after the same window every
        # cancel gets: only then is the refusal taken at its word.
        fake = FakeBroker([_position()], [_stop()], cancel_refused={"stop-xom"})
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "unchanged"
        assert fake.writes == [("cancel_order", "stop-xom")]
        assert sleeps == CONFIRM_SLEEPS
        _assert_protected_once(fake)

    def test_a_cancel_that_never_lands_is_waited_out_and_not_called_protected(self) -> None:
        # pending_cancel can still fill, so nothing is sold or re-armed beside
        # it. But the cancel cannot be taken back either: when it lands the lot
        # has no stop. So the wait runs the whole settle window, and the outcome
        # pages instead of reading as protected.
        fake = FakeBroker([_position()], [_stop()], cancel_stuck={"stop-xom"})
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "unknown" and not outcome.ok
        assert "pending_cancel" in outcome.detail
        assert "10 shares have no stop when it lands" in outcome.detail
        assert fake.writes == [("cancel_order", "stop-xom")]
        assert sleeps == FULL_CANCEL_WAIT

    def test_a_cancel_accepted_but_slow_to_land_is_waited_for_and_the_lot_sold(self) -> None:
        # The DELETE was accepted, but the stop still reads `new` for longer
        # than the confirm window. It was reported `unchanged` and then the
        # cancel landed: no stop, no exit.
        fake = FakeBroker([_position()], [_stop()], slow_cancel={"stop-xom": ("new", 15)})

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [s["qty"] for s in _sells(fake)] == [10.0]
        _assert_closed_clean(fake)

    def test_a_cancel_accepted_and_never_seen_to_land_is_not_reported_unchanged(self) -> None:
        fake = FakeBroker([_position()], [_stop()], slow_cancel={"stop-xom": ("new", 10**6)})

        outcome = _close(fake)

        assert outcome.status == "unknown" and not outcome.ok
        # Nothing stacked on the shares the stop still reserves.
        assert fake.writes == [("cancel_order", "stop-xom")]

    def test_a_bracket_stop_already_cancelling_with_its_take_profit_is_polled(self) -> None:
        # Cancelling the take-profit takes its OCO stop with it: the stop reads
        # pending_cancel and refuses its own DELETE. One read and a refusal was
        # taken as final, and the lot was left with no stop and no exit.
        fake = FakeBroker(
            [_position()], _bracket(),
            oco={"tp-xom": "stop-xom"},
            slow_cancel={"stop-xom": ("pending_cancel", 3)},
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [c[1] for c in fake.writes if c[0] == "cancel_order"] == ["tp-xom", "stop-xom"]
        assert [s["qty"] for s in _sells(fake)] == [10.0]
        _assert_closed_clean(fake)

    @pytest.mark.parametrize("nests_oco", [True, False], ids=["nested", "flat-listing"])
    def test_a_bracket_stop_refusing_its_own_cancel_after_its_take_profit_is_waited_out(
        self, nests_oco: bool
    ) -> None:
        # Cancelling the take-profit took this stop with it at the broker, but
        # the stop still reads `held` past the confirm window, and its own
        # DELETE is refused (a 429 under load, or 422). The refusal was taken
        # at its word: `unchanged`, rc 1, and the stop then went with the
        # cascade, leaving ten shares with no stop and no exit. A listing that
        # does not show the pairing must not be read as proof there is none.
        fake = FakeBroker(
            [_position()], _bracket(),
            oco={"tp-xom": "stop-xom"},
            slow_cancel={"stop-xom": ("held", 12)},
            cancel_refused={"stop-xom"},
            nests_oco=nests_oco,
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted", outcome.detail
        assert [s["qty"] for s in _sells(fake)] == [10.0]
        _assert_closed_clean(fake)

    @pytest.mark.parametrize("nests_oco", [True, False], ids=["nested", "flat-listing"])
    def test_a_bracket_stop_whose_cascade_never_shows_is_not_called_protected(
        self, nests_oco: bool
    ) -> None:
        # The same, with the cascade never seen to land inside the settle
        # window. The stop still stands, so nothing is sold or stacked beside
        # it, but its cancel is on its way: the outcome must page.
        fake = FakeBroker(
            [_position()], _bracket(),
            oco={"tp-xom": "stop-xom"},
            slow_cancel={"stop-xom": ("held", 10**6)},
            cancel_refused={"stop-xom"},
            nests_oco=nests_oco,
        )
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "unknown", outcome.detail
        assert "10 shares have no stop when it lands" in outcome.detail
        assert _sells(fake) == [] and _rearms(fake) == []
        assert sleeps[: len(FULL_CANCEL_WAIT)] == FULL_CANCEL_WAIT

    def test_a_refusal_after_another_pairs_take_profit_is_still_taken_at_its_word(
        self,
    ) -> None:
        # A stop on shares of its own, beside a bracket whose take-profit and
        # stop went first. The listing pairs that take-profit with its own
        # stop, so it cannot be this one's partner: the refusal stands.
        fake = FakeBroker(
            [_position(qty=15.0)],
            [_stop("ms", qty=5.0, price=91.0), *_bracket()],
            oco={"tp-xom": "stop-xom"},
            cancel_refused={"ms"},
        )
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "unchanged", outcome.detail
        assert sleeps == CONFIRM_SLEEPS, "the confirm window only, no settle window"

    def test_a_cancel_whose_reply_was_lost_is_polled_not_taken_as_refused(self) -> None:
        fake = FakeBroker(
            [_position()], [_stop()],
            cancel_reply_lost={"stop-xom"},
            slow_cancel={"stop-xom": ("pending_cancel", 3)},
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [s["qty"] for s in _sells(fake)] == [10.0]
        _assert_closed_clean(fake)

    def test_a_second_stop_that_lands_late_is_waited_for_and_the_lot_sold_once(self) -> None:
        fake = FakeBroker(
            [_position()],
            [_stop("stop-a", qty=6.0, price=90.0), _stop("stop-b", qty=4.0, price=88.0)],
            slow_cancel={"stop-b": ("pending_cancel", 12)},
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert _rearms(fake) == []
        assert [s["qty"] for s in _sells(fake)] == [10.0]
        _assert_closed_clean(fake)

    def test_a_second_stop_that_never_lands_is_reported_with_its_uncovered_shares(self) -> None:
        fake = FakeBroker(
            [_position()],
            [_stop("stop-a", qty=6.0, price=90.0), _stop("stop-b", qty=4.0, price=88.0)],
            cancel_stuck={"stop-b"},
        )

        outcome = _close(fake)

        assert outcome.status == "unknown"
        assert _sells(fake) == []
        # The first stop is back. The second is not put back beside itself:
        # the broker's fresh read says it still reserves its four shares.
        assert _rearms(fake) == [(6.0, 90.0)]
        assert sum(o.qty for o in fake.live_sells("XOM")) == 10.0
        assert "4 shares have no stop when it lands" in outcome.detail

    def test_a_refusal_on_a_second_bracket_puts_back_the_first_brackets_stop(self) -> None:
        # Cancelling TP-A takes SL-A with it. When TP-B then refuses its cancel
        # and is still working, SL-A's ten shares have no stop. It was never
        # put back, and the outcome read `unchanged`: 20 held, 10 covered.
        fake = _two_brackets(cancel_refused={"tp-b"})

        outcome = _close(fake)

        assert outcome.status == "unchanged", outcome.detail
        assert _sells(fake) == []
        assert _rearms(fake) == [(10.0, 90.0)]
        # SL-B still covers its ten, beside its take-profit as one OCO pair.
        assert _stop_cover(fake) == 20.0

    def test_each_take_profit_is_followed_by_its_own_stop(self) -> None:
        fake = _two_brackets()

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [c[1] for c in fake.writes if c[0] == "cancel_order"] == [
            "tp-a", "sl-a", "tp-b", "sl-b",
        ]
        _assert_closed_clean(fake)

    def test_a_second_brackets_cancel_still_on_its_way_is_reported_not_called_covered(
        self,
    ) -> None:
        # TP-B's cancel was sent and never seen to land. When it does, SL-B
        # goes with it. SL-A is put back; SL-B's ten shares cannot be, while it
        # still stands, and the outcome has to say so.
        fake = _two_brackets(slow_cancel={"tp-b": ("new", 10**6)})

        outcome = _close(fake)

        assert outcome.status == "unknown", outcome.detail
        assert _rearms(fake) == [(10.0, 90.0)]
        assert "10 shares have no stop when it lands" in outcome.detail

    def test_a_pairing_the_listing_does_not_show_is_never_called_unchanged(self) -> None:
        # Flat listing: which stop goes with which take-profit is invisible, so
        # the pair's reservation cannot be counted once and the room to put
        # SL-A back cannot be established. Doing nothing is the safe side, but
        # it must page, not read as protected.
        fake = _two_brackets(cancel_refused={"tp-b"}, nests_oco=False)

        outcome = _close(fake)

        assert outcome.status == "naked", outcome.detail
        assert "10 shares" in outcome.detail
        assert _stop_cover(fake) <= 20.0

    def test_a_stop_a_released_take_profit_took_along_unseen_is_not_counted_as_cover(
        self,
    ) -> None:
        # The same flat listing, with TP-A's cascade still on its way: SL-A
        # reads `held` a while after its take-profit went. TP-B then refuses
        # its cancel and still works, so it is trusted as the blocker and
        # SL-A is never looked at. SL-A was counted as cover, the close said
        # `unchanged` (rc 1), and once the cascade landed ten shares had no
        # stop. Which standing stop is TP-A's cannot be told from this
        # listing, but one of them is on its way out.
        fake = _two_brackets(
            cancel_refused={"tp-b"}, nests_oco=False, slow_cancel={"sl-a": ("held", 30)}
        )

        outcome = _close(fake)

        assert outcome.status == "unknown", outcome.detail
        assert "10 shares have no stop when it lands" in outcome.detail
        assert _sells(fake) == [] and _rearms(fake) == []

    def test_a_stop_whose_take_profit_already_filled_is_left_alone(self) -> None:
        # Regular hours, seconds after a bracket's take-profit filled: its
        # stop still reads `held` while the broker's cancel of it is on its
        # way, and refuses a DELETE of its own. The newer back-filled stop M
        # was listed first and cancelled, the doomed stop was then trusted as
        # the protection, M could not go back beside it, and the close said
        # `unchanged` over a lot that was naked once the cascade landed. Left
        # alone, M covers it.
        filled_tp = dataclasses.replace(
            _order("tp1", "limit", status="filled"), filled_qty=10.0
        )
        fake = FakeBroker(
            [_position()],
            [_stop("M", price=85.0), filled_tp, _stop("sl1", status="held")],
            oco={"tp1": "sl1"},
            cancel_refused={"sl1"},
        )

        outcome = _close(fake)

        assert outcome.status == "unchanged", outcome.detail
        assert "sl1" in outcome.detail
        assert fake.writes == []
        assert {o.id for o in fake.live_sells("XOM")} == {"M", "sl1"}

    def test_a_stop_that_fired_during_the_release_leaves_nothing_to_sell(self) -> None:
        fake = FakeBroker([_position()], [_stop()], fill_on_cancel={"stop-xom"})

        outcome = _close(fake)

        assert outcome.status == "already_closed" and outcome.ok
        assert _sells(fake) == [] and _rearms(fake) == []
        _assert_closed_clean(fake)

    def test_the_sell_is_sized_off_the_holding_read_after_the_release(self) -> None:
        # One of two stops fired as it was being cancelled: 6 of 10 shares are
        # gone. A sell sized off the pass's earlier read would be 4 shares short.
        fake = FakeBroker(
            [_position()],
            [_stop("stop-a", qty=6.0), _stop("stop-b", qty=4.0)],
            fill_on_cancel={"stop-a"},
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert [s["qty"] for s in _sells(fake)] == [4.0]
        _assert_closed_clean(fake)


class TestAfterTheRelease:
    def test_a_refused_sell_re_arms_the_stop_at_its_level(self) -> None:
        fake = FakeBroker([_position()], [_stop()], refuse_sell={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "unchanged" and not outcome.ok
        assert _rearms(fake) == [(10.0, 90.0)]
        assert outcome.rearmed and outcome.released == ("stop-xom",)
        _assert_protected_once(fake)

    def test_a_sell_rejected_after_acceptance_re_arms_the_stop(self) -> None:
        fake = FakeBroker([_position()], [_stop()], reject_sell={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "unchanged"
        assert "rejected" in outcome.detail
        _assert_protected_once(fake)

    def test_a_retry_after_a_rejected_exit_uses_a_fresh_id(self) -> None:
        # Alpaca refuses a reused client_order_id even for a dead order.
        fake = FakeBroker([_position()], [_stop()], reject_sell={"XOM"})
        _close(fake)
        fake.reject_sell.clear()

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert outcome.client_order_id == f"{STAMP}-r2"
        _assert_closed_clean(fake)

    def test_a_lost_reply_is_settled_by_the_stamp_not_by_re_arming(self) -> None:
        # The sell was accepted and filled; only the reply went missing.
        # Re-arming here would put a stop on shares already sold.
        fake = FakeBroker([_position()], [_stop()], lose_sell_reply={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "exit_submitted"
        assert _rearms(fake) == []
        _assert_closed_clean(fake)

    def test_a_refused_exit_is_re_armed_even_when_its_stamp_cannot_be_looked_up(self) -> None:
        # A 4xx is the broker's answer: no order exists under the stamp. The
        # lookup failing after it must not leave the released lot with nothing.
        fake = FakeBroker(
            [_position()], [_stop()], refuse_sell={"XOM"}, lookup_fails_after_sell=True
        )

        outcome = _close(fake)

        assert outcome.status == "unchanged"
        assert _rearms(fake) == [(10.0, 90.0)]
        _assert_protected_once(fake)

    def test_an_exit_that_lands_after_its_timeout_is_found_not_re_armed_over(self) -> None:
        # The POST timed out while the broker was still taking it. One lookup
        # 404'd, the stop was re-armed, the sell then filled, and a margin
        # account took the re-armed stop as a short-sale stop on a flat book.
        fake = FakeBroker([_position()], [_stop()], sell_in_flight={"XOM": 2}, allow_short=True)

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.client_order_id == STAMP
        assert _rearms(fake) == []
        _assert_closed_clean(fake)

    def test_an_exit_absent_past_the_client_timeout_is_re_armed_off_a_fresh_read(self) -> None:
        # The request never reached the broker. Only a lookup made after the
        # client's own timeout may conclude that, and the re-arm is sized off
        # the orders and holding read after that lookup.
        fake = FakeBroker([_position()], [_stop()], sell_in_flight={"XOM": 10**6})
        sleeps: list[float] = []

        outcome = _close(fake, sleeps)

        assert outcome.status == "unchanged"
        assert sum(sleeps) > CLIENT_TIMEOUT_S
        tail = [c[:2] if c[0] != "submit_order" else (c[0], c[1]["order_type"])
                for c in fake.calls[-5:]]
        assert tail == [
            ("get_order_by_client_order_id", STAMP),
            ("list_orders", "all"),
            ("get_position", "XOM"),
            ("submit_order", "stop"),
            # Once more after the re-arm: the exit may have landed in between.
            ("get_order_by_client_order_id", STAMP),
        ]
        _assert_protected_once(fake)
        # Were it to land after all, the re-armed stop reserves the shares and
        # the broker turns it down: still one seller.
        fake.land_in_flight()
        _assert_protected_once(fake)

    def test_an_exit_landing_after_the_last_lookup_is_caught_by_the_fresh_read(self) -> None:
        # Six lookups find nothing, then the sell lands and fills just before
        # the re-arm reads the book. The re-arm must see the flat book.
        fake = FakeBroker([_position()], [_stop()], sell_in_flight={"XOM": 7}, allow_short=True)

        outcome = _close(fake)

        assert outcome.status == "already_closed" and outcome.ok
        assert _rearms(fake) == []
        _assert_closed_clean(fake)

    def test_an_exit_landing_between_the_fresh_read_and_the_re_arm_is_taken_back(
        self,
    ) -> None:
        # Regular hours: the lost exit lands and fills after the fresh read said
        # 10 held, just before the re-armed stop reaches the broker. A margin
        # account takes that stop on the flat book as a short-sale stop.
        fake = FakeBroker([_position()], [_stop()], sell_in_flight={"XOM": 9}, allow_short=True)

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        assert outcome.client_order_id == STAMP
        _assert_closed_clean(fake)

    @pytest.mark.parametrize("lands_after", [7, 8])
    def test_an_exit_queued_just_before_the_re_arm_is_reported_as_the_exit(
        self, lands_after: int
    ) -> None:
        # After hours the same landing queues the exit for the open. The lot is
        # on its way out, not unchanged (the stop was never put back beside it)
        # and not naked (the re-arm was refused because the exit reserves it).
        fake = FakeBroker(
            [_position()], [_stop()],
            sell_in_flight={"XOM": lands_after}, allow_short=True, exit_status="accepted",
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        (working,) = fake.live_sells("XOM")
        assert working.client_order_id == STAMP and working.order_type == "market"

    def test_an_unverifiable_exit_is_not_re_armed(self) -> None:
        fake = FakeBroker(
            [_position()], [_stop()], exit_status="accepted", lookup_fails_after_sell=True
        )

        outcome = _close(fake)

        assert outcome.status == "unknown" and not outcome.ok
        assert _rearms(fake) == []
        # The exit is working and is the only seller.
        (working,) = fake.live_sells("XOM")
        assert working.client_order_id == STAMP

    def test_an_unreadable_holding_re_arms_and_sells_nothing(self) -> None:
        fake = FakeBroker([_position()], [_stop()], position_unreadable={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "unchanged"
        assert _sells(fake) == []
        _assert_protected_once(fake)

    def test_a_take_profit_fill_during_the_release_is_not_re_covered(self) -> None:
        # The bracket's legs reserve the same shares. The take-profit filled as
        # it was cancelled, so the stop's ten shares are gone with it: re-arming
        # it off its own size, with the holding unreadable, is an orphan stop.
        fake = FakeBroker(
            [_position()], _bracket(), fill_on_cancel={"tp-xom"}, position_unreadable={"XOM"}
        )

        outcome = _close(fake)

        assert _rearms(fake) == []
        assert outcome.rearmed == ()
        _assert_closed_clean(fake)

    def _unpaired_limit(self, **kw) -> FakeBroker:
        """20 held: a stop over 10, and a limit sell placed by hand over the
        other 10, paired with nothing. The limit fills as it is cancelled."""
        return FakeBroker(
            [_position(qty=20.0)], [_stop(), _order("lim-xom", "limit")],
            fill_on_cancel={"lim-xom"}, **kw,
        )

    def test_an_unpaired_sell_filling_in_the_release_leaves_the_stop_its_own_shares(
        self,
    ) -> None:
        # The limit's ten shares were never the stop's, but the re-arm cap
        # took them off the stop's cover all the same. The exit is refused, and
        # nothing was re-armed: ten shares that had a stop had none.
        fake = self._unpaired_limit(refuse_sell={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "unchanged", outcome.detail
        assert _rearms(fake) == [(10.0, 90.0)]
        _assert_protected_once(fake)

    def test_an_unpaired_sell_filling_beside_an_unreadable_holding_is_not_unchanged(
        self,
    ) -> None:
        # The same, with the holding unreadable after the release. Without it,
        # nothing tells whether the limit sold its own shares or (had the
        # listing hidden a pairing) the stop's, so the blind re-arm does not
        # guess. It reported `unchanged` over ten shares with no stop.
        fake = self._unpaired_limit(position_unreadable={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "unknown", outcome.detail
        assert "10 shares a stop covered were not put back" in outcome.detail
        assert _sells(fake) == [] and _rearms(fake) == []

    def test_a_failed_re_arm_is_reported_naked(self) -> None:
        fake = FakeBroker([_position()], [_stop()], refuse_sell={"XOM"}, refuse_stop={"XOM"})

        outcome = _close(fake)

        assert outcome.status == "naked" and not outcome.ok
        assert "stop refused" in outcome.detail

    def test_a_short_is_never_sold(self) -> None:
        fake = FakeBroker([_position(side="short")])

        outcome = _close(fake)

        assert outcome.status == "unknown"
        assert fake.writes == []


class _LookupDownOnceReArmed(FakeBroker):
    """Client-id lookups time out from the moment a stop has been re-armed."""

    def get_order_by_client_order_id(self, client_order_id: str) -> Order | None:
        if any(o.order_type == "stop" for o in self.created):
            self._record("get_order_by_client_order_id", client_order_id)
            raise httpx.ReadTimeout("order lookup timed out")
        return super().get_order_by_client_order_id(client_order_id)


def _protected_or_closed(fake: FakeBroker, qty: float = 10.0) -> None:
    """The only two acceptable ends: held under one stop, or flat with no sell."""
    if "XOM" in fake.positions:
        _assert_protected_once(fake, qty)
    else:
        _assert_closed_clean(fake)


class TestAReArmBesideAnExitThatLanded:
    """The exit's POST lost its reply, and it lands just as the stop is re-armed.

    In regular hours it fills, and the re-armed stop is then a sell stop on a
    flat book: a margin account takes it as a short-sale stop. The broker that
    lost one reply is the likeliest to lose the next, so the re-arm's own reply
    is lost here too, or the take-back is throttled, or the lookup goes down.
    Nine calls after the exit POST is the landing on the re-arm itself.
    """

    LANDS_ON_THE_RE_ARM = 9

    @pytest.mark.parametrize(
        "error",
        [httpx.ReadTimeout("reply lost"), AlpacaRequestError("POST", "/orders", 504, "gateway")],
        ids=["timeout", "504"],
    )
    def test_a_re_arm_whose_reply_is_lost_is_taken_back(self, error: Exception) -> None:
        fake = FakeBroker(
            [_position()], [_stop()], stop_reply_error=error,
            sell_in_flight={"XOM": self.LANDS_ON_THE_RE_ARM}, allow_short=True,
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        _assert_closed_clean(fake)

    @pytest.mark.parametrize("lands_after", range(5, 16))
    def test_two_brackets_whose_re_arms_all_answer_504_end_protected_or_closed(
        self, lands_after: int
    ) -> None:
        fake = FakeBroker(
            [_position(qty=20.0)],
            [
                _stop("sl-a", status="held", price=90.0),
                _stop("sl-b", status="held", price=88.0),
                _order("tp-a", "limit"),
                _order("tp-b", "limit"),
            ],
            oco={"tp-a": "sl-a", "tp-b": "sl-b"},
            stop_reply_error=AlpacaRequestError("POST", "/orders", 504, "gateway timeout"),
            sell_in_flight={"XOM": lands_after}, allow_short=True,
        )

        outcome = _close(fake)
        fake.land_in_flight()

        _protected_or_closed(fake, qty=20.0)
        if "XOM" not in fake.positions:
            assert outcome.ok, outcome.detail

    def test_a_re_arm_whose_reply_is_lost_is_found_and_counted_when_no_exit_lands(
        self,
    ) -> None:
        # The stop is at the broker; only its reply went missing. Reported as
        # naked, the lot paged as uncovered while it was protected.
        fake = FakeBroker(
            [_position()], [_stop()], stop_reply_error=httpx.ReadTimeout("reply lost"),
            sell_in_flight={"XOM": 10**6},
        )

        outcome = _close(fake)

        assert outcome.status == "unchanged", outcome.detail
        assert outcome.rearmed
        _assert_protected_once(fake)

    def test_a_throttled_take_back_is_sent_again_until_the_stop_is_gone(self) -> None:
        fake = FakeBroker(
            [_position()], [_stop()], throttle_own_cancels=True,
            sell_in_flight={"XOM": self.LANDS_ON_THE_RE_ARM}, allow_short=True,
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        _assert_closed_clean(fake)
        (rearmed,) = [o.id for o in fake.created if o.order_type == "stop"]
        assert [c for c in fake.writes if c == ("cancel_order", rearmed)] == [
            ("cancel_order", rearmed)
        ] * 2

    def test_a_lookup_down_after_the_re_arm_reads_the_flat_book_instead(self) -> None:
        # The stamp cannot be looked up again. The listing shows the exit
        # filled and the holding is gone: the re-armed stop comes off.
        fake = _LookupDownOnceReArmed(
            [_position()], [_stop()],
            sell_in_flight={"XOM": self.LANDS_ON_THE_RE_ARM}, allow_short=True,
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        _assert_closed_clean(fake)

    def test_an_unanswered_re_arm_that_cannot_be_looked_up_is_found_in_the_listing(
        self,
    ) -> None:
        # Its reply is lost and its client id cannot be looked up either: the
        # listing read beside the landed exit still shows it, and it comes off.
        fake = _LookupDownOnceReArmed(
            [_position()], [_stop()], stop_reply_error=httpx.ReadTimeout("reply lost"),
            sell_in_flight={"XOM": self.LANDS_ON_THE_RE_ARM}, allow_short=True,
        )

        outcome = _close(fake)

        assert outcome.ok, outcome.detail
        _assert_closed_clean(fake)

    def test_a_re_arm_landing_after_its_lookups_is_not_ruled_out_beside_a_landed_exit(
        self,
    ) -> None:
        # Both POSTs lose their reply and land late by the same amount: the
        # exit on the re-arm's own POST, the re-arm nine calls after that,
        # past its 23 s of lookups. A 404 at the end of that window counted
        # as "not at the broker", so the take-back read a listing the stop
        # was not in yet and the close said `exit_submitted` (rc 0). The stop
        # then landed on the flat book: a short-sale stop nothing withdraws.
        # The window is not trusted for the exit, nor for a stop beside it.
        fake = FakeBroker(
            [_position()], [_stop()],
            sell_in_flight={"XOM": self.LANDS_ON_THE_RE_ARM},
            stop_in_flight={"XOM": self.LANDS_ON_THE_RE_ARM},
            allow_short=True,
        )

        outcome = _close(fake)

        assert outcome.status == "unknown" and not outcome.ok, outcome.detail
        assert "may yet stand" in outcome.detail
        assert outcome.client_order_id == STAMP

    def test_a_late_re_arm_found_in_the_listing_beside_a_landed_exit_is_taken_back(
        self,
    ) -> None:
        # The same, with the stop landing before the take-back reads the
        # listing: it is there, it comes off, and the close is a close.
        fake = FakeBroker(
            [_position()], [_stop()],
            sell_in_flight={"XOM": self.LANDS_ON_THE_RE_ARM},
            stop_in_flight={"XOM": 7},
            allow_short=True,
        )

        outcome = _close(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        _assert_closed_clean(fake)


class TestABackFillBesideAnExitNeverRuledOut:
    """cover_beside_exit: a naked lot whose exit POST is still in flight.

    The exit is sent here with its reply lost, as the close sent it, and lands
    `lands_after` broker calls later: on the back-fill's own POST at 1.
    """

    def _in_flight(self, lands_after: int, **kw) -> FakeBroker:
        fake = FakeBroker([_position()], sell_in_flight={"XOM": lands_after}, **kw)
        with pytest.raises(httpx.ReadTimeout):
            fake.submit_order(symbol="XOM", qty=10.0, side="sell", order_type="market",
                              time_in_force="day", client_order_id=STAMP)
        return fake

    def _cover(self, fake: FakeBroker):
        return cover_beside_exit(
            fake, "XOM", stamp=STAMP, qty=10.0, stop_price=90.0, sleep=lambda _: None
        )

    @pytest.mark.parametrize("reply", [None, httpx.ReadTimeout("reply lost")],
                             ids=["answered", "reply-lost"])
    def test_an_exit_that_fills_on_the_back_fill_takes_it_back(self, reply) -> None:
        fake = self._in_flight(1, allow_short=True, stop_reply_error=reply)

        outcome = self._cover(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        _assert_closed_clean(fake)

    def test_a_back_fill_landing_after_its_lookups_beside_a_landed_exit_is_reported(
        self,
    ) -> None:
        # The back-fill's POST loses its reply too, and lands after its 23 s
        # of lookups, on a book the exit has emptied by then. It was settled
        # as absent and the outcome cleared the name off `uncovered`.
        fake = self._in_flight(1, allow_short=True, stop_in_flight={"XOM": 9})

        outcome = self._cover(fake)

        assert outcome.status == "unknown" and not outcome.ok, outcome.detail
        assert "may yet stand" in outcome.detail

    def test_an_exit_that_never_lands_leaves_the_back_fill_standing(self) -> None:
        fake = self._in_flight(10**6)

        outcome = self._cover(fake)

        assert outcome.status == "unchanged", outcome.detail
        _assert_protected_once(fake)
        fake.land_in_flight()
        _assert_protected_once(fake)

    def test_an_exit_queued_on_the_back_fill_is_the_only_seller(self) -> None:
        fake = self._in_flight(1, exit_status="accepted")

        outcome = self._cover(fake)

        assert outcome.status == "exit_submitted" and outcome.ok, outcome.detail
        (working,) = fake.live_sells("XOM")
        assert working.client_order_id == STAMP


class _ReArmRefused(FakeBroker):
    """Every re-armed stop is turned down with a 4xx (its level is at the market now)."""

    def submit_order(self, **kw) -> Order:
        if kw.get("order_type") == "stop" and "-arm-" in (kw.get("client_order_id") or ""):
            self._record("submit_order", kw)
            raise AlpacaRequestError("POST", "/orders", 422, "stop price must be below market")
        return super().submit_order(**kw)


class _HoldingUnreadableOnceSold(FakeBroker):
    """Once the exit has been sent, the holding cannot be read for three tries."""

    unreadable = 3

    def get_position(self, symbol: str) -> Position | None:
        if self._sold and self.unreadable:
            self.unreadable -= 1
            self._record("get_position", symbol)
            raise httpx.ConnectError("positions endpoint unreachable")
        return super().get_position(symbol)


class TestAnExitNeverRuledOutHandsOnItsStamp:
    """The exit's POST went unanswered and was never found: it can still land.

    Where a stop was re-armed, it reserves the shares and a later landing is
    refused. Where nothing was re-armed (a naked lot, a re-arm the broker
    refused, a holding that could not be read), nothing reserves them. A
    back-fill placed after this close must then look the stamp up once its
    stop stands, and it can only do that if the close hands the stamp on.
    """

    @pytest.mark.parametrize(
        ("make", "status"),
        [
            (lambda **kw: FakeBroker([_position()], **kw), "unchanged"),
            (lambda **kw: _ReArmRefused([_position()], [_stop()], **kw), "naked"),
            (lambda **kw: _HoldingUnreadableOnceSold([_position()], [_stop()], **kw), "unknown"),
        ],
        ids=["naked-lot", "re-arm-refused", "holding-unreadable"],
    )
    def test_whatever_the_close_ends_in(self, make, status: str) -> None:
        fake = make(sell_in_flight={"XOM": 10**6})

        outcome = _close(fake)

        assert outcome.status == status, outcome.detail
        assert outcome.rearmed == ()
        assert outcome.client_order_id == STAMP


class TestThroughTheRealClient:
    """The refusal/timeout split, with AlpacaClient's own exceptions.

    The fake raises what the client raises, and these pin that it does: a 4xx
    from Alpaca must read as a refusal, and a transport timeout must not.
    """

    _NOW = "2026-09-28T20:30:00Z"

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
        """A one-stop book whose exit POST and later stamp lookups are scripted."""
        state = {"stop": "new", "exit_posted": False, "lookups": 0, "posts": []}

        def lookup(_: httpx.Request) -> httpx.Response:
            if not state["exit_posted"]:
                return httpx.Response(404, json={"message": "order not found"})
            state["lookups"] += 1
            return on_lookup_after_exit(state["lookups"])

        def stop(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=self._order("stop-xom", "stop", state["stop"], stop_price="90")
            )

        def cancel(_: httpx.Request) -> httpx.Response:
            state["stop"] = "canceled"
            return httpx.Response(204)

        def post(request: httpx.Request) -> httpx.Response:
            kind = json.loads(request.content)["type"]
            state["posts"].append(kind)
            if kind == "market":
                state["exit_posted"] = True
                return on_exit_post(request)
            return httpx.Response(200, json=self._order("rearm-1", "stop", "new"))

        position = {
            "symbol": "XOM", "qty": "10", "side": "long", "avg_entry_price": "100",
            "market_value": "1005", "unrealized_pl": "5", "unrealized_plpc": "0.005",
        }
        routes = {
            ("GET", "/v2/orders:by_client_order_id"): lookup,
            ("GET", "/v2/orders"): lambda r: httpx.Response(200, json=[stop(r).json()]),
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

        assert outcome.status == "unchanged"
        assert state["posts"] == ["market", "stop"]

    def test_a_timeout_on_the_exit_is_not_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def timed_out(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("no reply", request=request)

        def lands_on_the_second_lookup(n: int) -> httpx.Response:
            if n < 2:
                return httpx.Response(404, json={"message": "order not found"})
            return httpx.Response(200, json=self._order(
                "exit-1", "market", "filled", coid=STAMP, filled_qty="10"
            ))

        handler, state = self._broker(timed_out, lands_on_the_second_lookup)

        outcome = close_with_protection(
            self._client(monkeypatch, handler), "XOM", trade_date=DAY, sleep=lambda _: None
        )

        assert outcome.status == "exit_submitted"
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
