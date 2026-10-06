"""/v1/risk/stop-coverage — what is actually protecting the book.

The endpoint exists because the answer turned out to be bad: the first coverage
run on the live paper account found 75.5% of held shares with no stop behind
them. These tests pin the two ways the underlying accounting is easy to get
wrong in the DANGEROUS direction — both of which invent naked exposure, which is
what makes a back-fill place a second stop on shares that already have one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.dataflows.alpaca_broker import Clock, Order, Position, Session
from tradingagents_us.execution.executor import derive_exit_client_order_id


def _order(symbol, side="sell", order_type="stop", status="held", qty=10.0,
           filled=0.0, stop_price=90.0, legs=()):
    o = MagicMock()
    o.symbol, o.side, o.order_type, o.status = symbol, side, order_type, status
    o.qty, o.filled_qty, o.stop_price = qty, filled, stop_price
    o.legs = legs
    return o


def _position(symbol, qty, side="long"):
    p = MagicMock()
    p.symbol, p.qty, p.side = symbol, qty, side
    return p


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    from api.main import app

    return TestClient(app)


#: The night of 2026-10-05, after the close: GOOGL's time exit is queued.
NIGHT = datetime(2026, 10, 5, 22, 40, tzinfo=UTC)


def _sessions() -> list[Session]:
    out, day = [], date(2026, 9, 21)
    while day <= date(2026, 10, 9):
        if day.weekday() < 5:
            out.append(Session(
                day, datetime.combine(day, time(13, 30), UTC), datetime.combine(day, time(20), UTC)
            ))
        day += timedelta(days=1)
    return out


def _googl_exit(coid=None, qty=32.0) -> Order:
    return Order(
        id="x1",
        client_order_id=coid or derive_exit_client_order_id("GOOGL", date(2026, 10, 5), "time"),
        symbol="GOOGL", side="sell", qty=qty, filled_qty=0.0, order_type="market",
        status="accepted", submitted_at=datetime(2026, 10, 5, 22, 31, tzinfo=UTC),
        filled_avg_price=None,
    )


def _with_alpaca(client: TestClient, positions, orders) -> dict:
    from api.deps import get_alpaca
    from api.main import app

    cli = MagicMock()
    cli.list_positions.return_value = positions
    cli.list_orders.return_value = orders
    cli.clock.return_value = Clock(
        False, NIGHT.isoformat(), "2026-10-06T13:30:00+00:00", "2026-10-06T20:00:00+00:00"
    )
    cli.calendar.side_effect = lambda start, end: [
        s for s in _sessions() if start <= s.date <= end
    ]
    cli.close = MagicMock()
    app.dependency_overrides[get_alpaca] = lambda: cli
    try:
        r = client.get("/v1/risk/stop-coverage")
        assert r.status_code == 200, r.text
        return r.json()
    finally:
        app.dependency_overrides.pop(get_alpaca, None)


class TestCoverageReporting:
    def test_a_fully_bracketed_book_is_not_reported_as_naked(self, client: TestClient) -> None:
        # The dangerous error: the resting leg of a bracket sits in status
        # `held`, which Alpaca's "open" filter excludes. Reading it the obvious
        # way reports a protected book as having zero stops.
        body = _with_alpaca(
            client, [_position("AAPL", 10.0)], [_order("AAPL", status="held", qty=10.0)]
        )
        assert body["naked_qty"] == 0.0
        assert body["naked_pct"] == 0.0
        assert body["protected_qty"] == 10.0

    def test_a_nested_bracket_leg_still_counts_as_protection(self, client: TestClient) -> None:
        # The second half of the same error: legs are returned as CHILDREN of
        # the parent order, so a reader that does not descend sees a naked book.
        parent = _order("AAPL", side="buy", order_type="market", status="filled",
                        qty=10.0, filled=10.0, stop_price=None,
                        legs=(_order("AAPL", status="held", qty=10.0),))
        body = _with_alpaca(client, [_position("AAPL", 10.0)], [parent])
        assert body["naked_qty"] == 0.0

    def test_reports_an_unprotected_position(self, client: TestClient) -> None:
        body = _with_alpaca(client, [_position("META", 18.0)], [])
        assert body["naked_qty"] == 18.0
        assert body["naked_pct"] == pytest.approx(100.0)
        assert body["symbols"][0]["symbol"] == "META"
        assert body["symbols"][0]["is_actionable"] is True

    def test_partial_protection_is_split_not_rounded(self, client: TestClient) -> None:
        # The live book's GOOGL: 32 shares held behind stops covering 3.
        body = _with_alpaca(
            client, [_position("GOOGL", 32.0)], [_order("GOOGL", qty=3.0, stop_price=326.0)]
        )
        row = body["symbols"][0]
        assert row["protected_qty"] == 3.0
        assert row["naked_qty"] == 29.0

    def test_an_empty_book_is_not_an_exposure(self, client: TestClient) -> None:
        # 0/0 must be 0%, not 100% — otherwise a flat account pages every day.
        body = _with_alpaca(client, [], [])
        assert body["total_qty"] == 0.0
        assert body["naked_pct"] == 0.0

    def test_an_unrecognised_status_is_indeterminate_not_naked(self, client: TestClient) -> None:
        # Neither evidence of protection nor of its absence. Collapsing it into
        # "naked" produces a confident number with nothing under it, and a
        # back-fill acting on it doubles up a stop that turns out to be live.
        body = _with_alpaca(
            client, [_position("V", 30.0)], [_order("V", status="calculated", qty=30.0)]
        )
        assert body["naked_qty"] == 0.0
        assert body["indeterminate_qty"] == 30.0
        assert body["symbols"][0]["is_actionable"] is False

    def test_a_lot_our_time_exit_is_selling_is_exiting_not_naked(
        self, client: TestClient
    ) -> None:
        # 2026-10-05: GOOGL's stops released, one stamped market sell for all
        # 32 queued for the open. The app must not show it naked, nor stopped.
        body = _with_alpaca(
            client,
            [_position("GOOGL", 32.0), _position("XOM", 40.0)],
            [_order("XOM", qty=40.0), _googl_exit()],
        )
        assert body["naked_qty"] == 0.0
        assert body["exiting_qty"] == 32.0
        assert body["protected_qty"] == 40.0
        row = next(r for r in body["symbols"] if r["symbol"] == "GOOGL")
        assert (row["protected_qty"], row["exiting_qty"], row["naked_qty"]) == (0.0, 32.0, 0.0)
        assert row["is_actionable"] is False

    def test_a_market_sell_that_is_not_our_exit_is_naked(self, client: TestClient) -> None:
        body = _with_alpaca(
            client, [_position("GOOGL", 32.0)], [_googl_exit(coid="hand-close-1")]
        )
        assert body["naked_qty"] == 32.0
        assert body["exiting_qty"] == 0.0

    def test_an_exit_for_part_of_the_lot_leaves_the_rest_naked(self, client: TestClient) -> None:
        body = _with_alpaca(client, [_position("GOOGL", 32.0)], [_googl_exit(qty=20.0)])
        assert (body["exiting_qty"], body["naked_qty"]) == (20.0, 12.0)

    def test_a_short_is_read_with_its_side(self, client: TestClient) -> None:
        # Alpaca sends a short's quantity negative. Read as a long, it summed
        # to minus its size and a sell stop on it read as protection.
        short = Position("GOOGL", -32.0, "short", 100.0, -3200.0, 0.0, 0.0)
        body = _with_alpaca(client, [short], [_order("GOOGL", qty=32.0)])
        assert body["total_qty"] == 32.0
        assert body["naked_qty"] == 32.0

    def test_a_stop_with_no_position_under_it_is_surfaced(self, client: TestClient) -> None:
        # Not dead weight: a stop with nothing to sell opens a short if it fires.
        body = _with_alpaca(client, [], [_order("XOM", qty=5.0)])
        assert "XOM" in body["orphan_stop_symbols"]

    def test_a_broker_outage_is_a_502_not_a_clean_zero(self, client: TestClient) -> None:
        # Reporting "0% naked" when the broker is unreachable is the worst
        # possible answer: it says the book is safe because nothing was checked.
        from api.deps import get_alpaca
        from api.main import app

        cli = MagicMock()
        cli.list_positions.side_effect = RuntimeError("connection refused")
        cli.close = MagicMock()
        app.dependency_overrides[get_alpaca] = lambda: cli
        try:
            r = client.get("/v1/risk/stop-coverage")
            assert r.status_code == 502
            assert "alpaca_error" in r.text
        finally:
            app.dependency_overrides.pop(get_alpaca, None)
