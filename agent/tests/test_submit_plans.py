"""The two Python halves of a parallel daily run, against one fake broker.

`scripts.trade --plan-dir` councils and sizes, records the decision and stops:
it must reach nothing at the broker but reads, however many run at once.
`scripts.submit_plans` then takes the records in the order given and does what
`scripts.trade` does after its council, against the book as it stands right
then. These drive both through their real entry points with the broker, Polygon
and the council stubbed, and look at what reached the broker and the DB.

The yardstick is today's run: one `scripts.trade --submit` per ticker, one after
another. The two passes must send the same orders, in the same order, and write
the same rows.
"""

from __future__ import annotations

import itertools
import json
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient

from tradingagents_us.dataflows.alpaca_broker import Account, Order, Position
from tradingagents_us.execution import executor
from tradingagents_us.execution.plans import plan_path
from tradingagents_us.schemas import AgentDecision
from tradingagents_us.storage import TradeLogRepository, make_engine

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from scripts import submit_plans  # noqa: E402
from scripts import trade as trade_cli  # noqa: E402

RUN_DATE = datetime.now(UTC).date().isoformat()
RUN_ID = "daily-plans.TEST01"
#: JPM before NVDA: by NVDA's turn the earlier BUYs have taken all but $5k of
#: the cash, and the pre-council gate would skip any name that came after it.
UNIVERSE = ["AAPL", "MSFT", "JPM", "NVDA", "XOM"]

#: Calls that change something at the broker. Everything else is a read.
WRITES = {"submit_order", "cancel_order", "replace_order", "close_position", "close_all_positions"}


def _decision(ticker: str, rating: str, *, age: timedelta = timedelta(0)) -> AgentDecision:
    buy = rating == "Buy"
    return AgentDecision(
        ticker=ticker,
        market="US",
        quote_currency="USD",
        rating=rating,
        entry_price=100.0 if buy else None,
        stop_loss=95.0 if buy else None,
        price_target=130.0 if buy else None,
        reasoning=[],
        timestamp_utc=datetime.now(UTC) - age,
        decision_id=f"dec-{ticker}",
    )


@dataclass
class FakeAlpaca:
    """Stands in for every AlpacaClient() the code under test opens: one book.

    A BUY it accepts stays open (the run is after the close), so the next
    ticker's read of open orders reserves its cash, as Alpaca's would.
    """

    cash: float = 25_000.0
    equity: float = 100_000.0
    positions: dict[str, Position] = field(default_factory=dict)
    open_orders: list[Order] = field(default_factory=list)
    calls: list[tuple[str, object]] = field(default_factory=list)
    base_url: str = "https://paper.invalid/v2"

    def __post_init__(self) -> None:
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._http = SimpleNamespace(get=self._list_open_orders)

    def __call__(self) -> FakeAlpaca:
        return self

    def __enter__(self) -> FakeAlpaca:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def close(self) -> None:
        return None

    def _record(self, name: str, detail: object = None) -> None:
        with self._lock:
            self.calls.append((name, detail))

    @property
    def writes(self) -> list[tuple[str, object]]:
        return [c for c in self.calls if c[0] in WRITES]

    @property
    def submitted(self) -> list[tuple[str, str, float]]:
        return [(kw["symbol"], kw["side"], kw["qty"]) for name, kw in self.writes]  # type: ignore[index]

    def hold(self, symbol: str, qty: float, price: float = 100.0) -> None:
        self.positions[symbol] = Position(
            symbol=symbol, qty=qty, side="long", avg_entry_price=price,
            market_value=qty * price, unrealized_pl=0.0, unrealized_plpc=0.0,
        )

    # -- reads ------------------------------------------------------------------

    def account(self) -> Account:
        self._record("account")
        return Account(
            account_number="PA-FAKE", status="ACTIVE", cash=self.cash,
            buying_power=self.cash, portfolio_value=self.equity,
            pattern_day_trader=False, trading_blocked=False, currency="USD",
            last_equity=self.equity,
        )

    def list_positions(self) -> list[Position]:
        self._record("list_positions")
        return list(self.positions.values())

    def _list_open_orders(self, url: str) -> SimpleNamespace:
        self._record("list_open_orders", url)
        page = [
            {
                "symbol": o.symbol, "side": o.side, "qty": str(o.qty),
                "filled_qty": str(o.filled_qty), "limit_price": None,
                "submitted_at": o.submitted_at.isoformat(),
            }
            for o in reversed(self.open_orders)
        ]
        return SimpleNamespace(json=lambda: page)

    def get_order_by_client_order_id(self, client_order_id: str) -> Order | None:
        self._record("get_order_by_client_order_id", client_order_id)
        return next((o for o in self.open_orders if o.client_order_id == client_order_id), None)

    # -- writes -----------------------------------------------------------------

    def submit_order(self, **kw: object) -> Order:
        self._record("submit_order", kw)
        order = Order(
            id=f"ord-{next(self._ids)}", client_order_id=str(kw["client_order_id"]),
            symbol=str(kw["symbol"]), side=kw["side"], qty=float(kw["qty"]),  # type: ignore[arg-type]
            filled_qty=0.0, order_type=kw.get("order_type", "market"),  # type: ignore[arg-type]
            status="accepted", submitted_at=datetime.now(UTC), filled_avg_price=None,
        )
        self.open_orders.append(order)
        return order

    def cancel_order(self, order_id: str) -> None:
        self._record("cancel_order", order_id)

    def replace_order(self, order_id: str, **kw: object) -> None:
        self._record("replace_order", (order_id, kw))

    def close_position(self, symbol: str) -> None:
        self._record("close_position", symbol)

    def close_all_positions(self, cancel_orders: bool = True) -> None:
        self._record("close_all_positions", cancel_orders)


class Row(NamedTuple):
    ticker: str
    side: str
    quantity: int
    approved: bool
    reasons: tuple[str, ...]
    broker_order_id: str | None
    status: str
    error: str | None


@dataclass
class World:
    broker: FakeAlpaca
    decisions: dict[str, AgentDecision]
    prices: dict[str, float]
    kill_switch: Path
    db_url: str
    plan_dir: Path
    councils: list[str] = field(default_factory=list)

    def repo(self) -> TradeLogRepository:
        return TradeLogRepository(engine=make_engine(self.db_url))

    def plan(self, tickers: list[str], *, parallelism: int = 1, run_id: str = RUN_ID) -> list[int]:
        """Pass 1: one `scripts.trade --plan-dir` per ticker, `parallelism` at once."""
        def one(ticker: str) -> int:
            return trade_cli.main([
                "--ticker", ticker, "--date", RUN_DATE, "--db-url", self.db_url,
                "--plan-dir", str(self.plan_dir), "--run-id", run_id,
            ])

        with ThreadPoolExecutor(max_workers=parallelism) as pool:
            return list(pool.map(one, tickers))

    def submit(self, tickers: list[str], *, run_id: str = RUN_ID, submit: bool = True) -> int:
        """Pass 2: one `scripts.submit_plans` over the records."""
        argv = [
            "--plan-dir", str(self.plan_dir), "--run-id", run_id, "--date", RUN_DATE,
            "--db-url", self.db_url, *(["--submit"] if submit else []), *tickers,
        ]
        return submit_plans.main(argv)

    def sequential(self, tickers: list[str]) -> list[int]:
        """Today's run: one `scripts.trade --submit` per ticker, in order."""
        return [
            trade_cli.main(["--ticker", t, "--date", RUN_DATE, "--db-url", self.db_url, "--submit"])
            for t in tickers
        ]

    def order_rows(self) -> list[Row]:
        """Every order row with its last update, in the order they were written."""
        from tradingagents_us.storage.models import OrderUpdateRow

        repo = self.repo()
        rows = []
        with repo.session() as s:
            for r in repo.list_orders_since():
                [last] = s.query(OrderUpdateRow).filter_by(order_id=r.order_id).all()[-1:]
                rows.append(Row(
                    r.ticker, r.side, r.quantity, r.risk_approved,
                    tuple(r.rejection_reasons_json or []), r.broker_order_id,
                    last.status, last.error_message,
                ))
        return rows


def _make_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> World:
    root = tmp_path / name
    root.mkdir()
    plan_dir = root / "plans"
    plan_dir.mkdir()
    broker = FakeAlpaca()
    broker.hold("XOM", 20)
    world = World(
        broker=broker,
        decisions={
            "AAPL": _decision("AAPL", "Buy"),
            "MSFT": _decision("MSFT", "Buy"),
            "NVDA": _decision("NVDA", "Buy"),
            "JPM": _decision("JPM", "Hold"),
            "XOM": _decision("XOM", "Sell"),
        },
        prices={},
        kill_switch=root / "kill.state",
        db_url=f"sqlite:///{root / 'local.db'}",
        plan_dir=plan_dir,
    )
    # The box's local.db has its tables long before any council opens it.
    world.repo()

    def council(ticker: str, trade_date: str) -> AgentDecision:
        world.councils.append(ticker)
        return world.decisions[ticker]

    monkeypatch.setattr(trade_cli, "AlpacaClient", broker)
    monkeypatch.setattr(executor, "AlpacaClient", broker)
    monkeypatch.setattr(trade_cli, "propagate", council)
    monkeypatch.setattr(trade_cli, "_load_env", lambda: None)
    monkeypatch.setattr(trade_cli, "_fetch_current_price", lambda t: world.prices.get(t, 100.0))
    monkeypatch.setattr(trade_cli, "average_dollar_volume", lambda t: 5e8)
    monkeypatch.setattr(trade_cli, "count_correlated", lambda t, book: 0)
    monkeypatch.setattr(trade_cli, "rolling_price_stats", lambda t, repo=None: None)
    monkeypatch.setenv("KILL_SWITCH_PATH", str(world.kill_switch))
    return world


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield _make_world(tmp_path, monkeypatch, "box")


# --- pass 1 sends nothing -----------------------------------------------------------


@pytest.mark.parametrize("parallelism", [1, 2, 4])
def test_the_council_pass_writes_nothing_to_the_broker(world: World, parallelism: int) -> None:
    rcs = world.plan(UNIVERSE, parallelism=parallelism)

    assert rcs == [0] * len(UNIVERSE)
    assert world.broker.writes == [], "a council reached the broker"
    assert {c[0] for c in world.broker.calls} <= {"account", "list_positions", "list_open_orders"}
    assert sorted(world.councils) == sorted(UNIVERSE)
    for ticker in UNIVERSE:
        record = json.loads(plan_path(world.plan_dir, ticker).read_text())
        assert record["run_id"] == RUN_ID
        assert record["decision"]["decision_id"] == f"dec-{ticker}"
    # Every decision is in the DB, as today; no order row is, until pass 2.
    repo = world.repo()
    assert {d.ticker for d in repo.list_recent_decisions()} == set(UNIVERSE)
    assert repo.list_orders_since() == []


def test_a_recorded_decision_never_reaches_the_apps_approval_queue(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/v1/orders/pending lists every order row with no broker id, and /approve
    sends one: a record kept as such a row could be approved, stale, from the
    phone. The records are files the API never reads."""
    assert world.plan(UNIVERSE) == [0] * len(UNIVERSE)

    monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", "1")
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    from api.deps import get_repo
    from api.main import app

    repo = world.repo()
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        pending = TestClient(app).get("/v1/orders/pending")
    finally:
        app.dependency_overrides.pop(get_repo, None)
    assert pending.status_code == 200
    assert pending.json() == []


@pytest.mark.parametrize(
    "extra",
    [["--submit"], ["--hold"], [], ["--submit", "--run-id", RUN_ID]],
)
def test_a_council_pass_cannot_be_asked_to_send(world: World, extra: list[str]) -> None:
    argv = ["--ticker", "AAPL", "--date", RUN_DATE, "--plan-dir", str(world.plan_dir), *extra]
    if "--run-id" not in extra and extra:
        argv += ["--run-id", RUN_ID]
    with pytest.raises(SystemExit) as exc:
        trade_cli.main(argv)
    assert exc.value.code == 2
    assert world.broker.calls == [] and world.councils == []


# --- pass 2: today's orders, in today's order ------------------------------------


def test_the_two_passes_send_what_the_sequential_run_sends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = _make_world(tmp_path, monkeypatch, "sequential")
    assert today.sequential(UNIVERSE) == [0] * len(UNIVERSE)
    expected_writes, expected_rows = today.broker.writes, today.order_rows()

    split = _make_world(tmp_path, monkeypatch, "parallel")
    assert split.plan(UNIVERSE, parallelism=4) == [0] * len(UNIVERSE)
    assert split.broker.writes == []
    assert split.submit(UNIVERSE) == 0

    assert split.broker.writes == expected_writes
    assert split.order_rows() == expected_rows
    # And what that is: the cash the earlier BUYs reserved trims NVDA to the $5k
    # left, the exit goes out, the Hold is a refusal row.
    assert split.broker.submitted == [
        ("AAPL", "buy", 100), ("MSFT", "buy", 100), ("NVDA", "buy", 50), ("XOM", "sell", 20),
    ]
    [jpm] = [r for r in expected_rows if r.ticker == "JPM"]
    assert not jpm.approved and "non-actionable rating=Hold" in jpm.reasons


def test_the_submit_pass_goes_in_the_order_it_is_given(world: World) -> None:
    buys = ["AAPL", "MSFT", "NVDA"]
    assert world.plan(buys, parallelism=3) == [0, 0, 0]
    assert world.submit(["NVDA", "AAPL", "MSFT"]) == 0
    # Each sized against the BUYs placed before it: the last one gets what is left.
    assert world.broker.submitted == [
        ("NVDA", "buy", 100), ("AAPL", "buy", 100), ("MSFT", "buy", 50),
    ]


def test_without_submit_the_pass_is_a_dry_run(world: World) -> None:
    assert world.plan(UNIVERSE, parallelism=2) == [0] * len(UNIVERSE)
    assert world.submit(UNIVERSE, submit=False) == 0
    assert world.broker.writes == []
    statuses = {r.ticker: r.status for r in world.order_rows()}
    assert statuses["AAPL"] == "PENDING" and statuses["JPM"] == "REJECTED"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        # Filled since the council: the name is now at its 10% cap.
        (lambda w: w.broker.hold("AAPL", 100), "trimmed_to_zero_by_portfolio_caps"),
        # 25% of the book now in tech: another 10% breaks the 30% sector cap.
        (lambda w: (w.broker.hold("MSFT", 150), w.broker.hold("NVDA", 100)), "sector_pct="),
        # The cash went elsewhere.
        (lambda w: setattr(w.broker, "cash", 0.0), "trimmed_to_zero_by_cash_cap"),
        # The price ran to the target: no headroom left (executor guard).
        (lambda w: w.prices.__setitem__("AAPL", 126.0), "no_tp_headroom"),
        # The price fell onto the stop (executor guard).
        (lambda w: w.prices.__setitem__("AAPL", 96.0), "too_close_to_stop"),
    ],
)
def test_every_guard_judges_the_book_at_submit_time(world: World, change, reason: str) -> None:
    assert world.plan(["AAPL"]) == [0]
    change(world)
    assert world.submit(["AAPL"]) == 0, "a refusal is policy, not a failure"
    assert world.broker.writes == []
    [row] = world.order_rows()
    assert row.status == "REJECTED", row
    assert reason in (row.error or ""), row


def test_a_decision_too_old_to_trade_is_refused(world: World) -> None:
    world.decisions["AAPL"] = _decision("AAPL", "Buy", age=timedelta(hours=30))
    assert world.plan(["AAPL"]) == [0]
    assert world.submit(["AAPL"]) == 0
    assert world.broker.writes == []
    [row] = world.order_rows()
    assert row.status == "REJECTED"
    assert "stale_decision" in (row.error or ""), row


@pytest.mark.parametrize("state", ["PAUSE_NEW", "FLATTEN_ALL"])
def test_the_kill_switch_is_read_at_submit_time(world: World, state: str) -> None:
    assert world.plan(UNIVERSE, parallelism=4) == [0] * len(UNIVERSE)
    world.kill_switch.write_text(state)  # flipped while the councils ran
    assert world.submit(UNIVERSE) == 0
    if state == "PAUSE_NEW":
        # No new entries, and the exit still goes out: never block an exit.
        assert world.broker.submitted == [("XOM", "sell", 20)]
    else:
        # The flatten owns the book: nothing at all.
        assert world.broker.writes == []
    buys = ("AAPL", "MSFT", "NVDA")
    refused = {r.ticker: r.reasons for r in world.order_rows() if r.ticker in buys}
    assert len(refused) == 3
    assert all(f"kill_switch={state}" in reasons for reasons in refused.values()), refused


def test_one_tickers_crash_does_not_cost_the_rest(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert world.plan(["AAPL", "MSFT"]) == [0, 0]
    real = trade_cli.read_book

    def flaky(ticker: str, limits):  # noqa: ANN001
        if ticker == "AAPL":
            raise RuntimeError("broker read blew up")
        return real(ticker, limits)

    monkeypatch.setattr(trade_cli, "read_book", flaky)
    assert world.submit(["AAPL", "MSFT"]) == 1
    assert world.broker.submitted == [("MSFT", "buy", 100)]


def test_the_submit_pass_reports_each_ticker(world: World, capsys: pytest.CaptureFixture) -> None:
    assert world.plan(["AAPL", "MSFT"]) == [0, 0]
    plan_path(world.plan_dir, "MSFT").unlink()  # never recorded
    assert world.submit(["AAPL", "MSFT"]) == 1
    out = capsys.readouterr().out
    assert f"--- AAPL submit @ {RUN_DATE} ---" in out
    assert "  -> AAPL done" in out
    assert "  -> MSFT FAILED (rc=1)" in out


# --- once, and only by its own run ---------------------------------------------------


def test_a_record_is_sent_once(world: World) -> None:
    assert world.plan(["AAPL"]) == [0]
    assert world.submit(["AAPL"]) == 0
    assert world.broker.submitted == [("AAPL", "buy", 100)]
    # The same pass again (a retry, a second process): nothing left to take.
    assert world.submit(["AAPL"]) == 1
    assert world.broker.submitted == [("AAPL", "buy", 100)]
    assert len(world.order_rows()) == 1


def test_a_record_from_an_interrupted_run_is_never_sent_by_the_next(world: World) -> None:
    # Yesterday's run recorded AAPL and died before its submit pass.
    assert world.plan(["AAPL"], run_id="daily-plans.OLDRUN") == [0]
    assert world.submit(["AAPL"]) == 1  # today's run id
    assert world.broker.writes == []
    assert world.order_rows() == []
    # Refused records are spent: not sent by a later pass of that run either.
    assert world.submit(["AAPL"], run_id="daily-plans.OLDRUN") == 1
    assert world.broker.writes == []


@pytest.mark.parametrize(
    ("field_name", "value"),
    [("date", "2001-01-01"), ("ticker", "MSFT"), ("version", 99)],
)
def test_a_record_that_does_not_match_its_pass_is_refused(
    world: World, field_name: str, value: object
) -> None:
    assert world.plan(["AAPL"]) == [0]
    path = plan_path(world.plan_dir, "AAPL")
    record = json.loads(path.read_text())
    record[field_name] = value
    path.write_text(json.dumps(record))
    assert world.submit(["AAPL"]) == 1
    assert world.broker.writes == []


def test_a_torn_record_is_refused(world: World) -> None:
    plan_path(world.plan_dir, "AAPL").write_text('{"version": 1, "run_id": ')
    assert world.submit(["AAPL"]) == 1
    assert world.broker.writes == []


def test_a_name_the_sequential_gate_skips_is_councilled_but_never_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing the passes do differently, and why it is safe.

    The pre-council gate skips a name the cash cannot buy one share of. In the
    sequential run NVDA comes after two BUYs that take all the cash, so it is
    never councilled. The councils of pass 1 all run before any order exists,
    so NVDA is councilled (the cost of one council) and then refused at submit
    time by the same cash cap: a refusal row, and nothing more at the broker.
    """
    tickers = ["AAPL", "MSFT", "NVDA"]
    today = _make_world(tmp_path, monkeypatch, "sequential")
    today.broker.cash = 20_000.0
    assert today.sequential(tickers) == [0, 0, 0]
    assert today.councils == ["AAPL", "MSFT"]

    split = _make_world(tmp_path, monkeypatch, "parallel")
    split.broker.cash = 20_000.0
    assert split.plan(tickers, parallelism=3) == [0, 0, 0]
    assert sorted(split.councils) == tickers
    assert split.submit(tickers) == 0

    assert split.broker.writes == today.broker.writes
    assert split.order_rows()[:2] == today.order_rows()
    [nvda] = split.order_rows()[2:]
    assert nvda.ticker == "NVDA" and not nvda.approved
    assert any(r.startswith("trimmed_to_zero_by_cash_cap") for r in nvda.reasons)


def test_a_decision_for_another_name_fails_the_council_loudly(world: World) -> None:
    """daily_run.sh looks for the record under the ticker it asked about; one
    filed under any other name would read as a skipped council and never be sent."""
    world.decisions["AAPL"] = _decision("MSFT", "Buy")
    with pytest.raises(ValueError, match="the council for 'AAPL'"):
        world.plan(["AAPL"])
    assert list(world.plan_dir.iterdir()) == []
    assert world.broker.writes == []
