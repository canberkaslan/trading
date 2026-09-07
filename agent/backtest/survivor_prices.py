"""Historical bars that stay attached to the issuer the backtest meant.

`dataflows/sp500_history.py` already reconstructs point-in-time index
membership, so a backtest can ask "who was in the S&P 500 in June 2021" and
get Twitter, SVB Financial and IHS Markit back rather than today's survivors.
That half of survivorship safety has been done for a while. The other half —
*prices* for those names — has not, and feeding the first half into
`backtest/data.py` as it stands does something worse than losing the delisted
names. It silently substitutes a different company's prices for them.

Two provider behaviours make that happen, both measured against the live keys
on 2026-09-07 rather than assumed:

  * **yfinance returns nothing for a delisted symbol, and a stranger's prices
    for a recycled one.** TWTR, ATVI, SIVB, CERN, XLNX, VMW, SPLK, PXD, CTXS,
    NLSN, ABMD, SGEN, HZNP, MXIM, ALXN, DISCA — sixteen names, zero rows each.
    But SBNY came back with 345 rows starting 2024-08-15, and INFO with 306
    starting 2024-10-09. Signature Bank was seized in March 2023 and IHS Markit
    merged into S&P Global in February 2022; those bars belong to whoever holds
    the ticker now (INFO is an ETF today). A loader that reports "SBNY: 345
    bars" has not found Signature Bank's history, and nothing in its output
    says so.

  * **Polygon has the delisted history, and silently truncates the range to
    whatever the plan covers.** A request fully outside the window is an honest
    `NOT_AUTHORIZED`. A request that *overlaps* it is not: asking TWTR for
    2018-01-01 → 2022-10-27 returns 288 bars beginning 2021-09-08, HTTP 200,
    no flag. Every backtest in this repo is configured 2018-2025, which is
    exactly the overlapping case, so the silent version is the one that fires.

So the guard here is not "drop delisted tickers". It is: **prove the symbol
meant the same issuer for the whole span before using it, and never report a
span the provider did not actually return.**

Issuer identity comes from Polygon's point-in-time reference endpoint, which
carries a CIK — the SEC's permanent issuer id, and the only field in the
response that a ticker recycle cannot preserve. Names drift for benign reasons
(re-brands, share-class edits) and comparing them would flag those as
recycling; CIKs do not. Probing is O(log n) per ticker: read the CIK at each
end of the window, and only when they disagree bisect for the day it changed.
The result is an exact boundary, not a sampled guess.

Where the evidence runs out, the answer is a refusal rather than a default.
A symbol whose CIK cannot be read at a probe point is `unknown`, and an unknown
segment is never merged into the usable span — the same rule `exit_paths` uses
for a bar that touched both bracket legs and `stop_coverage` uses for an order
status it does not recognise. Assuming continuity is how a stranger's prices
get into a backtest in the first place.

Nothing here trades, decides, or touches the box: reference lookups and daily
aggregates, both read-only.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from tradingagents_us.backtest.exit_paths import Bar

#: Polygon returns `NOT_FOUND` for a symbol nobody held on the probe date, and
#: an issuer with no CIK (an ETF, most trust structures) is equally unusable as
#: an identity. Both collapse to this, and it never compares equal to itself:
#: two unknowns are not evidence of the same issuer.
UNKNOWN_CIK = None


class ProviderWindowError(RuntimeError):
    """The provider returned nothing for a range it was asked for.

    Distinct from "this ticker has no history": raised only when the request
    itself was refused, so a plan limit cannot be read as an empty market.
    """


@dataclass(frozen=True)
class IssuerRef:
    """Who held a ticker on one day, as the provider saw it then."""

    ticker: str
    day: date
    cik: str | None
    name: str | None

    @property
    def known(self) -> bool:
        return self.cik is not None

    def same_issuer_as(self, other: IssuerRef) -> bool:
        """True only when both ends are known and identical.

        Deliberately not `self.cik == other.cik`: that makes two unknowns
        compare equal, which is the one answer the caller must never get, since
        it reads as "continuous" and licences the span.
        """
        return self.known and other.known and self.cik == other.cik


@dataclass(frozen=True)
class IssuerSpan:
    """The days a single issuer is known to have held the ticker.

    `end` is the last day proven to still be that issuer — the change itself
    happened somewhere in the gap between `end` and the next span's `start`,
    and bisection narrows that gap rather than pretending to close it.
    """

    cik: str | None
    name: str | None
    start: date
    end: date

    @property
    def known(self) -> bool:
        return self.cik is not None


@dataclass(frozen=True)
class Coverage:
    """What the provider actually gave back, next to what was asked for.

    Every field exists because leaving it out is a way to overstate the data:
    `truncated_start` because a plan window silently shortens a range,
    `dropped_recycled` because bars from another issuer would otherwise be
    counted as history, `spans` because "we could not tell" has to survive as
    far as the report.
    """

    ticker: str
    requested_start: date
    requested_end: date
    covered_start: date | None
    covered_end: date | None
    bars: int
    dropped_recycled: int
    spans: tuple[IssuerSpan, ...]
    intended_cik: str | None
    note: str = ""

    @property
    def truncated_start(self) -> timedelta | None:
        """How much of the front of the request never arrived."""
        if self.covered_start is None:
            return None
        gap = self.covered_start - self.requested_start
        return gap if gap > timedelta(days=0) else None

    @property
    def usable(self) -> bool:
        return self.bars > 0

    def summary(self) -> str:
        if not self.usable:
            return f"{self.ticker}: no usable bars ({self.note or 'no data returned'})"
        line = (
            f"{self.ticker}: {self.bars} bars {self.covered_start} → {self.covered_end}"
        )
        gap = self.truncated_start
        if gap is not None and gap > timedelta(days=5):
            line += f" (requested {self.requested_start}, short {gap.days}d)"
        if self.dropped_recycled:
            line += f" · dropped {self.dropped_recycled} recycled"
        return line


# --------------------------------------------------------------------------
# Pure core. Everything below the fetchers is offline-testable.
# --------------------------------------------------------------------------


def spans_from_refs(refs: Sequence[IssuerRef]) -> tuple[IssuerSpan, ...]:
    """Collapse chronological probe points into runs of one issuer.

    Adjacent unknowns are *not* merged into one span for the same reason
    `same_issuer_as` refuses to: an unknown carries no identity, so two of them
    in a row are two absences of evidence rather than one continuous issuer.
    """
    ordered = sorted(refs, key=lambda r: r.day)
    spans: list[IssuerSpan] = []
    for ref in ordered:
        if spans and ref.known and spans[-1].known and spans[-1].cik == ref.cik:
            last = spans[-1]
            spans[-1] = IssuerSpan(last.cik, last.name, last.start, ref.day)
            continue
        spans.append(IssuerSpan(ref.cik, ref.name, ref.day, ref.day))
    return tuple(spans)


def usable_span(
    spans: Sequence[IssuerSpan], intended_cik: str | None
) -> tuple[date, date] | None:
    """The date range belonging to the issuer the caller meant.

    An unknown `intended_cik` yields None rather than "everything": the caller
    asked for a specific company's history and the provider could not say which
    company this was, so there is no range that answers the question.
    """
    if intended_cik is None:
        return None
    matching = [s for s in spans if s.cik == intended_cik]
    if not matching:
        return None
    return min(s.start for s in matching), max(s.end for s in matching)


def drop_outside_span(bars: Sequence[Bar], span: tuple[date, date] | None) -> list[Bar]:
    """Keep only bars proven to be the intended issuer's.

    The right edge is the last *proven* day, so bars after it are dropped even
    though some of them are probably still the same company. That asymmetry is
    on purpose: an extra week of real history is worth less than one week of a
    stranger's prices, and only one of those two mistakes is silent.
    """
    if span is None:
        return []
    start, end = span
    return [b for b in bars if start <= b.day <= end]


def _extend(
    ticker: str,
    anchor: IssuerRef,
    limit: date,
    read_issuer: Callable[[str, date], IssuerRef],
    max_probes: int,
) -> IssuerRef:
    """Push `anchor` as far toward `limit` as the provider will still prove it.

    A midpoint that is *not* provably the same issuer moves the bound back,
    whether it names a different CIK or names nobody at all. Collapsing those
    two into one answer is deliberate: they are equally not-evidence that the
    anchor's issuer held the ticker that day, and the search only ever returns
    a day it has positive proof for.
    """
    if not anchor.known:
        return anchor
    proven, unproven = anchor, limit
    for _ in range(max_probes):
        if abs((unproven - proven.day).days) <= 1:
            break
        mid_day = proven.day + (unproven - proven.day) // 2
        mid = read_issuer(ticker, mid_day)
        if mid.same_issuer_as(anchor):
            proven = mid
        else:
            unproven = mid_day
    return proven


def bisect_change_day(
    ticker: str,
    known_left: IssuerRef,
    known_right: IssuerRef,
    read_issuer: Callable[[str, date], IssuerRef],
    max_probes: int = 12,
) -> tuple[IssuerRef, IssuerRef]:
    """Narrow the two probe points straddling a ticker's change of issuer.

    Returns the last day still proven to be the left issuer and the first day
    proven to be the right one. Whatever sits between them stays unattributed
    and is reported rather than split.

    The two ends are searched **independently** rather than as one boundary
    hunt, because on a recycled ticker there is usually no boundary to find:
    INFO was IHS Markit until February 2022, then nobody at all until an ETF
    listed on the same four letters in October 2024. A single search converging
    on "the day it changed" meets the unused stretch in the middle, cannot
    attribute it to either side, and — if it stops there — throws away every
    proven day it had not reached yet. Measured live, that cost INFO all but
    one of its IHS Markit bars. Searching outward from each known end instead
    keeps every day the provider will confirm, and still refuses the gap.
    """
    left = _extend(ticker, known_left, known_right.day, read_issuer, max_probes)
    right = _extend(ticker, known_right, known_left.day, read_issuer, max_probes)
    return left, right


# --------------------------------------------------------------------------
# Provider layer. Injectable so the pure core above is tested without network.
# --------------------------------------------------------------------------


def _api_key(explicit: str | None = None) -> str:
    key = explicit or os.getenv("POLYGON_API_KEY")
    if not key:
        raise ProviderWindowError("POLYGON_API_KEY is not set")
    return key


def read_issuer(ticker: str, day: date, api_key: str | None = None) -> IssuerRef:
    """Who held `ticker` on `day`, per Polygon's point-in-time reference."""
    import httpx

    resp = httpx.get(
        f"https://api.polygon.io/v3/reference/tickers/{ticker}",
        params={"date": day.isoformat(), "apiKey": _api_key(api_key)},
        timeout=30.0,
    )
    if resp.status_code == 404:
        return IssuerRef(ticker, day, UNKNOWN_CIK, None)
    resp.raise_for_status()
    body = resp.json()
    result = body.get("results") or {}
    cik = result.get("cik") or None
    return IssuerRef(ticker, day, cik, result.get("name"))


def read_bars(
    ticker: str, start: date, end: date, api_key: str | None = None
) -> list[Bar]:
    """Adjusted daily bars, oldest first.

    A `NOT_AUTHORIZED` body is raised rather than returned empty: a plan limit
    and a market with no trading are the same empty list to a caller, and only
    one of them means "go get better data".
    """
    import httpx

    resp = httpx.get(
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}",
        params={"adjusted": "true", "limit": 50000, "apiKey": _api_key(api_key)},
        timeout=60.0,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("status") == "NOT_AUTHORIZED":
        raise ProviderWindowError(
            f"{ticker} {start}→{end}: {body.get('message', 'range outside plan window')}"
        )
    bars = [
        Bar(
            day=datetime.fromtimestamp(row["t"] / 1000.0).date(),
            open=float(row["o"]),
            high=float(row["h"]),
            low=float(row["l"]),
            close=float(row["c"]),
        )
        for row in body.get("results") or []
    ]
    bars.sort(key=lambda b: b.day)
    return bars


def load_ticker(
    ticker: str,
    start: date,
    end: date,
    *,
    intended_on: date | None = None,
    read_issuer_fn: Callable[[str, date], IssuerRef] = read_issuer,
    read_bars_fn: Callable[[str, date, date], list[Bar]] = read_bars,
) -> tuple[list[Bar], Coverage]:
    """Bars for one ticker, restricted to the issuer that held it on `intended_on`.

    `intended_on` defaults to the start of the window, which is the right
    default for a point-in-time membership run: the company being backtested is
    the one that was in the index when the window opened, not whoever inherited
    its four letters afterwards.
    """
    anchor = intended_on or start
    try:
        bars = read_bars_fn(ticker, start, end)
    except ProviderWindowError as exc:
        return [], Coverage(
            ticker, start, end, None, None, 0, 0, (), None, note=str(exc)
        )

    if not bars:
        return [], Coverage(
            ticker, start, end, None, None, 0, 0, (), None, note="provider returned no bars"
        )

    # Probe the span the provider actually covered, not the one requested: a
    # reference lookup before the first bar describes a period this run has no
    # prices for, and would put a phantom issuer change in the report.
    first_day, last_day = bars[0].day, bars[-1].day
    probe_anchor = min(max(anchor, first_day), last_day)
    refs = [read_issuer_fn(ticker, first_day), read_issuer_fn(ticker, last_day)]
    if probe_anchor not in (first_day, last_day):
        refs.append(read_issuer_fn(ticker, probe_anchor))

    left = min(refs, key=lambda r: r.day)
    right = max(refs, key=lambda r: r.day)
    if not left.same_issuer_as(right):
        # Deliberately *not* `left.known and right.known`: an unreadable far end
        # is the commonest shape here, not a rare one — Polygon's current row for
        # SBNY carries no CIK at all — and requiring both ends to be known meant
        # the near end never got extended. Signature Bank is provable through
        # 2023-03-13 and the guard was keeping a single day of it.
        bounded_left, bounded_right = bisect_change_day(
            ticker, left, right, read_issuer_fn
        )
        refs = [*refs, bounded_left, bounded_right]

    spans = spans_from_refs(refs)
    intended = next((r.cik for r in refs if r.day == probe_anchor and r.known), None)
    span = usable_span(spans, intended)
    kept = drop_outside_span(bars, span)

    note = ""
    if intended is None:
        note = f"issuer unknown on {probe_anchor} — no span can be attributed"
    elif len(spans) > 1:
        note = f"ticker changed hands ({len(spans)} issuer spans)"

    return kept, Coverage(
        ticker=ticker,
        requested_start=start,
        requested_end=end,
        covered_start=kept[0].day if kept else None,
        covered_end=kept[-1].day if kept else None,
        bars=len(kept),
        dropped_recycled=len(bars) - len(kept),
        spans=spans,
        intended_cik=intended,
        note=note,
    )


def load_universe(
    tickers: Sequence[str],
    start: date,
    end: date,
    *,
    intended_on: date | None = None,
    read_issuer_fn: Callable[[str, date], IssuerRef] = read_issuer,
    read_bars_fn: Callable[[str, date, date], list[Bar]] = read_bars,
) -> tuple[dict[str, list[Bar]], list[Coverage]]:
    """Survivor-safe bars for a whole universe, plus one Coverage row each.

    Tickers with no usable bars stay out of the price dict but keep their
    Coverage row. A universe run has to be able to say how many of the names it
    asked for it could not price — that count *is* the survivorship measurement,
    and dropping the rows would hide it.
    """
    prices: dict[str, list[Bar]] = {}
    report: list[Coverage] = []
    for ticker in tickers:
        bars, coverage = load_ticker(
            ticker,
            start,
            end,
            intended_on=intended_on,
            read_issuer_fn=read_issuer_fn,
            read_bars_fn=read_bars_fn,
        )
        report.append(coverage)
        if bars:
            prices[ticker] = bars
    return prices, report
