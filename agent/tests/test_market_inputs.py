"""Real inputs for risk checks that were fed constants.

`market_inputs` exists because three portfolio caps could not fire — they were
handed a hardcoded $1B ADV, an empty sector book, and a correlation count of
zero. These tests cover the two additions that revive the CIRCUIT BREAKER's
equivalent dead checks: a price-anomaly z-score computed against the price
itself (always exactly zero) and a consecutive-loss counter nothing ever
incremented.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.models import ClosedTradeRow
from tradingagents_us.storage.price_cache import write_bars


def _repo(tmp_path) -> TradeLogRepository:
    return TradeLogRepository(engine=create_engine(f"sqlite:///{tmp_path}/t.db", future=True))


def _seed(repo: TradeLogRepository, ticker: str, closes: list[float]) -> None:
    """Consecutive daily bars ending today, one per close."""
    start = date.today() - timedelta(days=len(closes) - 1)
    bars = [
        {
            "t": (start + timedelta(days=i)).isoformat(),
            "o": c, "h": c, "l": c, "c": c, "v": 1_000_000.0,
        }
        for i, c in enumerate(closes)
    ]
    with repo.session() as s:
        write_bars(s, ticker, bars)


def _seed_trades(repo: TradeLogRepository, pnls: list[float]) -> None:
    """Round trips, NEWEST FIRST in the argument, written with descending exits."""
    now = datetime.now(UTC)
    with repo.session() as s:
        for i, pnl in enumerate(pnls):
            s.add(
                ClosedTradeRow(
                    trade_id=f"t{i}",
                    symbol="AAPL",
                    direction="LONG",
                    quantity=1.0,
                    entry_price=100.0,
                    exit_price=100.0 + pnl,
                    realized_pnl=pnl,
                    realized_pnl_pct=pnl / 100.0,
                    holding_days=1.0,
                    open_activity_id=f"o{i}",
                    close_activity_id=f"c{i}",
                    opened_at_utc=now - timedelta(days=i + 1),
                    closed_at_utc=now - timedelta(days=i),
                    reconciled_at_utc=now,
                )
            )
        s.commit()




class TestRollingPriceStats:
    """The price-anomaly breaker was fed `rolling_mean = the price itself`, so
    its z-score was |p - p| / std — exactly zero, every ticker, every run. It
    read as a working control in every log."""

    def test_none_when_history_is_too_short(self, tmp_path) -> None:
        from tradingagents_us.risk.market_inputs import rolling_price_stats

        repo = _repo(tmp_path)
        _seed(repo, "AAPL", [100.0] * 5)
        # A caller must SKIP the check, not substitute a band: a fabricated
        # deviation is how this became inert in the first place.
        assert rolling_price_stats("AAPL", repo=repo) is None

    def test_computes_mean_and_sample_deviation(self, tmp_path) -> None:
        from tradingagents_us.risk.market_inputs import rolling_price_stats

        repo = _repo(tmp_path)
        _seed(repo, "AAPL", [10.0, 12.0] * 8)
        out = rolling_price_stats("AAPL", repo=repo)
        assert out is not None
        mean, std = out
        assert mean == pytest.approx(11.0)
        # Sample sd of an alternating 10/12 series, n=16.
        assert std == pytest.approx(1.0328, abs=1e-3)

    def test_a_flat_series_has_zero_deviation_and_the_caller_must_handle_it(
        self, tmp_path
    ) -> None:
        from tradingagents_us.risk.market_inputs import rolling_price_stats

        repo = _repo(tmp_path)
        _seed(repo, "AAPL", [100.0] * 20)
        out = rolling_price_stats("AAPL", repo=repo)
        assert out is not None
        assert out[1] == 0.0
        # The breaker guards on `rolling_std > 0`, so a halted-but-unmoved name
        # skips the check rather than dividing by zero.


class TestRecentLossStreak:
    """`record_trade_result` was never called, so the consecutive-loss counter
    sat at zero from process start to exit and a five-loss run halted nothing."""

    def test_zero_on_an_empty_ledger(self, tmp_path) -> None:
        from tradingagents_us.risk.market_inputs import recent_loss_streak

        assert recent_loss_streak(repo=_repo(tmp_path)) == 0

    def test_counts_back_from_the_newest_close(self, tmp_path) -> None:
        from tradingagents_us.risk.market_inputs import recent_loss_streak

        repo = _repo(tmp_path)
        # Newest first: three losses, then a winner that ends the streak.
        _seed_trades(repo, [-5.0, -3.0, -1.0, 10.0, -99.0])
        assert recent_loss_streak(repo=repo) == 3

    def test_a_break_even_trade_ends_the_streak(self, tmp_path) -> None:
        from tradingagents_us.risk.market_inputs import recent_loss_streak

        repo = _repo(tmp_path)
        # It did not lose. Treating "no move" as a loss halts the book for a
        # flat week.
        _seed_trades(repo, [-5.0, 0.0, -1.0])
        assert recent_loss_streak(repo=repo) == 1

    def test_an_unbroken_run_of_losses_counts_all_of_them(self, tmp_path) -> None:
        from tradingagents_us.risk.market_inputs import recent_loss_streak

        repo = _repo(tmp_path)
        _seed_trades(repo, [-1.0] * 6)
        assert recent_loss_streak(repo=repo) == 6
