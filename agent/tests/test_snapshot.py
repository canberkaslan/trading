"""Eval snapshot logger — pure build + append, no broker calls."""

from __future__ import annotations

import json
from pathlib import Path

from tradingagents_us.dataflows.alpaca_broker import Account, Position
from scripts.snapshot import append_snapshot, build_snapshot


def _account(equity: float = 100_000.0, cash: float = 40_000.0) -> Account:
    return Account(
        account_number="PA1",
        status="ACTIVE",
        cash=cash,
        buying_power=cash * 2,
        portfolio_value=equity,
        pattern_day_trader=False,
        trading_blocked=False,
        currency="USD",
    )


def _pos(symbol: str, mv: float) -> Position:
    return Position(
        symbol=symbol,
        qty=10,
        side="long",
        avg_entry_price=mv / 10,
        market_value=mv,
        unrealized_pl=5.0,
        unrealized_plpc=0.01,
    )


def test_build_snapshot_weights_and_top() -> None:
    snap = build_snapshot(
        _account(equity=100_000.0),
        [_pos("AAPL", 30_000.0), _pos("MSFT", 10_000.0)],
        "2026-06-24T22:31:00+00:00",
    )
    assert snap["n_positions"] == 2
    assert snap["equity"] == 100_000.0
    assert snap["top_weight_pct"] == 30.0  # AAPL 30k / 100k
    aapl = next(p for p in snap["positions"] if p["ticker"] == "AAPL")
    assert aapl["weight_pct"] == 30.0


def test_build_snapshot_empty_book() -> None:
    snap = build_snapshot(_account(), [], "2026-06-24T22:31:00+00:00")
    assert snap["n_positions"] == 0
    assert snap["top_weight_pct"] == 0.0
    assert snap["positions"] == []


def test_build_snapshot_zero_equity_no_div0() -> None:
    snap = build_snapshot(_account(equity=0.0), [_pos("AAPL", 1000.0)], "t")
    assert snap["positions"][0]["weight_pct"] == 0.0


def test_append_snapshot_writes_jsonl(tmp_path: Path) -> None:
    f = tmp_path / "nested" / "snaps.jsonl"
    append_snapshot({"ts": "a", "equity": 1}, f)
    append_snapshot({"ts": "b", "equity": 2}, f)
    lines = f.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["ts"] == "b"
