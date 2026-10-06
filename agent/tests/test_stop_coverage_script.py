"""scripts/stop_coverage.py: the human report keeps exiting shares apart.

A lot our own time exit is selling at the open has no stop. The report must
not count it as protected, nor as naked: it gets a column and a note.
"""

from __future__ import annotations

from scripts.stop_coverage import format_report, payload
from tradingagents_us.risk.stop_coverage import OrderView, PositionView, coverage


def _report():
    return coverage(
        [PositionView("GOOGL", 32, "long"), PositionView("XOM", 40, "long")],
        [
            OrderView("GOOGL", "sell", "market", "accepted", 32, own_exit=True),
            OrderView("XOM", "sell", "stop", "held", 40, stop_price=100.0),
        ],
    )


def test_the_table_has_an_exit_column_and_names_the_lot() -> None:
    text = format_report(_report())
    header, _, googl, xom = text.splitlines()[:4]
    assert "EXIT" in header
    assert googl.split()[:6] == ["GOOGL", "long", "32", "0", "32", "0"]
    assert xom.split()[:6] == ["XOM", "long", "40", "40", "0", "0"]
    assert "0.0% naked" in text
    assert "NOTE: on their way out at the next open under our own time exit" in text
    assert "GOOGL (32)" in text


def test_the_json_carries_exiting_shares() -> None:
    body = payload(_report())
    assert body["exiting_qty"] == 32
    assert body["naked_qty"] == 0
    googl = next(s for s in body["symbols"] if s["symbol"] == "GOOGL")
    assert (googl["protected_qty"], googl["exiting_qty"], googl["naked_qty"]) == (0, 32, 0)
