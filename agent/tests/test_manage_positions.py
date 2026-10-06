"""scripts.manage_positions end to end, against a stub broker.

The planner has its own tests; nothing covered the runner that turns its plan
into broker calls. These drive `main()` with a real bar cache and a fake broker
that records every call and reserves shares for live sell orders the way Alpaca
does, and assert on what would reach the broker: nothing in a dry run, exactly
the plan with --submit, and one bad symbol never stopping the rest of the pass.

The time exit used to be a bare DELETE /positions/{symbol}. On any position
whose GTC stop reserves the shares (every bracket entry, and every stop this
pass back-fills) the broker refused it, and a close that did go through carried
a broker id and booked as an operator flatten. It now releases the stop, sells
under the time-exit stamp and re-arms on failure; the step-by-step failure
points are in test_protected_close.py, and the pass-level wiring is here.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine

from scripts import manage_positions as mp
from tests.broker_fake import FakeBroker
from tradingagents_us.dataflows.alpaca_broker import (
    AlpacaRequestError,
    FillActivity,
    Order,
    Position,
)
from tradingagents_us.execution.executor import derive_exit_client_order_id
from tradingagents_us.execution.protected_close import (
    LISTING_CATCH_UP_DELAYS_S,
    CloseOutcome,
    _rearm_id,
)
from tradingagents_us.risk.position_manager import PlaceStop, RatchetStop, TimeExit
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.price_cache import write_bars

TODAY = datetime.now(UTC).date()
#: Entered well before every bar in the cache, so all of them count as held.
ENTRY = TODAY - timedelta(days=50)
#: A flat tape: every true range is 2.0, so the ATR is exactly 2.0.
BAR_DAYS = 45

#: The stamp today's time exit on XOM carries, and what the ledger reads.
XOM_EXIT_ID = derive_exit_client_order_id("XOM", TODAY, "time")


def _position(symbol: str, price: float, avg: float = 100.0, qty: float = 10.0) -> Position:
    return Position(
        symbol=symbol,
        qty=qty,
        side="long",
        avg_entry_price=avg,
        market_value=qty * price,
        unrealized_pl=qty * (price - avg),
        unrealized_plpc=price / avg - 1.0,
    )


def _stop(order_id: str, symbol: str, stop_price: float, qty: float = 10.0) -> Order:
    return Order(
        id=order_id,
        client_order_id=f"coid-{order_id}",
        symbol=symbol,
        side="sell",
        qty=qty,
        filled_qty=0.0,
        order_type="stop",
        status="new",
        submitted_at=datetime.now(UTC),
        filled_avg_price=None,
        stop_price=stop_price,
    )


def _buy(symbol: str, qty: float = 10.0) -> FillActivity:
    return FillActivity(
        id=f"fill-{symbol}",
        symbol=symbol,
        side="buy",
        qty=qty,
        price=100.0,
        transaction_time=datetime.combine(ENTRY, datetime.min.time(), UTC),
        order_id=f"buy-{symbol}",
    )


def _book(**kw) -> FakeBroker:
    """One position per branch of the plan.

    XOM   flat for 45 bars under a covering stop  -> TimeExit
    AAPL  up 20% with its stop at 90              -> RatchetStop 90 -> 114
    MSFT  up 33% with no stop at all              -> PlaceStop 10 @ 194
    NVDA  flat for 50 days, bar cache empty       -> no action, reported
    """
    return FakeBroker(
        positions=[
            _position("XOM", 100.5),
            _position("AAPL", 120.0),
            _position("MSFT", 200.0, avg=150.0),
            _position("NVDA", 100.5),
        ],
        orders=[
            _stop("stop-xom", "XOM", 90.0),
            _stop("stop-aapl", "AAPL", 90.0),
            _stop("stop-nvda", "NVDA", 90.0),
        ],
        fills=[_buy(s) for s in ("XOM", "AAPL", "MSFT", "NVDA")],
        **kw,
    )


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A bar cache holding a flat tape for every name except NVDA."""
    monkeypatch.setenv("KILL_SWITCH_FILE", str(tmp_path / "kill_switch.state"))
    url = f"sqlite:///{tmp_path / 'bars.db'}"
    repo = TradeLogRepository(engine=create_engine(url, future=True))
    flat = {"o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0}
    bars = [{"t": (TODAY - timedelta(days=d)).isoformat(), **flat} for d in range(BAR_DAYS, 0, -1)]
    # MSFT trades at 200, and its bars say so: a mark twice its last close is
    # a bad number the stop logic refuses to move a stop off. AAPL has run to
    # 120 and its bars show that too: a stop trails the lower of mark and close.
    msft = [{**b, "o": 200.0, "h": 201.0, "l": 199.0, "c": 200.0} for b in bars]
    aapl = [{**b, "o": 120.0, "h": 121.0, "l": 119.0, "c": 120.0} for b in bars]
    with repo.session() as session:
        write_bars(session, "XOM", bars)
        write_bars(session, "AAPL", aapl)
        write_bars(session, "MSFT", msft)
    return url


def _run(monkeypatch: pytest.MonkeyPatch, fake: FakeBroker, *argv: str) -> int:
    monkeypatch.setattr(mp, "AlpacaClient", lambda: fake)
    return mp.main(list(argv))


def _clock_at(monkeypatch: pytest.MonkeyPatch, fake: FakeBroker, when: datetime) -> None:
    """The pass and the fake's clock at `when`, the next open at the 13:30 UTC after it."""

    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001, ANN206 — datetime's own signature
            return when

    nxt = datetime.combine(when.date(), time(13, 30), UTC)
    if nxt <= when:
        nxt += timedelta(days=1)
    monkeypatch.setattr(mp, "datetime", _Now)
    fake.now, fake.minutes_to_open = when, (nxt - when).total_seconds() / 60


XOM_EXIT = (
    "submit_order",
    {
        "symbol": "XOM",
        "qty": 10.0,
        "side": "sell",
        "order_type": "market",
        "time_in_force": "day",
        "client_order_id": XOM_EXIT_ID,
    },
)

XOM_REARM = (
    "submit_order",
    {
        "symbol": "XOM",
        "qty": 10.0,
        "side": "sell",
        "order_type": "stop",
        "time_in_force": "gtc",
        "stop_price": 90.0,
        # Its own client id, so a re-arm whose reply is lost can be found.
        "client_order_id": _rearm_id(XOM_EXIT_ID, "stop-xom"),
    },
)

EXPECTED_PLAN = [
    ("cancel_order", "stop-xom"),
    XOM_EXIT,
    ("replace_order", "stop-aapl", {"stop_price": 114.0}),
    (
        "submit_order",
        {
            "symbol": "MSFT",
            "qty": 10.0,
            "side": "sell",
            "order_type": "stop",
            "time_in_force": "gtc",
            "stop_price": 194.0,
        },
    ),
]


def _kinds(fake: FakeBroker) -> list[tuple[str, str]]:
    """Each write as (call, symbol or order id), in order."""
    out = []
    for call in fake.writes:
        arg = call[1]
        out.append((call[0], arg["symbol"] if isinstance(arg, dict) else arg))
    return out


class TestDryRunVersusSubmit:
    def test_a_dry_run_sends_nothing(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _book()

        rc = _run(monkeypatch, fake, "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.writes == []
        # The plan is still described in full, so a dry run is a real preview.
        assert sum("[dry run]" in r.getMessage() for r in caplog.records) == 3
        assert "dry run — pass --submit to act" in caplog.text

    def test_submit_sends_exactly_the_plan(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _book()

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.writes == EXPECTED_PLAN

    def test_without_backfill_a_naked_name_is_reported_not_protected(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _book()

        _run(monkeypatch, fake, "--submit", "--db-url", db_url)

        stops = [w for w in fake.writes if w[0] == "submit_order" and w[1]["order_type"] == "stop"]
        assert stops == []

    def test_flatten_all_skips_the_pass_before_any_broker_call(
        self, db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "kill_switch.state").write_text("FLATTEN_ALL")
        fake = _book()

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.calls == []


class TestPerSymbolFailureIsolation:
    def test_every_action_is_attempted_and_each_failure_counted(self) -> None:
        fake = _book(refuse_sell={"XOM", "NVDA"})

        failures = mp._execute(
            fake,
            [
                TimeExit("XOM", 10.0, 45, 0.005),
                RatchetStop("AAPL", "stop-aapl", 90.0, 114.0, 2.0),
                TimeExit("NVDA", 10.0, 30, 0.001),
                PlaceStop("MSFT", 10.0, 194.0, 2.0),
            ],
        )

        assert failures == 2
        # Each refused exit puts its stop back before the pass moves on.
        assert _kinds(fake) == [
            ("cancel_order", "stop-xom"),
            ("submit_order", "XOM"),
            ("submit_order", "XOM"),
            ("replace_order", "stop-aapl"),
            ("cancel_order", "stop-nvda"),
            ("submit_order", "NVDA"),
            ("submit_order", "NVDA"),
            ("submit_order", "MSFT"),
        ]

    def test_a_refused_exit_is_counted_and_the_rest_of_the_pass_still_runs(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _book(refuse_sell={"XOM"})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        assert ("replace_order", "stop-aapl", {"stop_price": 114.0}) in fake.writes
        assert ("submit_order", "MSFT") in _kinds(fake)
        assert "XOM    FAILED" in caplog.text
        assert "market closed for this symbol" in caplog.text


def _aged_xom(**kw) -> FakeBroker:
    """XOM alone: flat for 45 bars, fully covered by a GTC stop -> TimeExit."""
    return FakeBroker(
        positions=[_position("XOM", 100.5)],
        orders=[_stop("stop-xom", "XOM", 90.0)],
        fills=[_buy("XOM")],
        **kw,
    )


class TestTimeExitReleasesItsStop:
    """The time exit is the system's only rule-driven exit.

    Against a broker that reserves shares, a close that leaves the stop in place
    is refused, and one that releases it must never leave two sellers on the lot.
    """

    def test_the_stop_is_released_then_the_holding_sold_under_the_exit_stamp(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _aged_xom()

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.writes == [("cancel_order", "stop-xom"), XOM_EXIT]
        assert "XOM" not in fake.positions
        assert fake.live_sells("XOM") == []

    def test_a_sell_that_fails_after_the_release_re_arms_the_stop(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _aged_xom(refuse_sell={"XOM"})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        assert fake.writes == [("cancel_order", "stop-xom"), XOM_EXIT, XOM_REARM]
        # Still held, and protected by exactly one stop for exactly the holding.
        (stop,) = fake.live_sells("XOM")
        assert (stop.order_type, stop.stop_price, stop.qty) == ("stop", 90.0, 10.0)
        assert fake.positions["XOM"].qty == 10.0

    def test_the_close_log_names_the_order_and_its_stamp(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _aged_xom()

        _run(monkeypatch, fake, "--submit", "--db-url", db_url)

        (line,) = [r.getMessage() for r in caplog.records if "closed on age" in r.getMessage()]
        (exit_order,) = [o for o in fake.created if o.client_order_id == XOM_EXIT_ID]
        assert exit_order.id in line
        assert XOM_EXIT_ID in line
        assert "released stop-xom" in line
        assert "flatten" not in line


class TestAFailedTimeExitIsCoveredInTheSamePass:
    """A time exit that does not close must not leave its name uncovered.

    The plan is made once, before the pass, and a name planned for a time exit
    gets no back-fill in it. Whatever the close leaves behind (a refusal, a
    re-arm that could not cover everything, a crash between release and sell)
    is re-read from the broker after the pass and back-filled there, or else it
    stays naked for this run and the next picks it for a time exit again.
    """

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tradingagents_us.execution.protected_close.time.sleep", lambda _: None)

    def _covered_once(self, fake: FakeBroker, qty: float = 10.0) -> None:
        """Held, and every held share under exactly one live stop, nothing else."""
        assert fake.positions["XOM"].qty == qty
        live = fake.live_sells("XOM")
        assert [o for o in live if o.order_type != "stop"] == []
        assert sum(o.qty - o.filled_qty for o in live) == qty

    def test_a_partly_naked_name_whose_exit_is_refused_ends_fully_covered(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The GOOGL shape: a stop over 3 of 10 shares, the other 7 naked. The
        # refused exit re-arms the 3; the 7 used to stay naked all run.
        fake = FakeBroker(
            positions=[_position("XOM", 100.5)],
            orders=[_stop("stop-xom", "XOM", 90.0, qty=3.0)],
            fills=[_buy("XOM")],
            refuse_sell={"XOM"},
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1, "the exit still failed, and says so"
        stops = [(w[1]["qty"], w[1]["stop_price"]) for w in fake.writes
                 if w[0] == "submit_order" and w[1]["order_type"] == "stop"]
        # The back-fill trails the last close, 100, not the 100.50 mark above it.
        assert stops == [(3.0, 90.0), (7.0, 94.0)]
        self._covered_once(fake)

    def test_a_lot_left_naked_by_a_crash_mid_release_is_covered_when_its_exit_fails(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A previous pass cancelled the stop and died before the sell. This
        # pass finds nothing to release, the exit is refused, and nothing was
        # re-armed because nothing was released.
        dead = dataclasses.replace(_stop("stop-xom", "XOM", 90.0), status="canceled")
        fake = FakeBroker(
            positions=[_position("XOM", 100.5)], orders=[dead], fills=[_buy("XOM")],
            refuse_sell={"XOM"},
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        self._covered_once(fake)

    @pytest.mark.parametrize("status", ["done_for_day", "calculated"])
    def test_an_exit_whose_day_is_over_does_not_hold_off_the_re_cover(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, status: str
    ) -> None:
        # Yesterday's exit sold 4 of 10 at the open, and the session left the
        # rest of that day order in an end-of-day state; tonight's exit for
        # the 6 left is refused. The dead exit sells nothing more, so it is no
        # seller to stand aside for: the 6 shares get their stop tonight.
        stamp = derive_exit_client_order_id("XOM", TODAY - timedelta(days=1), "time")
        dead = dataclasses.replace(
            _stop("exit-y", "XOM", 0.0), client_order_id=stamp, order_type="market",
            stop_price=None, status=status, filled_qty=4.0,
            submitted_at=datetime.now(UTC) - timedelta(days=1),
        )
        fake = FakeBroker(
            positions=[_position("XOM", 100.5, qty=6.0)], orders=[dead], fills=[_buy("XOM")],
            refuse_sell={"XOM"},
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1, "the miss and the refused exit page; nothing is left naked"
        stops = [(w[1]["qty"], w[1]["stop_price"]) for w in fake.writes
                 if w[0] == "submit_order" and w[1]["order_type"] == "stop"]
        assert stops == [(6.0, 94.0)]
        self._covered_once(fake, 6.0)

    def test_a_stop_still_cancelling_is_re_read_and_left_alone(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The stop's cancel never landed. It may still fill, so a back-fill
        # beside it is a second stop on the same shares: the re-read must see
        # it as ambiguous and place nothing.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _aged_xom(cancel_stuck={"stop-xom"})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        # Not the 1 of a routine refused exit: when that cancel lands the lot has
        # no stop and no exit, and nothing else in the run will notice.
        assert rc == 3
        assert fake.writes == [("cancel_order", "stop-xom")]
        assert "re-cover: time exits left XOM open" in caplog.text
        assert "XOM    SKIP  re-cover: a stop sell in pending_cancel still stands" in caplog.text

    def test_a_stop_whose_fill_is_on_its_way_is_not_back_filled_beside(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # `stopped`: the stop triggered and its fill is guaranteed, not booked.
        # The close refuses to act beside it, and coverage counts it as gone, so
        # the shares read naked. A back-fill there is a second seller. Nothing
        # working covers the lot, so the close pages rather than reading as a
        # routine failure.
        caplog.set_level(logging.INFO, logger="manage_positions")
        stopped = dataclasses.replace(_stop("stop-xom", "XOM", 90.0), status="stopped")
        fake = FakeBroker(positions=[_position("XOM", 100.5)], orders=[stopped],
                          fills=[_buy("XOM")])

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 3
        assert fake.writes == []
        assert "XOM    SKIP  re-cover: a stop sell in stopped still stands" in caplog.text

    def test_a_second_bracket_refusing_its_cancel_leaves_every_share_under_a_stop(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 20 XOM bought as two brackets. Cancelling TP-A takes SL-A with it as
        # an OCO pair; TP-B then refuses its cancel. SL-A was never put back,
        # the close said unchanged, and the re-cover left the name alone while
        # TP-B stands: 10 shares held with no stop, and nothing paged.
        tp = dict(side="sell", filled_qty=0.0, order_type="limit", status="new",
                  submitted_at=datetime.now(UTC), filled_avg_price=None, limit_price=120.0)
        fake = FakeBroker(
            positions=[_position("XOM", 100.5, qty=20.0)],
            orders=[
                dataclasses.replace(_stop("sl-a", "XOM", 90.0), status="held"),
                dataclasses.replace(_stop("sl-b", "XOM", 88.0), status="held"),
                Order(id="tp-a", client_order_id="coid-tp-a", symbol="XOM", qty=10.0, **tp),
                Order(id="tp-b", client_order_id="coid-tp-b", symbol="XOM", qty=10.0, **tp),
            ],
            fills=[_buy("XOM", qty=20.0)],
            oco={"tp-a": "sl-a", "tp-b": "sl-b"},
            cancel_refused={"tp-b"},
        )

        _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert fake.positions["XOM"].qty == 20.0
        stops = [o for o in fake.live_sells("XOM") if o.order_type == "stop"]
        assert sorted((o.qty, o.stop_price) for o in stops) == [(10.0, 88.0), (10.0, 90.0)]

    def test_a_take_profit_beside_its_own_stop_does_not_hold_off_the_re_cover(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # GOOGL's shape: a stop over 7 of 10 shares and three one-share
        # brackets, after a bad night. The first bracket went (take-profit and
        # stop cancelled) and its stop was never put back; the second's stop
        # stands `held` with its take-profit gone, and covers its share. The
        # third's take-profit works beside its own stop and refuses its
        # cancel, so the close sells nothing and leaves the lot as covered as
        # it was. Counted as a sell still standing, that take-profit held the
        # re-cover off, and the first bracket's share had no stop on any run
        # after.
        caplog.set_level(logging.INFO, logger="manage_positions")

        def leg(oid: str, kind: str, status: str) -> Order:
            if kind == "stop":
                return dataclasses.replace(_stop(oid, "XOM", 90.0, qty=1.0), status=status)
            return Order(
                id=oid, client_order_id=f"coid-{oid}", symbol="XOM", side="sell", qty=1.0,
                filled_qty=0.0, order_type="limit", status=status,
                submitted_at=datetime.now(UTC), filled_avg_price=None, limit_price=120.0,
            )

        fake = FakeBroker(
            positions=[_position("XOM", 100.5)],
            orders=[
                _stop("stop-s", "XOM", 90.0, qty=7.0),
                leg("tp-1", "limit", "canceled"), leg("sl-1", "stop", "canceled"),
                leg("tp-2", "limit", "canceled"), leg("sl-2", "stop", "held"),
                leg("tp-3", "limit", "new"), leg("sl-3", "stop", "held"),
            ],
            fills=[_buy("XOM")],
            oco={"tp-1": "sl-1", "tp-2": "sl-2", "tp-3": "sl-3"},
            cancel_refused={"tp-3"},
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1, "the close failed, and left no share uncovered that a stop covered"
        assert "SKIP  re-cover" not in caplog.text
        stops = [(w[1]["qty"], w[1]["stop_price"]) for w in fake.writes
                 if w[0] == "submit_order" and w[1]["order_type"] == "stop"]
        assert stops == [(1.0, 94.0)]
        assert sum(o.qty for o in fake.live_sells("XOM") if o.order_type == "stop") == 10.0
        assert fake._reserved("XOM") == 10.0

    def test_an_exit_that_fills_between_two_reads_gets_no_stop_beside_it(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The exit POST was accepted but its reply was lost, and the stamp
        # lookups time out. The listing shows it working, so nothing is
        # re-armed beside it, and it fills between that read and the verdict's
        # (a fill only regular hours allow). Read holding first, orders second,
        # and the lot would read as held with nothing over it: a stop on a
        # flat book, which a margin account takes as a short-sale stop. The
        # verdict reads the fill instead, and the run is the close it is.
        fake = _FillsAfterFirstReadOnceSold(
            positions=[_position("XOM", 100.5)],
            orders=[_stop("stop-xom", "XOM", 90.0)],
            fills=[_buy("XOM")],
            lose_sell_reply={"XOM"},
            exit_status="accepted",
            lookup_fails_after_sell=True,
            allow_short=True,
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert fake.fired, "the exit has to fill between the reads for this to test anything"
        assert "XOM" not in fake.positions
        assert fake.live_sells("XOM") == [], "a sell stop on a flat book is a short"

    @pytest.mark.parametrize("lands_after", range(1, 31))
    def test_an_unverified_exit_landing_at_any_point_of_the_run_leaves_one_seller(
        self, lands_after: int, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The exit POST lost its reply and the stamp lookups time out, so the
        # close never finds it by its stamp. It lands somewhere in the rest of
        # the run (the lookups, the re-arm, the verdict, the re-cover), or
        # after it, queued for the open as Alpaca does after the close. The
        # broker's reservation lets the exit or a stop stand, never both, and
        # every end state is one the run could read.
        fake = _aged_xom(
            sell_in_flight={"XOM": lands_after}, lookup_fails_after_sell=True,
            exit_status="accepted",
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)
        exiting = [o for o in fake.live_sells("XOM") if o.order_type == "market"]
        fake.land_in_flight()

        live = fake.live_sells("XOM")
        assert fake.positions["XOM"].qty == 10.0
        if exiting:
            assert [o.order_type for o in live] == ["market"], "the exit is the only seller"
            assert rc == 0
        else:
            self._covered_once(fake)
            assert rc == 1, "the exit failed, and the lot is as protected as before"

    @pytest.mark.parametrize("variant", ["naked-lot", "re-arm-refused"])
    def test_an_exit_landing_on_the_back_fill_after_a_close_that_re_armed_nothing(
        self, variant: str, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The exit POST lost its reply and was never found, and the close put
        # no stop back: the lot was naked, or the broker refused the re-arm.
        # Nothing reserves the shares, so the exit can still land, and the
        # close hands its stamp on. The re-cover's back-fill goes through
        # cover_beside_exit, and the exit lands just before it, queued for the
        # open: the back-fill is refused beside it, and the verdict reads the
        # exit, so the doubt the close paged on is settled.
        fake = _LandsOnTheBackFill(
            positions=[_position("XOM", 100.5)],
            orders=[] if variant == "naked-lot" else [_stop("stop-xom", "XOM", 90.0)],
            fills=[_buy("XOM")],
            sell_in_flight={"XOM": 10**6},
            exit_status="accepted",
        )
        fake.refuse_rearms = variant == "re-arm-refused"

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        exits = [o for o in fake.created if o.client_order_id == XOM_EXIT_ID]
        assert [o.status for o in exits] == ["accepted"], "the exit has to land for this to test"
        (working,) = fake.live_sells("XOM")
        assert working.client_order_id == XOM_EXIT_ID, "the exit is the only seller"
        assert rc == 1, "the close failed; what it left is settled, so this is no page"

    def test_a_holding_unreadable_after_the_exit_puts_the_stop_back_unchecked(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The same lost exit, with the holding unreadable for the fresh read.
        # The released stop goes back as it was (it was the lot's only seller,
        # and nothing fills while the market is shut), and once it stands the
        # exit cannot land: the broker refuses it beside the stop.
        fake = _LandsOnTheBackFill(
            positions=[_position("XOM", 100.5)], orders=[_stop("stop-xom", "XOM", 90.0)],
            fills=[_buy("XOM")], sell_in_flight={"XOM": 10**6}, exit_status="accepted",
        )
        fake.unreadable = 3

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)
        fake.land_in_flight()

        self._covered_once(fake)
        assert rc == 1

    def test_without_backfill_the_re_cover_reports_and_places_nothing(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        dead = dataclasses.replace(_stop("stop-xom", "XOM", 90.0), status="canceled")
        fake = FakeBroker(
            positions=[_position("XOM", 100.5)], orders=[dead], fills=[_buy("XOM")],
            refuse_sell={"XOM"},
        )

        _run(monkeypatch, fake, "--submit", "--db-url", db_url)

        assert [w for w in fake.writes
                if isinstance(w[1], dict) and w[1]["order_type"] == "stop"] == []
        assert "XOM    SKIP  re-cover: no_stop_order" in caplog.text


class TestAReArmBesideALandedExitThroughThePass:
    """The exit's reply is lost, and it lands on the re-arm's POST.

    After the close it queues for the open and reserves the shares, so the
    re-armed stop is refused beside it, whether the broker answers the stop's
    POST or loses that reply too. The pass must end with the exit as the only
    seller, and say the exit went through.
    """

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tradingagents_us.execution.protected_close.time.sleep", lambda _: None)

    @pytest.mark.parametrize(
        "fault",
        [
            {"stop_reply_error": httpx.ReadTimeout("reply lost")},
            {"stop_reply_error": AlpacaRequestError("POST", "/orders", 504, "gateway timeout")},
            {"throttle_own_cancels": True},
        ],
        ids=["stop-reply-timeout", "stop-reply-504", "take-back-429"],
    )
    def test_the_exit_is_the_only_seller(
        self, fault: dict, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = _aged_xom(sell_in_flight={"XOM": 9}, exit_status="accepted", **fault)

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)
        fake.land_in_flight()

        (working,) = fake.live_sells("XOM")
        assert working.client_order_id == XOM_EXIT_ID and working.qty == 10.0
        assert rc == 0


class _FillsAfterFirstReadOnceSold(FakeBroker):
    """The working exit fills just after the first book read that follows its
    POST: between that read and the next one, as a regular-hours fill would."""

    fired = False

    def _after_read(self) -> None:
        if self.fired or not self._sold:
            return
        for oid, order in list(self.orders.items()):
            if order.client_order_id == XOM_EXIT_ID and order.status == "accepted":
                self.fired = True
                self.orders[oid] = dataclasses.replace(
                    order, status="filled", filled_qty=order.qty, filled_avg_price=100.5
                )
                self._sell_shares("XOM", order.qty)

    def list_positions(self) -> list[Position]:
        out = super().list_positions()
        self._after_read()
        return out

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False):
        out = super().list_orders(status, limit, nested)
        self._after_read()
        return out


class _LandsOnTheBackFill(FakeBroker):
    """An exit in flight lands just before the first stop that is not a re-arm.

    That is the re-cover's back-fill. `refuse_rearms` turns every re-armed
    stop down with a 4xx, and `unreadable` fails that many holding reads once
    the exit has been sent.
    """

    refuse_rearms = False
    unreadable = 0

    def submit_order(self, **kw) -> Order:
        if kw.get("order_type") == "stop":
            if "-arm-" not in (kw.get("client_order_id") or ""):
                self.land_in_flight()
            elif self.refuse_rearms:
                self._record("submit_order", kw)
                raise AlpacaRequestError("POST", "/orders", 422, "stop price must be below market")
        return super().submit_order(**kw)

    def get_position(self, symbol: str) -> Position | None:
        if self._sold and self.unreadable:
            self.unreadable -= 1
            self._record("get_position", symbol)
            raise httpx.ConnectError("positions endpoint unreachable")
        return super().get_position(symbol)


class _StopFillsAfterFirstRead(FakeBroker):
    """A stop fires just after the pass's first book read returns."""

    fired = False

    def __init__(self, *a, stop_id: str, **kw) -> None:
        super().__init__(*a, **kw)
        self.stop_id = stop_id

    def _after_read(self) -> None:
        if self.fired:
            return
        self.fired = True
        stop = self.orders[self.stop_id]
        self.orders[self.stop_id] = dataclasses.replace(
            stop, status="filled", filled_qty=stop.qty, filled_avg_price=stop.stop_price
        )
        self._sell_shares(stop.symbol, stop.qty)

    def list_positions(self) -> list[Position]:
        out = super().list_positions()
        self._after_read()
        return out

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False):
        out = super().list_orders(status, limit, nested)
        self._after_read()
        return out


class TestThePassReadsOrdersBeforeTheHolding:
    """A sell that fills between the pass's two reads must show as fewer shares.

    Read the holding first and the orders second, and a stop that fires in
    between reads as gone while its shares still read as held: the lot looks
    naked, and the back-fill puts a stop on a flat book. A margin account takes
    that as a short-sale stop.
    """

    def test_a_stop_that_fires_between_the_reads_is_not_back_filled(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Up 20% (no time exit), fully covered by one stop that fires just
        # after the pass's first read, in a regular-hours run.
        fake = _StopFillsAfterFirstRead(
            positions=[_position("AAPL", 120.0)],
            orders=[_stop("stop-aapl", "AAPL", 110.0)],
            fills=[_buy("AAPL")],
            allow_short=True,
            stop_id="stop-aapl",
        )

        _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert fake.fired
        assert "AAPL" not in fake.positions
        assert fake.live_sells("AAPL") == [], "a sell stop on a flat book is a short"

    def test_a_lot_sold_while_an_earlier_exit_settles_gets_no_back_fill(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The plan is read before the pass writes anything, and a time exit
        # (release, confirm, sell, look up) can take a minute. In regular
        # hours the naked MSFT is closed by hand while XOM's stop is being
        # released: the back-fill planned off the old read went to the flat
        # book all the same, a short-sale stop on a margin account, rc 0.
        fake = _SoldWhileXomIsReleased(
            positions=[_position("XOM", 100.5), _position("MSFT", 200.0, avg=150.0)],
            orders=[_stop("stop-xom", "XOM", 90.0)],
            fills=[_buy("XOM"), _buy("MSFT")],
            allow_short=True,
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert "MSFT" not in fake.positions
        assert fake.live_sells("MSFT") == [], "a sell stop on a flat book is a short"
        assert rc == 0


class TestAListingAfterATimeExitHasCaughtUpWithIt:
    """The re-cover's listings wait for one that shows what the pass's closes read by id.

    Right after a close, Alpaca's listing may still show the stop it released
    and read cancelled, or not yet the one it put back: read as it comes, the
    re-cover finds the lot covered by a stop that is gone, or naked under one
    that stands, and back-fills off that.
    """

    #: XOM's time exit released its stop and read it cancelled.
    CLOSE = CloseOutcome("XOM", "unchanged", "exit refused", released=("stop-xom",))

    def _released(self, monkeypatch: pytest.MonkeyPatch, behind: int) -> list[float]:
        sleeps: list[float] = []
        monkeypatch.setattr(mp.time, "sleep", sleeps.append)
        self.fake = FakeBroker(
            positions=[_position("XOM", 100.5)], orders=[_stop("stop-xom", "XOM", 90.0)],
            lists_behind=behind,
        )
        self.fake.cancel_order("stop-xom")
        return sleeps

    def test_a_listing_still_showing_the_released_stop_is_read_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps = self._released(monkeypatch, behind=2)

        *_, sells = mp._read_book(self.fake, [self.CLOSE])

        assert [o.status for o in sells] == ["canceled"]
        assert sleeps == list(LISTING_CATCH_UP_DELAYS_S[:2])

    def test_one_that_never_catches_up_is_an_unreadable_book(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps = self._released(monkeypatch, behind=10**6)

        with pytest.raises(RuntimeError, match="stop-xom listed new"):
            mp._read_book(self.fake, [self.CLOSE])
        assert sleeps == list(LISTING_CATCH_UP_DELAYS_S)

    def test_a_stop_the_close_put_back_is_waited_for_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The listing has caught up with the cancel, not with the re-arm: all
        # it is behind on is the stop the close put back, and off it the lot
        # reads naked under that stop.
        sleeps = self._released(monkeypatch, behind=1)
        self.fake.list_orders()
        rearm = self.fake.submit_order(
            symbol="XOM", qty=10.0, side="sell", order_type="stop", time_in_force="gtc",
            stop_price=90.0, client_order_id="re-arm",
        )
        close = dataclasses.replace(self.CLOSE, rearmed=(rearm.id,))

        assert mp._naked_now(self.fake, "XOM", [close]) == 0.0
        assert sleeps == [LISTING_CATCH_UP_DELAYS_S[0]]

    def test_an_exit_its_close_read_dead_is_waited_for_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The listing has caught up with the released stop, not with the
        # exit the close read cancelled by its id: listed accepted, the lot
        # reads as on its way out, and the re-cover skips it.
        sleeps = self._released(monkeypatch, behind=1)
        self.fake.exit_status = "accepted"
        exit_ = self.fake.submit_order(
            symbol="XOM", qty=10.0, side="sell", order_type="market", time_in_force="day",
            client_order_id=XOM_EXIT_ID,
        )
        self.fake.list_orders()
        self.fake.cancel_order(exit_.id)
        close = dataclasses.replace(self.CLOSE, exit_order_id=exit_.id, dead=(exit_.id,))

        *_, sells = mp._read_book(self.fake, [close])

        assert {o.id: o.status for o in sells}[exit_.id] == "canceled"
        assert sleeps == [LISTING_CATCH_UP_DELAYS_S[0]]

    def test_a_back_fill_beside_an_exit_waits_on_its_own_lot_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # XOM's exit was never found, and its back-fill is placed beside it.
        # AAPL's close placed an exit too, which XOM's listing never shows.
        sleeps = self._released(monkeypatch, behind=2)
        aapl = CloseOutcome("AAPL", "exit_submitted", "", exit_order_id="market-AAPL-9")
        uncovered = ["XOM unknown"]

        failed = mp._cover_beside_exit(
            self.fake, PlaceStop("XOM", 10.0, 90.0, 2.0), XOM_EXIT_ID, uncovered, 0.0,
            [aapl, self.CLOSE],
        )

        assert (failed, uncovered) == (0, []), "the back-fill stands: the doubt is settled"
        assert sleeps == list(LISTING_CATCH_UP_DELAYS_S[:2])

    def test_a_refused_back_fill_beside_an_exit_is_no_cover_from_the_released_stop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # XOM's exit was never found and its back-fill is refused: only the
        # close's read of stop-xom, handed on, says a listing still showing
        # that stop is old. Taken as it comes, the lot reads covered.
        sleeps = self._released(monkeypatch, behind=2)
        self.fake.refuse_stop = {"XOM"}
        uncovered = ["XOM unknown"]

        failed = mp._cover_beside_exit(
            self.fake, PlaceStop("XOM", 10.0, 90.0, 2.0), XOM_EXIT_ID, uncovered, 0.0,
            [self.CLOSE],
        )

        assert failed == 1
        assert uncovered == ["XOM unknown", f"XOM naked beside exit {XOM_EXIT_ID}"]
        assert sleeps == list(LISTING_CATCH_UP_DELAYS_S[:2])

    def test_a_back_fill_beside_an_exit_is_handed_the_exit_its_close_holds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The lot's closes went on as a generator, spent on the released
        # stops before the orders placed were read off it: cover_beside_exit
        # never waited for a listing that shows the exit or a re-arm.
        handed: dict = {}
        monkeypatch.setattr(
            mp, "cover_beside_exit",
            lambda *a, **kw: handed.update(kw) or CloseOutcome("XOM", "unchanged", ""),
        )
        close = dataclasses.replace(self.CLOSE, exit_order_id="market-XOM-1", rearmed=("re",))

        mp._cover_beside_exit(
            FakeBroker([]), PlaceStop("XOM", 10.0, 90.0, 2.0), XOM_EXIT_ID, [], 0.0, [close]
        )

        assert (handed["released"], handed["rearmed"]) == ({"stop-xom"}, {"market-XOM-1", "re"})

    def test_a_lot_s_lagging_listing_holds_up_no_other_lot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # XOM's listing never catches up. MSFT, naked, had no close in the
        # pass, and AAPL's re-cover finds it sold: neither waits on XOM.
        sleeps = self._released(monkeypatch, behind=10**6)
        self.fake.positions["MSFT"] = _position("MSFT", 200.0, avg=150.0)
        uncovered: list[str] = []

        failed = mp._execute(
            self.fake, [PlaceStop("MSFT", 10.0, 180.0, 2.0)], TODAY, closes=[self.CLOSE]
        )
        failed += mp._recover_unclosed(
            self.fake, None, {"AAPL"}, {}, TODAY, None, uncovered, closes=[self.CLOSE]
        )

        assert (failed, uncovered, sleeps) == (0, [], [])
        assert [o.qty for o in self.fake.live_sells("MSFT")] == [10.0]

    def test_a_re_cover_the_listing_holds_up_names_its_lots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._released(monkeypatch, behind=10**6)
        uncovered: list[str] = []

        mp._recover_unclosed(
            self.fake, None, {"XOM"}, {}, TODAY, None, uncovered, closes=[self.CLOSE]
        )

        assert uncovered[0].startswith("re-cover could not read the book for XOM: ")

    def test_with_no_close_before_it_a_listing_is_taken_as_it_comes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps = self._released(monkeypatch, behind=2)

        *_, sells = mp._read_book(self.fake)

        assert [o.status for o in sells] == ["new"] and sleeps == []


class _RefusesReArms(FakeBroker):
    """Every stop a close puts back is turned down with a 4xx; a back-fill is placed."""

    def submit_order(self, **kw) -> Order:
        if "-arm-" in (kw.get("client_order_id") or ""):
            self._record("submit_order", kw)
            raise AlpacaRequestError("POST", "/orders", 422, "stop price must be below market")
        return super().submit_order(**kw)


class TestTheReCoverWaitsForAListingThatCaughtUpWithTheClose:
    """Through main(): the re-cover after a failed close reads a listing no older than it."""

    def test_a_lot_whose_exit_and_re_arm_were_refused_is_back_filled(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The listing is five listings behind each write: through all of the
        # close's own reads, which give up, it still shows the stop the close
        # released and read cancelled. Taken as it comes, the re-cover's first
        # listing covers the lot with that stop, and the lot spends the night
        # with none.
        monkeypatch.setattr(mp.time, "sleep", lambda _: None)
        fake = _RefusesReArms(
            positions=[_position("XOM", 100.5)], orders=[_stop("stop-xom", "XOM", 90.0)],
            fills=[_buy("XOM")], refuse_sell={"XOM"}, lists_behind=5,
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == mp.EXIT_UNCOVERED, "the close could not read its end state"
        assert [(o.qty, o.stop_price) for o in fake.live_sells("XOM")] == [(10.0, 94.0)]


class _ListsAStopWorkingForGood(_RefusesReArms):
    """A listing that never shows `stop-T0` gone, whatever get_order reads."""

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False):
        listed = super().list_orders(status, limit, nested)
        return [dataclasses.replace(o, status="new") if o.id == "stop-T0" else o for o in listed]


class TestALotsListingThatNeverCatchesUpThroughThePass:
    """Two time exits fail; the listing never catches up with one of them."""

    def test_the_other_lot_is_still_back_filled(
        self, aged_book_db: str, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # T0's and T1's exits and re-arms are refused, and both lots are left
        # with no stop. The listing has caught up with T1's close, never with
        # T0's: T1 is back-filled off it, as main does, and T0 alone is a book
        # the re-cover could not read.
        monkeypatch.setattr(mp.time, "sleep", lambda _: None)
        fake = _ListsAStopWorkingForGood(
            positions=[_position(s, 100.5) for s in AGED],
            orders=[_stop(f"stop-{s}", s, 90.0) for s in AGED],
            fills=[_buy(s) for s in AGED],
            refuse_sell={"T0", "T1"},
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", aged_book_db)

        assert rc == mp.EXIT_UNCOVERED, "T0 is still in doubt"
        assert [(o.qty, o.stop_price) for o in fake.live_sells("T1")] == [(10.0, 94.0)]
        assert fake.live_sells("T0") == []
        assert "re-cover could not read the book for T0: listing behind: stop-T0 listed new" in (
            caplog.text
        )


class _ListsStopT1AfterT0sBackFill(_RefusesReArms):
    """The first listing after T0's back-fill is the book from before stop-T1's cancel.

    Once, as Alpaca's listing lags; get_order, the client-id lookup and the
    holding are current.
    """

    before_t1: dict[str, Order] | None = None

    def cancel_order(self, order_id: str) -> dict:
        if order_id == "stop-T1" and self.before_t1 is None:
            self.before_t1 = dict(self.orders)
        return super().cancel_order(order_id)

    def submit_order(self, **kw) -> Order:
        order = super().submit_order(**kw)
        if (kw["symbol"], kw["order_type"]) == ("T0", "stop") and self.before_t1 is not None:
            self.behind, self._behind_left = self.before_t1, 1
        return order


class TestEachLotsBackFillWaitsOnItsOwnClose:
    def test_a_listing_behind_another_lot_s_close_is_read_again(
        self, aged_book_db: str, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # T0's and T1's exits and re-arms are refused. T1's back-fill is sized
        # off the listing right after T0's, two changes behind (stop-T1's
        # cancel, T0's back-fill; every stop is at its trail, so nothing is
        # ratcheted between): it still shows stop-T1 working. Taken as it
        # comes, T1 reads covered, its back-fill is skipped, and the lot
        # spends the night with no stop.
        caplog.set_level(logging.INFO, logger="manage_positions")
        sleeps: list[float] = []
        monkeypatch.setattr(mp.time, "sleep", sleeps.append)
        fake = _ListsStopT1AfterT0sBackFill(
            positions=[_position(s, 100.5) for s in AGED],
            orders=[_stop(f"stop-{s}", s, 94.0) for s in AGED],
            fills=[_buy(s) for s in AGED],
            refuse_sell={"T0", "T1"},
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", aged_book_db)

        assert rc == mp.EXIT_UNCOVERED, "the closes paged"
        assert "replace_order" not in {w[0] for w in fake.writes}
        for lot in ("T0", "T1"):
            assert [(o.qty, o.stop_price) for o in fake.live_sells(lot)] == [(10.0, 94.0)], lot
        assert "SKIP  back-fill" not in caplog.text
        assert sleeps[-1] == LISTING_CATCH_UP_DELAYS_S[0], "one catch-up wait, for T1"


class _ListsTheExitLate(FakeBroker):
    """After the exit's POST: four listings still show the stop working and no exit, six
    more the stop cancelled and still no exit. get_order and the holding are current."""

    late = 0

    def list_orders(self, status: str = "open", limit: int = 50, nested: bool = False):
        listed = super().list_orders(status, limit, nested)
        if not self._sold or self.late >= 10:
            return listed
        self.late += 1
        listed = [o for o in listed if o.client_order_id != XOM_EXIT_ID]
        if self.late <= 4:
            listed = [dataclasses.replace(o, status="new") if o.id == "stop-xom" else o
                      for o in listed]
        return listed


class TestAnExitTheListingNeverShowedThroughThePass:
    def test_the_re_cover_waits_for_it_and_places_nothing_beside_it(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The exit is accepted and holds every share. The close's listings
        # never catch up, and it pages `unknown`. The re-cover's first listing
        # shows the stop gone and no exit yet: it is older than the exit's
        # POST reply, and off it the lot read naked, got a back-fill beside
        # its exit (refused, held_for_orders 10), and was paged as naked
        # beside an exit "never found".
        caplog.set_level(logging.INFO, logger="manage_positions")
        monkeypatch.setattr(mp.time, "sleep", lambda _: None)
        fake = _ListsTheExitLate(
            positions=[_position("XOM", 100.5)], orders=[_stop("stop-xom", "XOM", 90.0)],
            fills=[_buy("XOM")], exit_status="accepted",
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        (exit_order,) = fake.created
        assert rc == mp.EXIT_UNCOVERED
        assert fake.writes == [("cancel_order", "stop-xom"), XOM_EXIT], "nothing beside the exit"
        assert f"order {exit_order.id} ({XOM_EXIT_ID})" in caplog.text
        assert "never found" not in caplog.text


class _SoldWhileXomIsReleased(FakeBroker):
    """Another seller closes MSFT the moment the pass cancels XOM's stop."""

    def cancel_order(self, order_id: str) -> dict:
        out = super().cancel_order(order_id)
        if order_id == "stop-xom" and "MSFT" in self.positions:
            self._new_order("MSFT", self.positions["MSFT"].qty, "market", "by-hand", "filled")
        return out


class _PositionsUnreadableAfterFirstRead(FakeBroker):
    """The positions endpoint answers the pass's first read, then goes down."""

    def list_positions(self) -> list[Position]:
        if any(c[0] == "list_positions" for c in self.calls):
            self._record("list_positions")
            raise RuntimeError("positions endpoint down")
        return super().list_positions()


class TestAnUncoveredOutcomeHasItsOwnExitCode:
    """rc 3 when a time exit may have left shares with no stop; 1 stays routine.

    A refused exit whose stop went back is a failure, and the lot is protected.
    An `unknown` or `naked` close is not that: a cancel that lands after the run
    strips the stop with no exit behind it, and the stop coverage check at the
    end of the run still sees the stop standing. daily_run.sh pages on this code.
    """

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tradingagents_us.execution.protected_close.time.sleep", lambda _: None)

    def test_a_cancel_never_seen_to_land(self, db_url: str, monkeypatch) -> None:
        fake = _aged_xom(slow_cancel={"stop-xom": ("new", 10**6)})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 3

    def test_a_stop_that_could_not_be_put_back(self, db_url: str, monkeypatch) -> None:
        fake = _aged_xom(refuse_sell={"XOM"}, refuse_stop={"XOM"})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 3

    def test_a_re_cover_that_cannot_read_the_book(self, db_url: str, monkeypatch) -> None:
        # A crash between an earlier pass's release and its sell left the lot
        # with no stop; this exit is refused, and the re-cover cannot look.
        dead = dataclasses.replace(_stop("stop-xom", "XOM", 90.0), status="canceled")
        fake = _PositionsUnreadableAfterFirstRead(
            positions=[_position("XOM", 100.5)], orders=[dead], fills=[_buy("XOM")],
            refuse_sell={"XOM"},
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 3

    def test_a_re_arm_landing_late_beside_a_queued_exit_is_refused(
        self, db_url: str, monkeypatch
    ) -> None:
        # The exit and its re-arm both lose their reply; the exit lands during
        # the re-arm's lookups, queued for the open, and the re-arm lands after
        # the run, where the exit's reservation refuses it. A close, rc 0.
        fake = _aged_xom(
            sell_in_flight={"XOM": 11}, stop_in_flight={"XOM": 9}, exit_status="accepted"
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)
        fake.land_in_flight()

        assert rc == 0
        (working,) = fake.live_sells("XOM")
        assert working.client_order_id == XOM_EXIT_ID

    def test_a_stop_whose_fill_is_on_its_way(self, db_url: str, monkeypatch) -> None:
        stopped = dataclasses.replace(_stop("stop-xom", "XOM", 90.0), status="stopped")
        fake = FakeBroker(positions=[_position("XOM", 100.5)], orders=[stopped],
                          fills=[_buy("XOM")])

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 3

    def test_a_pass_run_in_the_session_defers_its_time_exit(
        self, db_url: str, monkeypatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Nothing sent for the close, the lot keeps its stop, and the pass
        # fails quietly (rc 1) so a person sees the exit did not run.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _aged_xom(market_open=True)

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        assert fake.writes == []
        assert "deferred" in caplog.text and "market is open" in caplog.text

    def test_a_refused_exit_whose_stop_is_back_stays_an_ordinary_failure(
        self, db_url: str, monkeypatch
    ) -> None:
        fake = _aged_xom(refuse_sell={"XOM"})

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1


class TestAnExitTheOpenRefusedIsNamed:
    """Yesterday's exit, queued for the open, was cancelled there: the lot sat naked.

    The next run finds it naked and sells it again, which closes the gap, but
    only this line says the gap was there, so the pass fails and pages.
    """

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tradingagents_us.execution.protected_close.time.sleep", lambda _: None)

    def _yesterdays_exit(self, status: str) -> Order:
        stamp = derive_exit_client_order_id("XOM", TODAY - timedelta(days=1), "time")
        return dataclasses.replace(
            _stop("exit-y", "XOM", 0.0), client_order_id=stamp, order_type="market",
            stop_price=None, status=status, submitted_at=datetime.now(UTC) - timedelta(days=1),
        )

    def test_the_lot_is_sold_again_and_the_run_pages(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = FakeBroker(
            positions=[_position("XOM", 100.5)], orders=[self._yesterdays_exit("canceled")],
            fills=[_buy("XOM")], exit_status="accepted",
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        assert [w for w in fake.writes if w[0] == "submit_order"] == [XOM_EXIT]
        assert "MISSED EXIT" in caplog.text and "canceled, 0 of 10 sold" in caplog.text

    def test_an_exit_that_sold_the_lot_is_no_miss(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        filled = dataclasses.replace(self._yesterdays_exit("filled"), filled_qty=10.0)
        fake = FakeBroker(
            positions=[_position("XOM", 100.5)], orders=[filled, _stop("stop-xom", "XOM", 90.0)],
            fills=[_buy("XOM")], exit_status="accepted",
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 0
        assert "MISSED EXIT" not in caplog.text

    @pytest.mark.parametrize(("status", "filled"), [("rejected", 0.0), ("expired", 4.0),
                                                     ("canceled", 0.0), ("done_for_day", 4.0),
                                                     ("calculated", 4.0)])
    def test_a_lot_no_longer_due_is_named_too(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        status: str, filled: float,
    ) -> None:
        # The session that refused the exit moved the lot out of the flat band,
        # here by an age window it no longer passes: no close is planned, so
        # only the back-fill runs, and the close never got to see the miss.
        caplog.set_level(logging.INFO, logger="manage_positions")
        held = 10.0 - filled
        yesterdays = dataclasses.replace(self._yesterdays_exit(status), filled_qty=filled)
        fake = FakeBroker(
            positions=[_position("XOM", 100.5, qty=held)], orders=[yesterdays],
            fills=[_buy("XOM")],
        )

        rc = _run(
            monkeypatch, fake, "--submit", "--backfill-stops", "--max-bars", "1000",
            "--db-url", db_url,
        )

        assert rc == 1
        (backfill,) = [w for w in fake.writes if w[0] == "submit_order"]
        assert backfill[1]["order_type"] == "stop" and backfill[1]["qty"] == held
        assert "MISSED EXIT" in caplog.text
        assert f"{status}, {filled:g} of 10 sold" in caplog.text

    @pytest.mark.parametrize("status", ["done_for_day", "calculated"])
    def test_a_lot_whose_exit_the_day_ended_half_sold_is_sold_again(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        status: str,
    ) -> None:
        # The open sold 4 of 10 and the session left the rest of the day
        # order in an end-of-day state. It sells nothing more, yet read as
        # "neither working nor gone" it blocked the close and the re-cover:
        # the 6 shares left had neither exit nor stop, every night.
        caplog.set_level(logging.INFO, logger="manage_positions")
        dead = dataclasses.replace(self._yesterdays_exit(status), filled_qty=4.0)
        fake = FakeBroker(
            positions=[_position("XOM", 100.5, qty=6.0)], orders=[dead], fills=[_buy("XOM")],
            exit_status="accepted",
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        assert [(w[1]["order_type"], w[1]["qty"]) for w in fake.writes
                if w[0] == "submit_order"] == [("market", 6.0)]
        assert "MISSED EXIT" in caplog.text and f"{status}, 4 of 10 sold" in caplog.text

    def test_a_miss_is_named_once(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The run after the miss back-filled the lot. The run after that finds
        # the dead exit still in the listing, with a stop placed since: old news.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = FakeBroker(
            positions=[_position("XOM", 100.5)],
            orders=[self._yesterdays_exit("rejected"), _stop("backfill-xom", "XOM", 96.0)],
            fills=[_buy("XOM")],
        )

        rc = _run(
            monkeypatch, fake, "--submit", "--backfill-stops", "--max-bars", "1000",
            "--db-url", db_url,
        )

        assert rc == 0
        assert "MISSED EXIT" not in caplog.text

    def test_a_lot_the_exit_budget_defers_is_named_too(
        self, aged_book_db: str, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # T7's exit died at the open, and today T0 and T1 spend the budget.
        caplog.set_level(logging.INFO, logger="manage_positions")
        stamp = derive_exit_client_order_id("T7", TODAY - timedelta(days=1), "time")
        dead = dataclasses.replace(
            _stop("exit-T7", "T7", 0.0), client_order_id=stamp, order_type="market",
            stop_price=None, status="rejected", submitted_at=datetime.now(UTC) - timedelta(days=1),
        )
        fake = FakeBroker(
            positions=[_position(s, 100.5) for s in AGED],
            orders=[dead, *(_stop(f"stop-{s}", s, 90.0) for s in AGED[:-1])],
            fills=[_buy(s) for s in AGED],
        )

        rc = _run(monkeypatch, fake, "--submit", "--db-url", aged_book_db)

        assert rc == 1
        assert "T7" not in {w[1]["symbol"] for w in fake.writes if w[0] == "submit_order"}
        (missed,) = [r.getMessage() for r in caplog.records if "MISSED EXIT" in r.getMessage()]
        assert missed.startswith("T7") and f"{stamp} rejected" in missed


class TestAnExitStampedWithTodaysDateTheOpenRefusedIsNamed:
    """A run past 00:00 UTC stamps the date tonight's 22:30 run has too.

    That exit queued for today's open, which refused it: the lot spent the
    session with neither stop nor exit. Read by its stamp's date it was
    tonight's own, so the close moved on to `-r2`, the back-fill covered the
    lot, the pass exited 0, and tomorrow the last exit was `-r2`: never named.
    Whether it met an open is the calendar's to say.
    """

    #: Tonight's run, and the 13:30 UTC open before it (the fake's calendar).
    NOW = datetime.combine(TODAY, time(22, 30), UTC)

    @pytest.fixture(autouse=True)
    def _no_waiting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tradingagents_us.execution.protected_close.time.sleep", lambda _: None)

    def _todays_exit(self, status: str, filled: float = 0.0) -> Order:
        # Sent at 01:00 UTC by the catch-up of yesterday's run.
        return dataclasses.replace(
            _stop("exit-t", "XOM", 0.0), client_order_id=XOM_EXIT_ID, order_type="market",
            stop_price=None, status=status, filled_qty=filled,
            submitted_at=datetime.combine(TODAY, time(1, 0), UTC),
        )

    @pytest.mark.parametrize(("status", "filled"), [("rejected", 0.0), ("canceled", 0.0),
                                                     ("expired", 4.0), ("done_for_day", 4.0),
                                                     ("calculated", 4.0)])
    def test_a_lot_still_due_is_sold_again_and_the_run_pages(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        status: str, filled: float,
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        held = 10.0 - filled
        fake = FakeBroker(
            positions=[_position("XOM", 100.5, qty=held)],
            orders=[self._todays_exit(status, filled)], fills=[_buy("XOM")],
            exit_status="accepted", now=self.NOW,
        )

        rc = _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        assert rc == 1
        sent = [w[1] for w in fake.writes if w[0] == "submit_order"]
        assert [(w["client_order_id"], w["qty"]) for w in sent] == [(f"{XOM_EXIT_ID}-r2", held)]
        named = f"{XOM_EXIT_ID} {status}, {filled:g} of 10 sold"
        (missed,) = [r.getMessage() for r in caplog.records if "MISSED EXIT" in r.getMessage()]
        assert missed.startswith("XOM") and named in missed
        (closed,) = [r.getMessage() for r in caplog.records if "closed on age" in r.getMessage()]
        assert named in closed

    @pytest.mark.parametrize("status", ["rejected", "canceled", "expired"])
    def test_a_lot_no_longer_due_is_back_filled_and_the_run_pages(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
        status: str,
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = FakeBroker(
            positions=[_position("XOM", 100.5)], orders=[self._todays_exit(status)],
            fills=[_buy("XOM")], now=self.NOW,
        )

        rc = _run(
            monkeypatch, fake, "--submit", "--backfill-stops", "--max-bars", "1000",
            "--db-url", db_url,
        )

        assert rc == 1
        (backfill,) = [w[1] for w in fake.writes if w[0] == "submit_order"]
        assert backfill["order_type"] == "stop" and backfill["qty"] == 10.0
        assert "MISSED EXIT" in caplog.text and f"{XOM_EXIT_ID} {status}" in caplog.text

    def test_one_that_died_before_the_open_is_no_miss(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Refused at 22:00, after the session: no open passed with the lot naked.
        caplog.set_level(logging.INFO, logger="manage_positions")
        dead = dataclasses.replace(
            self._todays_exit("rejected"), submitted_at=datetime.combine(TODAY, time(22), UTC)
        )
        fake = FakeBroker(
            positions=[_position("XOM", 100.5)], orders=[dead], fills=[_buy("XOM")],
            now=self.NOW,
        )

        rc = _run(
            monkeypatch, fake, "--submit", "--backfill-stops", "--max-bars", "1000",
            "--db-url", db_url,
        )

        assert rc == 0
        assert "MISSED EXIT" not in caplog.text


class TestTodaysExitsAreCountedByTheirStamp:
    def test_a_stop_re_armed_under_a_retried_stamp_spends_no_exit_budget(self) -> None:
        retried = f"{XOM_EXIT_ID}-r2"
        sells = frozenset({
            ("XOM", _rearm_id(retried, "stop-xom"), "new"),
            ("AAPL", derive_exit_client_order_id("AAPL", TODAY, "time") + "-r3", "accepted"),
            ("MSFT", derive_exit_client_order_id("MSFT", TODAY, "time"), "filled"),
            ("NVDA", derive_exit_client_order_id("NVDA", TODAY, "time"), "rejected"),
        })

        assert mp._exited_on(sells, TODAY) == {"AAPL", "MSFT"}

    def test_an_exit_queued_on_an_earlier_day_is_told_apart_from_todays(self) -> None:
        yesterday = TODAY - timedelta(days=1)
        sells = frozenset({
            ("XOM", derive_exit_client_order_id("XOM", yesterday, "time"), "accepted"),
            ("AAPL", derive_exit_client_order_id("AAPL", yesterday, "time") + "-r2", "new"),
            ("MSFT", derive_exit_client_order_id("MSFT", yesterday, "time"), "expired"),
            ("NVDA", derive_exit_client_order_id("NVDA", TODAY, "time"), "accepted"),
            ("AMD", _rearm_id(derive_exit_client_order_id("AMD", yesterday, "time"), "s"), "new"),
        })

        assert mp._exiting_before(sells, TODAY) == {"XOM", "AAPL"}
        assert mp._exited_on(sells, TODAY) == {"NVDA"}


class TestTimeExitAttributionThroughTheLedger:
    """What the ledger books a production time exit as, from the pass to reconcile.

    Follows the order the pass really produces into the reconcile step that
    stores the class, so it cannot be satisfied by the classifier and the id
    helper agreeing with each other (test_exit_quality covers those alone).
    """

    def _booked_as(self, fake: FakeBroker, db_url: str, monkeypatch) -> dict[str, str]:
        from scripts.reconcile import attribute_exits, to_fills
        from tradingagents_us.execution.reconcile import reconcile_fills

        _run(monkeypatch, fake, "--submit", "--db-url", db_url)
        (close,) = [o for o in fake.created if o.symbol == "XOM" and o.order_type == "market"]
        sell = FillActivity(
            id="fill-XOM-close",
            symbol="XOM",
            side="sell",
            qty=close.qty,
            price=100.5,
            transaction_time=datetime.now(UTC),
            order_id=close.id,
        )
        activities = [*fake.fills, sell]
        closed = reconcile_fills(to_fills(activities)).closed
        assert len(closed) == 1
        return attribute_exits(closed, activities, fake.created)

    def test_a_protected_time_exit_books_as_a_time_exit(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert list(self._booked_as(_aged_xom(), db_url, monkeypatch).values()) == ["time_exit"]

    def test_an_unprotected_time_exit_books_as_a_time_exit(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = FakeBroker(positions=[_position("XOM", 100.5)], fills=[_buy("XOM")])
        assert list(self._booked_as(fake, db_url, monkeypatch).values()) == ["time_exit"]


AGED = [f"T{i}" for i in range(8)]


@pytest.fixture
def aged_book_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A flat tape for eight names, every one of them due a time exit."""
    monkeypatch.setenv("KILL_SWITCH_FILE", str(tmp_path / "kill_switch.state"))
    url = f"sqlite:///{tmp_path / 'bars.db'}"
    repo = TradeLogRepository(engine=create_engine(url, future=True))
    flat = {"o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0}
    bars = [{"t": (TODAY - timedelta(days=d)).isoformat(), **flat} for d in range(BAR_DAYS, 0, -1)]
    with repo.session() as session:
        for symbol in AGED:
            write_bars(session, symbol, bars)
    return url


class TestExitBudgetThroughThePass:
    """What reaches the broker when every name in the book is due at once."""

    def _book(self) -> FakeBroker:
        return FakeBroker(
            positions=[_position(s, 100.5) for s in AGED],
            orders=[_stop(f"stop-{s}", s, 90.0) for s in AGED],
            fills=[_buy(s) for s in AGED],
        )

    def test_only_the_budget_is_sold_and_the_rest_keep_their_stops(
        self, aged_book_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = self._book()

        rc = _run(monkeypatch, fake, "--submit", "--db-url", aged_book_db)

        # The budget holds a bad input to two names only until a person
        # looks, and a warning line in the run log is read by no one.
        assert rc == 1, "a deferral pages"
        sold = [w[1]["symbol"] for w in fake.writes
                if w[0] == "submit_order" and w[1]["order_type"] == "market"]
        # Eight names: a quarter is two, under the cap of three.
        assert sold == ["T0", "T1"]
        assert [w[1] for w in fake.writes if w[0] == "cancel_order"] == ["stop-T0", "stop-T1"]
        # The six deferred are still held, and their stops are maintained, not released.
        ratcheted = [w[1] for w in fake.writes if w[0] == "replace_order"]
        assert ratcheted == [f"stop-{s}" for s in AGED[2:]]
        assert sorted(fake.positions) == AGED[2:]

    def test_a_second_pass_on_the_same_trade_date_closes_nothing_more(
        self, aged_book_db: str, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A regular-hours run (the exits fill at once), then another the same
        # day: a missed run replayed at boot before the scheduled one, or a
        # re-run by hand. Each pass took a fresh budget off the names still
        # held, and three passes sold four of the eight names.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = self._book()

        rcs = [_run(monkeypatch, fake, "--submit", "--db-url", aged_book_db) for _ in range(3)]

        sold = [w[1]["symbol"] for w in fake.writes
                if w[0] == "submit_order" and w[1]["order_type"] == "market"]
        assert sold == ["T0", "T1"]
        assert sorted(fake.positions) == AGED[2:]
        assert rcs == [1, 1, 1], "every pass that holds a due exit back pages"
        last = [r.getMessage() for r in caplog.records if r.getMessage().startswith("exit budget")]
        assert "closing 0 of 6 due (budget 2 of 8 positions today, 2 closed earlier today)" in (
            last[-1]
        )

    def test_an_exit_queued_on_an_earlier_day_is_left_and_spends_its_share(
        self, aged_book_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The run after a weekday exchange holiday (the timer runs Mon-Fri):
        # T0's exit, queued the trade date before, still waits for an open,
        # and the close that queued it released T0's stop. The lot is on its
        # way out. Cancelling and resending its exit left T0 with neither
        # exit nor stop whenever that cancel stuck. Left alone, it still sells
        # at the open tonight's exits queue for: one of that open's two.
        stamp = derive_exit_client_order_id("T0", TODAY - timedelta(days=1), "time")
        queued = dataclasses.replace(
            _stop("exit-T0", "T0", 0.0), client_order_id=stamp, order_type="market",
            stop_price=None, status="accepted", submitted_at=datetime.now(UTC) - timedelta(days=1),
        )
        fake = FakeBroker(
            positions=[_position(s, 100.5) for s in AGED],
            orders=[queued, *(_stop(f"stop-{s}", s, 90.0) for s in AGED[1:])],
            fills=[_buy(s) for s in AGED],
            exit_status="accepted",
        )

        rc = _run(monkeypatch, fake, "--submit", "--db-url", aged_book_db)

        assert rc == 1, "the six names still deferred page"
        sold = [w[1]["symbol"] for w in fake.writes
                if w[0] == "submit_order" and w[1]["order_type"] == "market"]
        assert sold == ["T1"]
        assert [w[1] for w in fake.writes if w[0] == "cancel_order"] == ["stop-T1"]
        assert fake.orders["exit-T0"].status == "accepted"

    def test_a_rerun_past_midnight_sells_no_more_than_the_budget_at_one_open(
        self, aged_book_db: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The 22:30 UTC run queues T0 and T1 for the next open. A rerun past
        # 00:00 UTC (after the overnight page, before 13:30) carries the next
        # UTC date, so none of those stamps is its own, yet its exits queue
        # for the same open. Charged nothing for T0 and T1, it queued T2 and
        # T3 as well: four market sells at one open, twice the budget.
        monkeypatch.setattr(
            "tradingagents_us.execution.protected_close.time.sleep", lambda _: None
        )
        fake = self._book()
        fake.exit_status = "accepted"
        evening = datetime.combine(TODAY - timedelta(days=1), time(22, 30), UTC)
        rerun = datetime.combine(TODAY, time(0, 40), UTC)

        rcs = []
        for when in (evening, rerun):
            _clock_at(monkeypatch, fake, when)
            rcs.append(_run(monkeypatch, fake, "--submit", "--db-url", aged_book_db))

        sold = [w[1]["symbol"] for w in fake.writes
                if w[0] == "submit_order" and w[1]["order_type"] == "market"]
        assert sold == ["T0", "T1"]
        assert [w[1] for w in fake.writes if w[0] == "cancel_order"] == ["stop-T0", "stop-T1"]
        assert rcs == [1, 1], "every pass that holds a due exit back pages"

    def test_the_deferred_names_are_reported(
        self, aged_book_db: str, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.INFO, logger="manage_positions")

        rc = _run(monkeypatch, self._book(), "--db-url", aged_book_db)

        assert rc == 0, "a dry run reports; only --submit pages"
        (line,) = [r for r in caplog.records if r.getMessage().startswith("exit budget")]
        assert line.levelno == logging.WARNING
        assert "closing 2 of 8 due" in line.getMessage()
        assert "deferred: T2, T3, T4, T5, T6, T7" in line.getMessage()
        assert sum("SKIP  exit_budget" in r.getMessage() for r in caplog.records) == 6


class TestEmptyBarCache:
    def test_a_held_name_with_no_bars_is_reported_and_never_closed(
        self, db_url: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # NVDA is flat and was bought 50 days ago, but the cache has nothing
        # for it. Its age must read as unknown, not as zero.
        caplog.set_level(logging.INFO, logger="manage_positions")
        fake = _book()

        _run(monkeypatch, fake, "--submit", "--backfill-stops", "--db-url", db_url)

        nvda = [r.getMessage() for r in caplog.records if r.getMessage().startswith("NVDA")]
        assert len(nvda) == 1
        assert "no_bars" in nvda[0]
        assert "age unknown" in nvda[0]
        assert "NVDA" not in str(fake.writes)
