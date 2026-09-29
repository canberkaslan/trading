"""Concurrent tickers size and submit one at a time, against a fresh book.

The daily run councils several tickers at once. The book trade.py read before
its council can be minutes stale by the time the order is sized, and another
ticker may have spent the cash meanwhile, so sizing re-reads it under a lock
every ticker process takes.
"""

from __future__ import annotations

import fcntl
import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest

from tests.test_trade_cli import mock_dependencies  # noqa: F401 — the fixture
from tradingagents_us.file_lock import exclusive
from tradingagents_us.schemas import AgentDecision


def _buy() -> AgentDecision:
    return AgentDecision(
        ticker="AAPL", market="US", quote_currency="USD", rating="Buy",
        entry_price=150.0, stop_loss=140.0, price_target=170.0, time_horizon="1-3 months",
        suggested_size_pct=0.05, reasoning=[], final_decision_text="RATING:Buy",
        timestamp_utc=datetime.now(UTC), decision_id="test-buy-lock",
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
def argv(monkeypatch: pytest.MonkeyPatch) -> None:
    args = ["trade.py", "--ticker", "AAPL", "--use-cached", "--no-persist"]
    monkeypatch.setattr(sys, "argv", args)


def test_sizing_uses_the_book_read_under_the_lock(
    mock_dependencies,  # noqa: F811
    argv: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lock = Path(os.environ["TRADE_SUBMIT_LOCK_PATH"])
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

    with mock.patch("scripts.trade._decision_from_cached", return_value=_buy()), \
         mock.patch("scripts.trade.size_from_decision", side_effect=sizer):
        from scripts.trade import main
        assert main() == 0

    assert held_during_reads == [False, True]  # the gate's read, then the sizer's
    assert seen["cash"] == 0.0
    assert "Book moved during the council" in capsys.readouterr().out


def test_a_lock_that_never_frees_fails_the_ticker_without_submitting(
    mock_dependencies,  # noqa: F811
    argv: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import trade

    monkeypatch.setattr(trade, "SUBMIT_LOCK_TIMEOUT_S", 0.2)
    result: list[int] = []
    with mock.patch("scripts.trade._decision_from_cached", return_value=_buy()), \
         exclusive(os.environ["TRADE_SUBMIT_LOCK_PATH"]):
        # main() in another thread: a lock this thread holds would re-enter.
        t = threading.Thread(target=lambda: result.append(trade.main()))
        t.start()
        t.join(10)
    assert result == [1]
    assert not mock_dependencies["submit"].called


def test_the_lock_defaults_to_one_per_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts import trade

    monkeypatch.delenv("TRADE_SUBMIT_LOCK_PATH")
    assert trade._submit_lock_path().name == "tradingagents-trade-submit.lock"
