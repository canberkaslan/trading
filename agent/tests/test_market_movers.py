"""The market board — the arithmetic, and the floor that keeps it usable."""

from __future__ import annotations

import pytest

from api.routes.market import Mover, build_rows, select


def _bar(t: str, c: float, v: float = 1_000_000) -> dict:
    return {"T": t, "c": c, "v": v}


class TestChangeArithmetic:
    def test_change_is_measured_against_the_previous_close(self) -> None:
        # Not against the same day's open. "Today's change" means yesterday's
        # close everywhere it is quoted, and a different basis wearing the same
        # name would disagree with every other screen the operator looks at.
        rows = build_rows([_bar("AAPL", 110)], [_bar("AAPL", 100)], {}, {})
        assert rows[0].change_pct == pytest.approx(10.0)

    def test_a_fall_is_negative(self) -> None:
        rows = build_rows([_bar("X", 90)], [_bar("X", 100)], {}, {})
        assert rows[0].change_pct == pytest.approx(-10.0)

    def test_dollar_volume_is_price_times_shares(self) -> None:
        # Share count alone would rank a $3 stock above a $500 one.
        rows = build_rows([_bar("X", 50, 1_000)], [_bar("X", 50)], {}, {})
        assert rows[0].dollar_volume == pytest.approx(50_000)

    def test_a_name_with_no_prior_close_is_omitted(self) -> None:
        # Showing 0% would claim the name was flat, which is a different
        # statement from "it was not trading here yesterday".
        assert build_rows([_bar("NEW", 10)], [_bar("OLD", 10)], {}, {}) == []

    def test_a_zero_prior_close_does_not_divide(self) -> None:
        assert build_rows([_bar("X", 10)], [_bar("X", 0)], {}, {}) == []


class TestLabels:
    def test_the_company_name_is_used_when_known(self) -> None:
        rows = build_rows([_bar("AAPL", 1)], [_bar("AAPL", 1)], {"AAPL": "Apple Inc."}, {})
        assert rows[0].name == "Apple Inc."

    def test_it_falls_back_to_the_ticker_rather_than_blank(self) -> None:
        assert build_rows([_bar("ZZ", 1)], [_bar("ZZ", 1)], {}, {})[0].name == "ZZ"

    def test_index_membership_and_sector_are_carried(self) -> None:
        members = {"AAPL": {"name": "Apple", "sector": "Information Technology"}}
        row = build_rows([_bar("AAPL", 1)], [_bar("AAPL", 1)], {}, members)[0]
        assert row.in_sp500 is True
        assert row.sector == "Information Technology"

    def test_a_non_member_is_marked_as_such(self) -> None:
        row = build_rows([_bar("XYZ", 1)], [_bar("XYZ", 1)], {}, {})[0]
        assert row.in_sp500 is False
        assert row.sector is None


def _m(t: str, change: float, dv: float, price: float = 100.0, sp: bool = False) -> Mover:
    return Mover(
        ticker=t, name=t, price=price, change_pct=change, dollar_volume=dv, in_sp500=sp
    )


class TestTheLiquidityFloor:
    """Ranked raw, the gainers list is penny stocks.

    A real session here had FTFT at +179% on an $8 quote. Those are real
    prints and useless as a watchlist — a screen whose top row is an $8 stock
    that doubled is one the operator learns to ignore.
    """

    def test_a_thin_name_is_kept_out_of_the_gainers(self) -> None:
        rows = [_m("PENNY", 179.0, 1_000_000), _m("REAL", 4.0, 900_000_000)]
        assert [r.ticker for r in select(rows, "gainers", "all", 10)] == ["REAL"]

    def test_a_sub_dollar_quote_is_kept_out(self) -> None:
        # A two-cent tick on a $0.30 stock is +7%; the arithmetic is
        # meaningless before the liquidity question is even asked.
        rows = [_m("CENT", 50.0, 500_000_000, price=0.30), _m("REAL", 3.0, 500_000_000)]
        assert [r.ticker for r in select(rows, "gainers", "all", 10)] == ["REAL"]

    def test_the_floor_does_not_apply_to_the_volume_ranking(self) -> None:
        # Volume ranks itself; a floor there would only remove rows that could
        # never reach the top anyway.
        rows = [_m("THIN", 0.0, 1_000), _m("FAT", 0.0, 9_000_000_000)]
        assert [r.ticker for r in select(rows, "volume", "all", 10)] == ["FAT", "THIN"]


class TestOrdering:
    def test_gainers_are_highest_first(self) -> None:
        rows = [_m("A", 2.0, 1e9), _m("B", 8.0, 1e9), _m("C", 5.0, 1e9)]
        assert [r.ticker for r in select(rows, "gainers", "all", 3)] == ["B", "C", "A"]

    def test_losers_are_lowest_first(self) -> None:
        rows = [_m("A", -2.0, 1e9), _m("B", -8.0, 1e9), _m("C", 5.0, 1e9)]
        assert [r.ticker for r in select(rows, "losers", "all", 3)] == ["B", "A", "C"]

    def test_the_sp500_filter_keeps_only_members(self) -> None:
        rows = [_m("AAPL", 1.0, 1e9, sp=True), _m("RANDO", 9.0, 1e9)]
        assert [r.ticker for r in select(rows, "gainers", "sp500", 10)] == ["AAPL"]

    def test_the_limit_is_respected(self) -> None:
        rows = [_m(f"T{i}", float(i), 1e9) for i in range(40)]
        assert len(select(rows, "gainers", "all", 5)) == 5
