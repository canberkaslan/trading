"""Offline tests for the survivor-safe price loader.

No network anywhere: both provider functions are injected, so every rule below
is exercised against hand-built issuer histories. The shapes they encode are
the ones measured against the live keys on 2026-09-07 — a ticker recycled onto
a new issuer (INFO: IHS Markit → an ETF), a ticker whose issuer is unreadable
on the probe date (TWTR/SIVB, gone from the current reference), and a provider
window that silently starts later than the range requested.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backtest.survivor_prices import (
    Coverage,
    IssuerRef,
    IssuerSpan,
    ProviderWindowError,
    bisect_change_day,
    drop_outside_span,
    load_ticker,
    load_universe,
    spans_from_refs,
    usable_span,
)
from tradingagents_us.backtest.exit_paths import Bar

OLD_CIK = "0001316360"  # IHS Markit
NEW_CIK = "0009999999"  # whoever holds the four letters now


def _bars(start: date, days: int, price: float = 100.0) -> list[Bar]:
    return [
        Bar(day=start + timedelta(days=i), open=price, high=price + 1, low=price - 1, close=price)
        for i in range(days)
    ]


def _issuers(schedule: dict[date, tuple[str | None, str | None]]):
    """A reference reader that answers from a step function of (cik, name).

    Returns the most recent entry at or before the probe date, which is how a
    point-in-time reference behaves; days before the first entry are unknown.
    """

    def read(ticker: str, day: date) -> IssuerRef:
        applicable = [d for d in schedule if d <= day]
        if not applicable:
            return IssuerRef(ticker, day, None, None)
        cik, name = schedule[max(applicable)]
        return IssuerRef(ticker, day, cik, name)

    return read


# --------------------------------------------------------------------------
# same_issuer_as / spans
# --------------------------------------------------------------------------


def test_two_unknown_issuers_are_not_the_same_issuer():
    """The one comparison that must not default to True.

    `cik == cik` would call two absences of evidence a match, and a match is
    what licences the whole span — so an unreadable ticker would silently pass
    the recycling guard rather than being refused by it.
    """
    a = IssuerRef("TWTR", date(2021, 9, 8), None, None)
    b = IssuerRef("TWTR", date(2022, 10, 27), None, None)

    assert not a.same_issuer_as(b)
    assert not a.same_issuer_as(a)


def test_known_issuer_matches_itself_and_not_a_different_cik():
    a = IssuerRef("INFO", date(2021, 6, 1), OLD_CIK, "IHS Markit Ltd.")
    b = IssuerRef("INFO", date(2021, 12, 1), OLD_CIK, "IHS Markit Ltd. Common Shares")
    c = IssuerRef("INFO", date(2025, 6, 1), NEW_CIK, "Harbor PanAgora ETF")

    assert a.same_issuer_as(b), "a benign name edit is not a change of issuer"
    assert not a.same_issuer_as(c)


def test_spans_merge_one_issuer_and_split_at_the_handover():
    refs = [
        IssuerRef("INFO", date(2021, 9, 8), OLD_CIK, "IHS Markit"),
        IssuerRef("INFO", date(2022, 1, 1), OLD_CIK, "IHS Markit"),
        IssuerRef("INFO", date(2025, 6, 1), NEW_CIK, "Harbor PanAgora ETF"),
    ]

    spans = spans_from_refs(refs)

    assert len(spans) == 2
    assert spans[0] == IssuerSpan(OLD_CIK, "IHS Markit", date(2021, 9, 8), date(2022, 1, 1))
    assert spans[1].cik == NEW_CIK


def test_consecutive_unknowns_stay_separate_spans():
    """Two unknowns in a row are two absences, not one continuous issuer."""
    refs = [
        IssuerRef("SIVB", date(2021, 9, 8), None, None),
        IssuerRef("SIVB", date(2023, 3, 1), None, None),
    ]

    spans = spans_from_refs(refs)

    assert len(spans) == 2
    assert all(not s.known for s in spans)


def test_spans_are_built_in_date_order_regardless_of_probe_order():
    """Bisection appends probes out of order; the spans must not inherit that."""
    refs = [
        IssuerRef("INFO", date(2025, 6, 1), NEW_CIK, "ETF"),
        IssuerRef("INFO", date(2021, 9, 8), OLD_CIK, "IHS Markit"),
        IssuerRef("INFO", date(2022, 1, 1), OLD_CIK, "IHS Markit"),
    ]

    spans = spans_from_refs(refs)

    assert [s.start for s in spans] == sorted(s.start for s in spans)
    assert spans[0].cik == OLD_CIK


# --------------------------------------------------------------------------
# usable_span / drop_outside_span
# --------------------------------------------------------------------------


def test_usable_span_covers_only_the_intended_issuer():
    spans = (
        IssuerSpan(OLD_CIK, "IHS Markit", date(2021, 9, 8), date(2022, 2, 25)),
        IssuerSpan(NEW_CIK, "ETF", date(2024, 10, 9), date(2025, 12, 30)),
    )

    assert usable_span(spans, OLD_CIK) == (date(2021, 9, 8), date(2022, 2, 25))
    assert usable_span(spans, NEW_CIK) == (date(2024, 10, 9), date(2025, 12, 30))


def test_unknown_intended_issuer_yields_no_span_rather_than_everything():
    """The failure that would import a stranger's prices, if it defaulted open."""
    spans = (
        IssuerSpan(None, None, date(2021, 9, 8), date(2021, 9, 8)),
        IssuerSpan(NEW_CIK, "ETF", date(2024, 10, 9), date(2025, 12, 30)),
    )

    assert usable_span(spans, None) is None
    assert drop_outside_span(_bars(date(2024, 10, 9), 30), None) == []


def test_drop_outside_span_keeps_the_inclusive_edges():
    bars = _bars(date(2021, 9, 8), 10)
    span = (date(2021, 9, 10), date(2021, 9, 14))

    kept = drop_outside_span(bars, span)

    assert [b.day for b in kept] == [date(2021, 9, 10) + timedelta(days=i) for i in range(5)]


# --------------------------------------------------------------------------
# bisection
# --------------------------------------------------------------------------


def test_bisect_narrows_the_handover_to_adjacent_days():
    handover = date(2022, 2, 26)
    read = _issuers({date(2000, 1, 1): (OLD_CIK, "IHS Markit"), handover: (NEW_CIK, "ETF")})
    left = read("INFO", date(2021, 9, 8))
    right = read("INFO", date(2025, 12, 30))

    lo, hi = bisect_change_day("INFO", left, right, read)

    assert (hi.day - lo.day) <= timedelta(days=1)
    assert lo.cik == OLD_CIK and hi.cik == NEW_CIK
    assert lo.day < handover <= hi.day


def test_an_unused_gap_is_left_unattributed_but_costs_no_proven_day():
    """INFO's real shape: IHS Markit, then nobody, then an ETF.

    The gap must stay attributed to neither side — and searching outward from
    each end rather than hunting one boundary is what keeps the stretch of real
    IHS Markit history *before* the gap, which a single converging search would
    abandon the moment it landed on an unknown midpoint.
    """
    dark_from, relisted = date(2022, 2, 26), date(2024, 10, 9)
    read = _issuers(
        {
            date(2000, 1, 1): (OLD_CIK, "IHS Markit"),
            dark_from: (None, None),
            relisted: (NEW_CIK, "ETF"),
        }
    )
    left = read("INFO", date(2021, 9, 8))
    right = read("INFO", date(2025, 12, 30))

    lo, hi = bisect_change_day("INFO", left, right, read)

    assert lo.cik == OLD_CIK and hi.cik == NEW_CIK
    assert lo.day < dark_from, "the old issuer keeps the days it can be proven for"
    assert (dark_from - lo.day) < timedelta(days=30), "and nearly all of them"
    assert hi.day >= relisted
    assert (hi.day - lo.day) > timedelta(days=1), "the dark stretch stays unattributed"


def test_an_unreadable_far_end_still_extends_the_readable_near_end():
    """SBNY's shape, and the bug it caught.

    Polygon's *current* row for SBNY carries no CIK, while its point-in-time
    rows prove Signature Bank right up to 2023-03-13. Requiring both ends to be
    known before extending anything left that provable stretch as a single day.
    """
    seized = date(2023, 3, 14)
    read = _issuers(
        {date(2000, 1, 1): (OLD_CIK, "Signature Bank"), seized: (None, "SIGNATURE BANK (NY)")}
    )

    assert not read("SBNY", date(2025, 12, 31)).known, "fixture must have an unreadable far end"

    bars, coverage = load_ticker(
        "SBNY",
        date(2021, 9, 3),
        date(2025, 12, 31),
        read_issuer_fn=read,
        read_bars_fn=lambda t, s, e: _bars(date(2021, 9, 8), 1200),
    )

    assert coverage.intended_cik == OLD_CIK
    assert coverage.bars > 500, "the proven stretch must survive, not collapse to one day"
    assert all(b.day < seized for b in bars)


def test_bisect_skips_probing_an_unknown_anchor_entirely():
    """Nothing can be proven equal to an unknown, so spending calls on it is waste."""
    calls: list[date] = []

    def read(ticker: str, day: date) -> IssuerRef:
        calls.append(day)
        return IssuerRef(ticker, day, None, None)

    unknown_left = IssuerRef("X", date(2021, 9, 8), None, None)
    unknown_right = IssuerRef("X", date(2025, 12, 31), None, None)

    bisect_change_day("X", unknown_left, unknown_right, read)

    assert calls == []


def test_bisect_is_bounded_even_when_the_reader_never_agrees():
    """Two outward searches, so the call budget is 2×max_probes and not open."""
    calls: list[date] = []

    def hostile(ticker: str, day: date) -> IssuerRef:
        calls.append(day)
        return IssuerRef(ticker, day, f"cik-{day.isoformat()}", None)

    left = IssuerRef("X", date(2018, 1, 1), OLD_CIK, None)
    right = IssuerRef("X", date(2025, 12, 31), NEW_CIK, None)

    bisect_change_day("X", left, right, hostile, max_probes=12)

    assert len(calls) <= 24


# --------------------------------------------------------------------------
# load_ticker — the whole rule set together
# --------------------------------------------------------------------------


def test_recycled_ticker_keeps_only_the_intended_issuers_bars():
    """INFO in miniature: IHS Markit's bars kept, the ETF's dropped."""
    old = _bars(date(2021, 9, 8), 120)
    new = _bars(date(2024, 10, 9), 60, price=50.0)
    read = _issuers(
        {date(2000, 1, 1): (OLD_CIK, "IHS Markit"), date(2024, 10, 9): (NEW_CIK, "ETF")}
    )

    bars, coverage = load_ticker(
        "INFO",
        date(2021, 1, 1),
        date(2025, 12, 31),
        read_issuer_fn=read,
        read_bars_fn=lambda t, s, e: old + new,
    )

    assert coverage.intended_cik == OLD_CIK
    assert coverage.dropped_recycled == len(new)
    assert all(b.day <= date(2024, 10, 9) for b in bars)
    assert "changed hands" in coverage.note


def test_unreadable_issuer_drops_every_bar_and_says_why():
    """SIVB's shape: bars exist, identity does not, so nothing is usable."""
    read = _issuers({})  # nothing known on any date

    bars, coverage = load_ticker(
        "SIVB",
        date(2021, 9, 3),
        date(2023, 3, 10),
        read_issuer_fn=read,
        read_bars_fn=lambda t, s, e: _bars(date(2021, 9, 8), 200),
    )

    assert bars == []
    assert coverage.usable is False
    assert coverage.dropped_recycled == 200
    assert "issuer unknown" in coverage.note


def test_silent_provider_truncation_is_reported_not_absorbed():
    """The 2018-request-returns-2021-data case, which arrives as HTTP 200."""
    read = _issuers({date(2000, 1, 1): (OLD_CIK, "Twitter, Inc.")})

    _, coverage = load_ticker(
        "TWTR",
        date(2018, 1, 1),
        date(2022, 10, 27),
        read_issuer_fn=read,
        read_bars_fn=lambda t, s, e: _bars(date(2021, 9, 8), 288),
    )

    assert coverage.truncated_start == date(2021, 9, 8) - date(2018, 1, 1)
    assert coverage.truncated_start.days > 1300
    assert "short 1346d" in coverage.summary()


def test_a_range_fully_inside_the_window_reports_no_truncation():
    read = _issuers({date(2000, 1, 1): (OLD_CIK, "Apple Inc.")})

    _, coverage = load_ticker(
        "AAPL",
        date(2022, 1, 3),
        date(2022, 3, 1),
        read_issuer_fn=read,
        read_bars_fn=lambda t, s, e: _bars(date(2022, 1, 3), 40),
    )

    assert coverage.truncated_start is None
    assert "short" not in coverage.summary()


def test_a_refused_range_is_not_reported_as_an_empty_market():
    def refuse(ticker: str, start: date, end: date) -> list[Bar]:
        raise ProviderWindowError("range outside plan window")

    bars, coverage = load_ticker(
        "AAPL",
        date(2019, 1, 1),
        date(2019, 6, 1),
        read_issuer_fn=_issuers({}),
        read_bars_fn=refuse,
    )

    assert bars == []
    assert "plan window" in coverage.note
    assert coverage.spans == ()


def test_probes_are_clamped_to_the_bars_the_provider_returned():
    """A reference lookup before the first bar describes a priceless period.

    Probing the requested start would report an issuer change for a stretch the
    run has no prices for, which reads as a data problem where there is none.
    """
    probed: list[date] = []

    def read(ticker: str, day: date) -> IssuerRef:
        probed.append(day)
        return IssuerRef(ticker, day, OLD_CIK, "Twitter, Inc.")

    load_ticker(
        "TWTR",
        date(2018, 1, 1),
        date(2022, 10, 27),
        read_issuer_fn=read,
        read_bars_fn=lambda t, s, e: _bars(date(2021, 9, 8), 288),
    )

    assert min(probed) >= date(2021, 9, 8)
    assert max(probed) <= date(2021, 9, 8) + timedelta(days=287)


def test_intended_on_selects_the_later_issuer_when_asked():
    old = _bars(date(2021, 9, 8), 120)
    new = _bars(date(2024, 10, 9), 60, price=50.0)
    read = _issuers(
        {date(2000, 1, 1): (OLD_CIK, "IHS Markit"), date(2024, 10, 9): (NEW_CIK, "ETF")}
    )

    bars, coverage = load_ticker(
        "INFO",
        date(2021, 1, 1),
        date(2025, 12, 31),
        intended_on=date(2025, 1, 1),
        read_issuer_fn=read,
        read_bars_fn=lambda t, s, e: old + new,
    )

    assert coverage.intended_cik == NEW_CIK
    assert all(b.day >= date(2024, 10, 9) for b in bars)


# --------------------------------------------------------------------------
# load_universe
# --------------------------------------------------------------------------


def test_universe_keeps_a_coverage_row_for_every_unpriceable_ticker():
    """That count is the survivorship measurement — dropping it hides the result."""
    read = _issuers({date(2000, 1, 1): (OLD_CIK, "known")})

    def bars_for(ticker: str, start: date, end: date) -> list[Bar]:
        return _bars(date(2021, 9, 8), 50) if ticker == "AAPL" else []

    prices, report = load_universe(
        ["AAPL", "TWTR", "SIVB"],
        date(2021, 9, 3),
        date(2025, 12, 31),
        read_issuer_fn=read,
        read_bars_fn=bars_for,
    )

    assert set(prices) == {"AAPL"}
    assert [c.ticker for c in report] == ["AAPL", "TWTR", "SIVB"]
    assert sum(1 for c in report if not c.usable) == 2


def test_coverage_summary_names_the_reason_when_nothing_is_usable():
    coverage = Coverage(
        ticker="SIVB",
        requested_start=date(2021, 9, 3),
        requested_end=date(2023, 3, 10),
        covered_start=None,
        covered_end=None,
        bars=0,
        dropped_recycled=200,
        spans=(),
        intended_cik=None,
        note="issuer unknown on 2021-09-08 — no span can be attributed",
    )

    assert "no usable bars" in coverage.summary()
    assert "issuer unknown" in coverage.summary()


def test_bar_rejects_an_inverted_high_low():
    """The upstream invariant this module's Bars must keep satisfying."""
    with pytest.raises(ValueError, match="below low"):
        Bar(day=date(2021, 9, 8), open=10.0, high=9.0, low=11.0, close=10.0)
