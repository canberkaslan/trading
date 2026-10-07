"""Concurrent tickers size and submit one at a time, against a fresh book.

The daily run councils several tickers at once. The book trade.py read before
its council can be minutes stale by the time the order is sized, and another
ticker may have spent the cash meanwhile, so a BUY re-reads it under a lock
every order path takes. An exit spends no cash and is never dropped over the
lock, and the decision is recorded before any of it can fail.
"""

from __future__ import annotations

import fcntl
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.test_trade_cli import mock_dependencies  # noqa: F401 — the fixture
from tradingagents_us.execution import submit_lock
from tradingagents_us.execution.book import pending_buy_exposure
from tradingagents_us.file_lock import exclusive
from tradingagents_us.risk.portfolio_limits import PortfolioLimits
from tradingagents_us.schemas import AgentDecision


def _decision(rating: str) -> AgentDecision:
    buy = rating in ("Buy", "Overweight")
    return AgentDecision(
        ticker="AAPL", market="US", quote_currency="USD", rating=rating,
        entry_price=150.0 if buy else None, stop_loss=140.0 if buy else None,
        price_target=170.0 if buy else None, time_horizon="1-3 months",
        suggested_size_pct=0.05 if buy else 0.0, reasoning=[],
        final_decision_text=f"RATING:{rating}",
        timestamp_utc=datetime.now(UTC), decision_id=f"test-{rating.lower()}-lock",
    )


def _acct(cash: float) -> mock.MagicMock:
    acct = mock.MagicMock()
    acct.account_number, acct.status = "PA-TEST", "ACTIVE"
    acct.portfolio_value, acct.cash, acct.buying_power = 100_000.0, cash, cash
    acct.pattern_day_trader, acct.last_equity = False, 99_000.0
    return acct


def _lock_is_held(path: Path) -> bool:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


@pytest.fixture
def repo(monkeypatch: pytest.MonkeyPatch) -> mock.MagicMock:
    """Persisting run (no --no-persist) with the repository mocked."""
    monkeypatch.setattr(
        sys, "argv", ["trade.py", "--ticker", "AAPL", "--use-cached", "--db-url", "sqlite://"]
    )
    fake = mock.MagicMock()
    with mock.patch("scripts.trade.TradeLogRepository", return_value=fake):
        yield fake


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import trade

    monkeypatch.setattr(submit_lock, "BUY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(submit_lock, "EXIT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(trade, "_BOOK_RETRY_PAUSE_S", 0.0)


def _main_in_thread(rating: str) -> int:
    """main() on another thread: this thread may hold the lock, which re-enters."""
    from scripts import trade

    result: list[int] = []
    with mock.patch("scripts.trade._decision_from_cached", return_value=_decision(rating)):
        t = threading.Thread(target=lambda: result.append(trade.main()))
        t.start()
        t.join(20)
    assert result, "main() did not finish"
    return result[0]


def test_sizing_uses_the_book_read_under_the_lock(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock = submit_lock.lock_path()
    held_during_reads: list[bool] = []
    # Before the council: $50k. By sizing time another ticker spent it all.
    books = iter([_acct(50_000.0), _acct(0.0)])

    def account() -> mock.MagicMock:
        held_during_reads.append(_lock_is_held(lock))
        return next(books)

    mock_dependencies["alpaca"].account.side_effect = account
    seen: dict[str, float | None] = {}

    def sizer(**kwargs):
        seen["cash"] = kwargs["portfolio_ctx"].available_cash
        return mock.MagicMock(quantity=0, stop_loss=140.0, risk_approved=False,
                              rejection_reasons=["cash"], side="BUY")

    with mock.patch("scripts.trade._decision_from_cached", return_value=_decision("Buy")), \
         mock.patch("scripts.trade.size_from_decision", side_effect=sizer):
        from scripts.trade import main
        assert main() == 0

    assert held_during_reads == [False, True]  # the gate's read, then the sizer's
    assert seen["cash"] == 0.0
    out = capsys.readouterr().out
    assert "Book moved during the council" in out
    # The book the order was sized from is in the log, not only the first one.
    assert "=== BOOK AT SIZING (under the submit lock) ===" in out
    assert out.count("=== ALPACA ACCOUNT ===") == 2


def test_a_buy_that_cannot_lock_records_the_decision_and_sends_nothing(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
) -> None:
    with exclusive(submit_lock.lock_path()):
        assert _main_in_thread("Buy") == 1
    repo.save_decision.assert_called_once()
    assert not mock_dependencies["submit"].called


def test_a_sell_with_the_lock_held_is_still_submitted(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with exclusive(submit_lock.lock_path()):
        assert _main_in_thread("Sell") == 0
    repo.save_decision.assert_called_once()
    assert mock_dependencies["submit"].called
    assert "Submit lock NOT held" in capsys.readouterr().out


def test_an_unusable_lock_file_does_not_drop_a_sell(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A symlink where the lock should be: O_NOFOLLOW refuses it (OSError), as
    # would a lock file another user created 0600.
    link = tmp_path / "evil.lock"
    link.symlink_to(tmp_path / "elsewhere")
    monkeypatch.setenv(submit_lock.SUBMIT_LOCK_ENV, str(link))
    assert _main_in_thread("Sell") == 0
    assert mock_dependencies["submit"].called
    assert not (tmp_path / "elsewhere").exists()


def test_an_unusable_lock_file_refuses_a_buy(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = tmp_path / "evil.lock"
    link.symlink_to(tmp_path / "elsewhere")
    monkeypatch.setenv(submit_lock.SUBMIT_LOCK_ENV, str(link))
    assert _main_in_thread("Buy") == 1
    repo.save_decision.assert_called_once()
    assert not mock_dependencies["submit"].called


@pytest.mark.parametrize(("rating", "rc", "submitted"), [("Buy", 1, False), ("Sell", 0, True)])
def test_a_failed_fresh_read_still_records_the_decision(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
    rating: str,
    rc: int,
    submitted: bool,
) -> None:
    reads = iter([_acct(50_000.0)])

    def account() -> mock.MagicMock:
        try:
            return next(reads)
        except StopIteration:
            raise ConnectionError("alpaca down") from None

    mock_dependencies["alpaca"].account.side_effect = account
    assert _main_in_thread(rating) == rc
    repo.save_decision.assert_called_once()
    assert mock_dependencies["submit"].called is submitted


def test_the_fresh_read_is_retried_once(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
) -> None:
    outcomes = iter([_acct(50_000.0), ConnectionError("blip"), _acct(50_000.0)])

    def account() -> mock.MagicMock:
        nxt = next(outcomes)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    mock_dependencies["alpaca"].account.side_effect = account
    assert _main_in_thread("Buy") == 0
    assert mock_dependencies["submit"].called


def test_open_buys_count_toward_name_and_sector_exposure(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
) -> None:
    # Earlier tonight MSFT was bought; its order has not filled, so the
    # positions list does not show it. The sector cap must.
    mock_dependencies["alpaca"]._http.get.return_value.json.return_value = [
        {"symbol": "MSFT", "side": "buy", "qty": "20", "filled_qty": "0",
         "limit_price": "400.0", "submitted_at": "2026-09-29T22:31:00Z"},
    ]
    seen = {}

    def sizer(**kwargs):
        seen["ctx"] = kwargs["portfolio_ctx"]
        return mock.MagicMock(quantity=0, stop_loss=140.0, risk_approved=False,
                              rejection_reasons=["x"], side="BUY")

    with mock.patch("scripts.trade._decision_from_cached", return_value=_decision("Buy")), \
         mock.patch("scripts.trade.size_from_decision", side_effect=sizer), \
         mock.patch("scripts.trade.sector_for", side_effect=lambda s: "Tech"):
        from scripts.trade import main
        assert main() == 0
    ctx = seen["ctx"]
    assert ctx.existing_position_values_by_ticker["MSFT"] == pytest.approx(8_000.0)
    assert ctx.existing_position_values_by_sector["Tech"] == pytest.approx(8_000.0)


def test_the_lock_defaults_to_the_agents_run_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(submit_lock.SUBMIT_LOCK_ENV)
    path = submit_lock.lock_path()
    assert path.name == "trade-submit.lock"
    assert path.parent.name == "run"
    assert path.parent.parent == Path(__file__).resolve().parent.parent


def test_an_overweight_that_cannot_lock_is_not_sent(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
) -> None:
    # Overweight is a BUY in the sizer's mapping: it must not take the exit
    # bypass, which a copied list of ratings could silently let it.
    with exclusive(submit_lock.lock_path()):
        assert _main_in_thread("Overweight") == 1
    repo.save_decision.assert_called_once()
    assert not mock_dependencies["submit"].called


def test_no_market_data_call_is_made_under_the_lock(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Before the council nothing is pending. By sizing time NVDA (another
    # ticker of this run) has a market BUY in: its price must already be in
    # hand, fetched with the rest of the universe before the lock.
    monkeypatch.setenv("UNIVERSE", "AAPL NVDA MSFT")
    pages = iter([[], [
        {"symbol": "NVDA", "side": "buy", "qty": "10", "filled_qty": "0",
         "limit_price": None, "submitted_at": "2026-09-29T22:31:00Z"},
    ]])
    mock_dependencies["alpaca"]._http.get.side_effect = (
        lambda url: mock.MagicMock(json=mock.MagicMock(return_value=next(pages)))
    )
    lock = submit_lock.lock_path()
    calls: list[tuple[str, str, bool]] = []

    def watch(name: str, value):
        def fn(sym, *a, **k):
            calls.append((name, sym, _lock_is_held(lock)))
            return value
        return fn

    seen = {}

    def sizer(**kwargs):
        seen["cash"] = kwargs["portfolio_ctx"].available_cash
        return mock.MagicMock(quantity=0, stop_loss=140.0, risk_approved=False,
                              rejection_reasons=["x"], side="BUY")

    with mock.patch("scripts.trade._decision_from_cached", return_value=_decision("Buy")), \
         mock.patch("scripts.trade._fetch_current_price", side_effect=watch("price", 100.0)), \
         mock.patch("scripts.trade.sector_for", side_effect=watch("sector", "Tech")), \
         mock.patch("scripts.trade.average_dollar_volume", side_effect=watch("adv", 5e8)), \
         mock.patch("scripts.trade.rolling_price_stats", side_effect=watch("stats", None)), \
         mock.patch("scripts.trade.size_from_decision", side_effect=sizer):
        from scripts.trade import main
        assert main() == 0

    assert calls, "no market data was fetched at all"
    assert not [c for c in calls if c[2]], f"fetched under the lock: {calls}"
    assert ("price", "NVDA", False) in calls
    # NVDA's pending $1,000 was reserved from the prefetched price.
    assert seen["cash"] == pytest.approx(49_000.0)


def _positions(*qtys: int):
    """list_positions answers, one per call; an int is AAPL's share count."""
    answers = iter(qtys)

    def list_positions():
        qty = next(answers)
        if isinstance(qty, BaseException):
            raise qty
        return [mock.MagicMock(symbol="AAPL", qty=qty, market_value=qty * 150.0)]

    return list_positions


def _book_reads_fail_after_the_first(mock_dependencies) -> None:  # noqa: F811
    reads = iter([_acct(50_000.0)])

    def account():
        try:
            return next(reads)
        except StopIteration:
            raise ConnectionError("alpaca down") from None

    mock_dependencies["alpaca"].account.side_effect = account


def test_a_stale_exit_is_capped_to_the_shares_held_now(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
) -> None:
    # 10 shares before the council; something sold 6 during it. Selling 10
    # from the stale book would open a 6-share short.
    _book_reads_fail_after_the_first(mock_dependencies)
    mock_dependencies["alpaca"].list_positions.side_effect = _positions(10, 4)
    seen = {}

    def sizer(**kwargs):
        seen["held"] = kwargs["held_quantity"]
        return mock.MagicMock(quantity=4, stop_loss=140.0, risk_approved=True,
                              rejection_reasons=[], side="SELL")

    with mock.patch("scripts.trade.size_from_decision", side_effect=sizer):
        assert _main_in_thread("Sell") == 0
    assert seen["held"] == 4
    assert mock_dependencies["submit"].called


def test_an_exit_with_no_readable_position_is_skipped_and_fails_loudly(
    mock_dependencies,  # noqa: F811
    repo: mock.MagicMock,
    fast: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _book_reads_fail_after_the_first(mock_dependencies)
    mock_dependencies["alpaca"].list_positions.side_effect = _positions(
        10, ConnectionError("still down")
    )
    assert _main_in_thread("Sell") == 1  # daily_run counts it failed and pages
    repo.save_decision.assert_called_once()
    assert not mock_dependencies["submit"].called
    assert "EXIT NOT SENT" in capsys.readouterr().out


class _FillingBook:
    """$50k settled, nothing held, and a 100-share MSFT market BUY open ($40k
    at $400) that fills after the `fill_after`-th broker read. The submit lock
    covers our own order paths, not the broker's fills: a market-hours run
    (a manual one, or the timer's catch-up) can meet a fill mid-read."""

    base_url = "https://paper.invalid/v2"

    def __init__(self, fill_after: int | None) -> None:
        self.cash = 50_000.0
        self.positions: list[SimpleNamespace] = []
        self.open_orders = [
            {"symbol": "MSFT", "side": "buy", "qty": "100", "filled_qty": "0",
             "limit_price": None, "submitted_at": "2026-10-07T13:30:00Z"},
        ]
        self.fill_after = fill_after
        self.reads = 0
        self._http = SimpleNamespace(get=lambda url: SimpleNamespace(json=self._orders))

    def __enter__(self) -> _FillingBook:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _read(self) -> None:
        self.reads += 1
        if self.reads == self.fill_after:
            self.cash -= 40_000.0
            self.positions = [SimpleNamespace(symbol="MSFT", qty=100, market_value=40_000.0)]
            self.open_orders = []

    def account(self) -> SimpleNamespace:
        acct = SimpleNamespace(cash=self.cash, portfolio_value=100_000.0)
        self._read()
        return acct

    def list_positions(self) -> list[SimpleNamespace]:
        positions = list(self.positions)
        self._read()
        return positions

    def _orders(self) -> list[dict]:
        page = list(self.open_orders)
        self._read()
        return page


@pytest.mark.parametrize("fill_after", [None, 1, 2])
def test_a_buy_that_fills_mid_read_is_counted_in_cash_and_exposure(
    fill_after: int | None,
) -> None:
    # Whichever of the three reads the MSFT fill lands after, its $40k is
    # counted at least once in the cash and at least once in the exposure.
    # Counted in neither, a $30k BUY passes against $10k really there.
    from scripts import trade

    broker = _FillingBook(fill_after)
    with mock.patch("scripts.trade.AlpacaClient", return_value=broker):
        book = trade._read_book(
            "AAPL", PortfolioLimits(), verbose=False, price_of=lambda s: 400.0
        )
    assert broker.reads == 3
    assert book.spendable is not None
    assert book.spendable <= 10_000.0
    msft = book.existing_by_ticker.get("MSFT", 0.0) + pending_buy_exposure(
        book.open_buys, lambda s: 400.0
    ).get("MSFT", 0.0)
    assert msft >= 40_000.0
