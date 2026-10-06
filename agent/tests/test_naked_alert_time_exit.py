"""The end-of-run coverage check on a night a time exit went out.

2026-10-05, prod paper: the 22:30 UTC position pass time-exited GOOGL. It
cancelled every protective sell on the lot and queued one market sell for all
32 shares for the next open, under the time-exit stamp. Minutes later the
coverage check at the end of the run logged "32/278 naked (11.5%)" and paged
"NAKED: ... GOOGL". The shares were not naked in any sense that matters: our
own exit reserved the whole lot (the broker refuses any other sell), the
market was shut so nothing could trigger, and the lot was on its way out at
the open. Every time-exit night paged like that, and the night after, the lot
sold, it announced a "recovery" from an exposure that never was.

These run the real `collect_facts` against a broker stub holding that book,
and then the cases that ARE naked and must keep paging: a sell that is not our
exit, an exit for fewer shares than are held, an exit an open has already met,
and a short.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from scripts import naked_alert as cli
from tradingagents_us.dataflows.alpaca_broker import Clock, Order, Position, Session
from tradingagents_us.execution.executor import derive_exit_client_order_id
from tradingagents_us.notifications.ops_channel import ChannelResult, Delivery

#: The run that sent the exit, and the one after it.
NIGHT_1 = datetime(2026, 10, 5, 22, 40, tzinfo=UTC)
NIGHT_2 = datetime(2026, 10, 6, 22, 40, tzinfo=UTC)
#: When the 22:30 pass queued GOOGL's exit.
EXIT_SENT = datetime(2026, 10, 5, 22, 31, tzinfo=UTC)
STAMP = derive_exit_client_order_id("GOOGL", date(2026, 10, 5), "time")

#: The rest of the book: 246 shares, every one behind a stop.
OTHERS = {"AAPL": 45, "JPM": 30, "META": 18, "MSFT": 40, "NVDA": 32, "UNH": 11, "V": 30, "XOM": 40}


def _sessions(skip: frozenset[date] = frozenset()) -> list[Session]:
    """Weekday sessions around the event, 13:30-20:00 UTC, less `skip` (holidays)."""
    out, day = [], date(2026, 9, 14)
    while day <= date(2026, 10, 16):
        if day.weekday() < 5 and day not in skip:
            out.append(Session(
                day,
                datetime.combine(day, time(13, 30), UTC),
                datetime.combine(day, time(20, 0), UTC),
            ))
        day += timedelta(days=1)
    return out


class Broker:
    """The reads `collect_facts` makes, off a fixed book. It refuses every write."""

    def __init__(
        self,
        positions: list[Position],
        orders: list[Order],
        now: datetime,
        sessions: list[Session] | None = None,
        calendar_error: Exception | None = None,
    ) -> None:
        self.positions, self.orders, self.now = positions, orders, now
        self.sessions = _sessions() if sessions is None else sessions
        self.calendar_error = calendar_error
        self.calendar_reads = 0

    def __enter__(self) -> Broker:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def list_positions(self) -> list[Position]:
        return list(self.positions)

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False) -> list:
        assert status == "all" and nested, "a bracket's held stop is only seen this way"
        return list(self.orders)

    def clock(self) -> Clock:
        upcoming = min(s.open for s in self.sessions if s.open > self.now)
        is_open = any(s.open <= self.now < s.close for s in self.sessions)
        return Clock(is_open, self.now.isoformat(), upcoming.isoformat(), upcoming.isoformat())

    def calendar(self, start: date, end: date) -> list[Session]:
        self.calendar_reads += 1
        if self.calendar_error is not None:
            raise self.calendar_error
        return [s for s in self.sessions if start <= s.date <= end]

    def submit_order(self, *a: object, **k: object) -> None:  # pragma: no cover - guard
        raise AssertionError("the coverage check is read-only")


_ids = iter(range(1, 10_000))


def _order(
    symbol: str,
    qty: float,
    *,
    side: str = "sell",
    order_type: str = "stop",
    status: str = "new",
    coid: str | None = None,
    filled: float = 0.0,
    sent: datetime = datetime(2026, 9, 30, 22, 35, tzinfo=UTC),
    stop: float | None = None,
    legs: tuple[Order, ...] = (),
) -> Order:
    n = next(_ids)
    return Order(
        id=f"ord-{n}",
        client_order_id=coid or f"coid-{n}",
        symbol=symbol,
        side=side,
        qty=qty,
        filled_qty=filled,
        order_type=order_type,
        status=status,
        submitted_at=sent,
        filled_avg_price=None,
        stop_price=stop if stop is not None else (100.0 if "stop" in order_type else None),
        legs=legs,
    )


def _held(symbol: str, qty: float, side: str = "long") -> Position:
    signed = qty if side == "long" else -qty
    return Position(symbol, signed, side, 100.0, 100.0 * qty, 0.0, 0.0)


def _rest_of_book() -> tuple[list[Position], list[Order]]:
    """The other eight names: half behind a bracket's held stop, half a back-filled GTC stop."""
    positions, orders = [], []
    for i, (symbol, qty) in enumerate(sorted(OTHERS.items())):
        positions.append(_held(symbol, qty))
        if i % 2:
            orders.append(_order(symbol, qty))
        else:
            legs = (
                _order(symbol, qty, order_type="limit", status="new"),
                _order(symbol, qty, status="held"),
            )
            orders.append(_order(
                symbol, qty, side="buy", order_type="market", status="filled",
                filled=qty, legs=legs,
            ))
    return positions, orders


def _googl_released() -> list[Order]:
    """GOOGL's protection as the time exit left it: a stop on most, three
    one-share brackets, every sell leg cancelled before the exit went in."""
    out = [_order("GOOGL", 29, status="canceled")]
    for _ in range(3):
        legs = (
            _order("GOOGL", 1, order_type="limit", status="canceled"),
            _order("GOOGL", 1, status="canceled"),
        )
        out.append(_order(
            "GOOGL", 1, side="buy", order_type="market", status="filled", filled=1, legs=legs,
        ))
    return out


def _exit(
    qty: float = 32,
    status: str = "accepted",
    coid: str = STAMP,
    sent: datetime = EXIT_SENT,
    filled: float = 0.0,
) -> Order:
    return _order(
        "GOOGL", qty, order_type="market", status=status, coid=coid, sent=sent, filled=filled
    )


class Sent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, title: str, body: str, kind: str = "ops") -> Delivery:
        self.calls.append((title, body, kind))
        return Delivery((ChannelResult("github", True, "stub"),))


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Sent:
    monkeypatch.setenv("NAKED_ALERT_STATE_PATH", str(tmp_path / "naked.state.json"))
    monkeypatch.setenv("KILL_SWITCH_PATH", str(tmp_path / "kill_switch.state"))
    monkeypatch.delenv("KILL_SWITCH_FILE", raising=False)
    sent = Sent()
    monkeypatch.setattr(cli, "send_ops_alert", sent)
    return sent


def _run(monkeypatch: pytest.MonkeyPatch, broker: Broker) -> int:
    monkeypatch.setattr(cli, "AlpacaClient", lambda: broker)
    return cli.main([])


def _night_1(googl: list[Order], googl_qty: float = 32, side: str = "long", **kw) -> Broker:
    positions, orders = _rest_of_book()
    return Broker(positions + [_held("GOOGL", googl_qty, side)], orders + googl, NIGHT_1, **kw)


def _naked_line(out: str) -> str:
    return next((ln for ln in out.splitlines() if ln.startswith("NAKED: ")), "")


# --------------------------------------------------------------------------- the live event


def test_the_googl_time_exit_night_is_not_a_naked_book(
    box: Sent, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    broker = _night_1(_googl_released() + [_exit()])
    assert _run(monkeypatch, broker) == cli.EXIT_COVERED

    out = capsys.readouterr().out
    assert _naked_line(out) == ""
    assert "stop coverage: 0/278 naked (0.0%)" in out
    # Reported, not hidden: the 32 shares are on their way out, not stopped.
    assert "exiting 32 (GOOGL)" in out
    assert box.calls == []


def test_the_night_after_the_exit_sold_announces_no_recovery(
    box: Sent, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert _run(monkeypatch, _night_1(_googl_released() + [_exit()])) == cli.EXIT_COVERED

    # The open sold the lot under the exit; the rest of the book is as it was.
    positions, orders = _rest_of_book()
    sold = _googl_released() + [_exit(status="filled", filled=32)]
    assert _run(monkeypatch, Broker(positions, orders + sold, NIGHT_2)) == cli.EXIT_COVERED

    # Nothing was exposed, so there is nothing to have recovered from.
    assert box.calls == []
    state = tmp_path / "naked.state.json"
    assert not state.exists() or '"naked"' not in state.read_text()


def test_an_exit_queued_over_an_exchange_holiday_still_covers(
    box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No session on 2026-10-06: the exit stamped the 5th still waits for an
    # open, and the run on the 6th finds it working under an earlier date's
    # stamp. The close leaves such a lot to that exit, and so does this.
    positions, orders = _rest_of_book()
    broker = Broker(
        positions + [_held("GOOGL", 32)], orders + _googl_released() + [_exit()], NIGHT_2,
        sessions=_sessions(skip=frozenset({date(2026, 10, 6)})),
    )
    assert _run(monkeypatch, broker) == cli.EXIT_COVERED


def test_a_book_with_no_exit_reads_no_calendar(
    box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    positions, orders = _rest_of_book()
    broker = Broker(positions, orders, NIGHT_1)
    assert _run(monkeypatch, broker) == cli.EXIT_COVERED
    assert broker.calendar_reads == 0


# --------------------------------------------------------------------------- still naked


def test_a_market_sell_that_is_not_our_exit_is_still_naked(
    box: Sent, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A close by hand, or anything else's: it reserves the shares all the
    # same, but nothing here knows what it is or when it goes away.
    foreign = _exit(coid="9b2f6c1e-hand-close")
    assert _run(monkeypatch, _night_1(_googl_released() + [foreign])) == cli.EXIT_NAKED
    assert _naked_line(capsys.readouterr().out).startswith("NAKED: 32 of 278 shares")


def test_an_exit_for_fewer_shares_than_held_leaves_the_rest_naked(
    box: Sent, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, _night_1(_googl_released() + [_exit(qty=20)])) == cli.EXIT_NAKED
    line = _naked_line(capsys.readouterr().out)
    assert line.startswith("NAKED: 12 of 278 shares")
    assert "GOOGL" in line
    assert "20 exiting" in line


@pytest.mark.parametrize(
    "status", ["rejected", "canceled", "expired", "done_for_day", "calculated"]
)
def test_an_earlier_exit_the_open_did_not_fill_is_dead_not_cover(
    status: str, box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Queued on Friday the 2nd for Monday's open, which refused it, expired
    # it or left it done for the day: a day order, it sells nothing more,
    # and the lot has had neither stop nor exit since.
    dead = _exit(
        status=status,
        coid=derive_exit_client_order_id("GOOGL", date(2026, 10, 2), "time"),
        sent=datetime(2026, 10, 2, 22, 31, tzinfo=UTC),
    )
    assert _run(monkeypatch, _night_1(_googl_released() + [dead])) == cli.EXIT_NAKED


def test_an_earlier_exit_still_listed_working_after_an_open_is_not_cover(
    box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Monday's session opened after it was sent, and a day market sell that
    # met an open and still reads as working is not one to vouch for.
    stuck = _exit(
        coid=derive_exit_client_order_id("GOOGL", date(2026, 10, 2), "time"),
        sent=datetime(2026, 10, 2, 22, 31, tzinfo=UTC),
    )
    assert _run(monkeypatch, _night_1(_googl_released() + [stuck])) == cli.EXIT_NAKED


@pytest.mark.parametrize("status", ["pending_cancel", "suspended"])
def test_an_exit_that_is_not_plainly_working_is_not_cover(
    status: str, box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Something is cancelling our exit, or it is suspended: neither is the
    # lot's way out at the open.
    assert _run(monkeypatch, _night_1(_googl_released() + [_exit(status=status)])) == (
        cli.EXIT_NAKED
    )


def test_a_short_is_naked_whatever_sells_stand_on_it(
    box: Sent, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A sell, stamped or a stop, adds to a short; only a buy stop protects it.
    # Read as a long of -32 shares, the old check counted it as nothing at all.
    on_it = [_exit(), _order("GOOGL", 32)]
    assert _run(monkeypatch, _night_1(on_it, side="short")) == cli.EXIT_NAKED
    assert _naked_line(capsys.readouterr().out).startswith("NAKED: 32 of 278 shares")


def test_an_unreadable_calendar_counts_no_exit_as_cover(
    box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Whether an open has met the exit is the calendar's to say. Without it
    # the check pages, and says why in the log, rather than vouch blind.
    broker = _night_1(
        _googl_released() + [_exit()], calendar_error=RuntimeError("calendar 503")
    )
    assert _run(monkeypatch, broker) == cli.EXIT_NAKED
