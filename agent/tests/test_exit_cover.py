"""Which working sells are our own time exit: `execution.exit_cover`.

The coverage check counts such an exit as `exiting` cover for its lot. Each test
here is a way a sell could look like that exit and not be one: the accounting
would then vouch for shares nothing is going to sell at the open.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from tradingagents_us.dataflows.alpaca_broker import Order, Position
from tradingagents_us.execution.executor import derive_exit_client_order_id
from tradingagents_us.execution.exit_cover import order_views, own_time_exit, position_views
from tradingagents_us.risk.stop_coverage import LIVE_STATUSES

SENT = datetime(2026, 10, 5, 22, 31, tzinfo=UTC)
STAMP = derive_exit_client_order_id("GOOGL", date(2026, 10, 5), "time")


def _never(when: datetime) -> bool:
    """No session has opened since `when`."""
    return False


def _always(when: datetime) -> bool:
    return True


def _order(
    coid: str = STAMP,
    *,
    symbol: str = "GOOGL",
    side: str = "sell",
    order_type: str = "market",
    status: str = "accepted",
    qty: float = 32,
    filled: float = 0,
) -> Order:
    return Order(
        id="o1", client_order_id=coid, symbol=symbol, side=side, qty=qty, filled_qty=filled,
        order_type=order_type, status=status, submitted_at=SENT, filled_avg_price=None,
    )


def test_the_stamped_market_sell_no_open_has_met_is_our_exit() -> None:
    assert own_time_exit(_order(), _never)


def test_a_retry_under_the_stamp_is_our_exit_too() -> None:
    assert own_time_exit(_order(f"{STAMP}-r2"), _never)


@pytest.mark.parametrize("status", sorted(LIVE_STATUSES))
def test_every_live_status_is_working(status: str) -> None:
    assert own_time_exit(_order(status=status), _never)


@pytest.mark.parametrize(
    "order",
    [
        _order("9b2f6c1e-hand-close"),  # a close by hand: not ours
        _order("tr-GOOGL-20261005-SELL"),  # a council SELL, not a time exit
        _order(f"{STAMP}-r2-arm-abc123", order_type="stop"),  # a re-armed stop
        _order(derive_exit_client_order_id("AAPL", date(2026, 10, 5), "time")),  # another name's
        _order(order_type="limit"),  # we only ever send the exit at market
        _order(side="buy"),
        _order(status="canceled"),
        _order(status="expired"),
        _order(status="done_for_day"),
        _order(status="calculated"),
        _order(status="pending_cancel"),
    ],
    ids=[
        "foreign", "council-sell", "re-armed-stop", "other-symbol", "limit", "buy",
        "canceled", "expired", "done_for_day", "calculated", "pending_cancel",
    ],
)
def test_lookalikes_are_not_our_exit(order: Order) -> None:
    assert not own_time_exit(order, _never)


def test_an_exit_an_open_has_met_is_not_vouched_for() -> None:
    assert not own_time_exit(_order(), _always)


def test_no_calendar_vouches_for_no_exit() -> None:
    assert not own_time_exit(_order(), None)


def test_the_calendar_is_asked_only_about_a_working_stamped_exit() -> None:
    asked: list[datetime] = []

    def opened(when: datetime) -> bool:
        asked.append(when)
        return False

    order_views([_order(status="filled", filled=32), _order("manual"), _order(side="buy")], opened)
    assert asked == []
    order_views([_order()], opened)
    assert asked == [SENT]


def test_views_carry_the_flag_and_the_remainder() -> None:
    (view,) = order_views([_order(status="partially_filled", filled=12)], _never)
    assert view.own_exit
    assert view.remaining_qty == 20
    (plain,) = order_views([_order()])
    assert not plain.own_exit


def test_a_short_is_read_unsigned_with_its_side() -> None:
    (view,) = position_views([Position("GOOGL", -32.0, "short", 100.0, -3200.0, 0.0, 0.0)])
    assert (view.qty, view.side) == (32.0, "short")
