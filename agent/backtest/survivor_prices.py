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
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from tradingagents_us.backtest.exit_paths import Bar

#: A class-share ticker in the hyphen convention: `BF-B`, `BRK-B`, `BF-A`.
#: Deliberately anchored and narrow — it must not match a warrant (`FOO-WT`),
#: a unit, or anything else where a dot form would be a guess rather than the
#: same security written another way.
_CLASS_SHARE_HYPHEN = re.compile(r"^([A-Z]+)-([A-Z]{1,2})$")

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
    #: The symbol actually sent to the provider, which is not always the one
    #: the caller asked for — see `polygon_candidates`. `None` means no spelling
    #: was recognised, which is a different failure from a company whose history
    #: ended, and the two must not be counted together.
    provider_symbol: str | None = None

    @property
    def bars_offered(self) -> int:
        """What the provider returned, before the issuer guard took any away."""
        return self.bars + self.dropped_recycled

    @property
    def unlisted(self) -> bool:
        """The provider knows nothing about this symbol under any spelling tried.

        Kept apart from `usable` because the survivorship measurement is a count
        of names a survivor-only universe *loses*, and a symbol we failed to
        spell was never lost by the market. Folding the two together overstates
        exactly the number this module exists to report honestly.

        Both halves are required. A symbol that returns bars but no readable
        issuer — SIVB's shape, where the guard drops all 200 — *is* listed; what
        failed there is identity, which is a real gap in the provider's history
        and belongs in the survivorship count. Only an answer that is empty on
        both channels means we asked for something that was never there.
        """
        return self.provider_symbol is None and self.bars_offered == 0

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
        if self.provider_symbol and self.provider_symbol != self.ticker:
            line += f" (as {self.provider_symbol})"
        gap = self.truncated_start
        if gap is not None and gap > timedelta(days=5):
            line += f" (requested {self.requested_start}, short {gap.days}d)"
        if self.dropped_recycled:
            line += f" · dropped {self.dropped_recycled} recycled"
        return line


# --------------------------------------------------------------------------
# Pure core. Everything below the fetchers is offline-testable.
# --------------------------------------------------------------------------


def polygon_candidates(ticker: str) -> tuple[str, ...]:
    """The spellings of `ticker` worth asking Polygon for, best guess first.

    The membership layer normalises class shares to the hyphen form, because
    that is what Wikipedia's tables and yfinance use — `sp500_history` does the
    `.`→`-` rewrite in two places. Polygon uses the dot form, and the two are
    the same security written two ways, not two securities.

    What makes this worth a dedicated step rather than a blind rewrite is how
    Polygon says no, measured on 2026-09-08:

      * The **aggregates** endpoint does not say no at all. `BF-B` and `MRSH`
        both come back **HTTP 200, `status: OK`, zero results** — byte-identical
        to each other and to a company whose history genuinely ran out. A loader
        that only asks for bars books a spelling mistake as a delisting, in the
        one report whose entire purpose is counting delistings.
      * The **reference** endpoint does distinguish them, and not in the shape
        the rest of this module expects: `BF-B` and `BRK-B` are **HTTP 400**
        (unparseable symbol), while `MRSH` is a clean **404** (a symbol nobody
        held). `BF.B` and `BRK.B` return real CIKs and 1083 bars.

    So the dot form is not a cosmetic preference — it is the difference between
    1083 bars and silence.

    The list is short on purpose. Only a trailing one-or-two-letter share class
    is rewritten, so a warrant or unit suffix is left alone rather than guessed
    at, and the original spelling is always tried first so a ticker that
    legitimately contains a hyphen is never overridden by the rewrite.
    """
    candidates = [ticker]
    match = _CLASS_SHARE_HYPHEN.match(ticker)
    if match:
        candidates.append(f"{match.group(1)}.{match.group(2)}")
    return tuple(candidates)


def resolve_symbol(
    ticker: str,
    day: date,
    read_issuer_fn: Callable[[str, date], IssuerRef],
) -> str | None:
    """The spelling the provider positively recognises on `day`, or None.

    "Recognises" means it named an issuer — the same bar `read_issuer` sets
    everywhere else in this module. A null-CIK answer is not recognition, which
    is the whole point: that is precisely what a misspelled ticker returns.

    Returning None rather than falling back to the original spelling here is
    safe because the caller keeps using the original anyway; None is a *label*
    on that outcome, not a refusal to proceed. So this can only ever add
    coverage to a run, never take any away — worth stating plainly, since a
    change to the loader that silently dropped names would corrupt the same
    measurement it is meant to fix.
    """
    for candidate in polygon_candidates(ticker):
        if read_issuer_fn(candidate, day).known:
            return candidate
    return None


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
    # 404 is "a symbol, nobody held it on this date"; 400 is "not a symbol I can
    # parse at all", which is what the hyphen form of a class share returns
    # (`BF-B`, `BRK-B`, measured 2026-09-08). Both are the same answer to the
    # only question asked here — no issuer — and neither is a transport failure.
    # Letting the 400 raise meant one misspelled name aborted an entire
    # 500-ticker universe run instead of costing that one row.
    if resp.status_code in (400, 404):
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

    # A ticker with only one plausible spelling has nothing to resolve, and
    # probing it here would break a rule this module already keeps: probes are
    # clamped to the days the provider returned bars for, so a lookup before the
    # first bar cannot invent an issuer change. The one case that must be
    # resolved up front is a class share, where the spelling decides which
    # symbol the bars are even fetched under. So the extra reference call is
    # spent only on hyphenated tickers — everything else pays nothing.
    candidates = polygon_candidates(ticker)
    symbol = (
        resolve_symbol(ticker, anchor, read_issuer_fn) if len(candidates) > 1 else ticker
    )
    query = symbol or ticker

    def _issuer(_ticker: str, day: date) -> IssuerRef:
        # Probes go out under the resolved spelling but come back labelled with
        # the ticker the caller asked for, so spans and notes stay in the
        # caller's vocabulary.
        ref = read_issuer_fn(query, day)
        return IssuerRef(ticker, ref.day, ref.cik, ref.name)

    try:
        bars = read_bars_fn(query, start, end)
    except ProviderWindowError as exc:
        return [], Coverage(
            ticker, start, end, None, None, 0, 0, (), None,
            note=str(exc), provider_symbol=symbol,
        )

    if not bars:
        # Nothing came back, so there are no bars for a probe to be clamped to
        # and the lookup is free to happen now — which is the only moment it can
        # tell the two empty answers apart. "Delisted" and "we misspelled it"
        # are the same empty list from here, and only one of them belongs in the
        # survivorship count.
        if symbol is not None and not read_issuer_fn(query, anchor).known:
            symbol = None
        note = (
            "provider does not list this symbol"
            if symbol is None
            else "provider returned no bars"
        )
        return [], Coverage(
            ticker, start, end, None, None, 0, 0, (), None,
            note=note, provider_symbol=symbol,
        )

    # Probe the span the provider actually covered, not the one requested: a
    # reference lookup before the first bar describes a period this run has no
    # prices for, and would put a phantom issuer change in the report.
    first_day, last_day = bars[0].day, bars[-1].day
    probe_anchor = min(max(anchor, first_day), last_day)
    refs = [_issuer(ticker, first_day), _issuer(ticker, last_day)]
    if probe_anchor not in (first_day, last_day):
        refs.append(_issuer(ticker, probe_anchor))

    left = min(refs, key=lambda r: r.day)
    right = max(refs, key=lambda r: r.day)
    if not left.same_issuer_as(right):
        # Deliberately *not* `left.known and right.known`: an unreadable far end
        # is the commonest shape here, not a rare one — Polygon's current row for
        # SBNY carries no CIK at all — and requiring both ends to be known meant
        # the near end never got extended. Signature Bank is provable through
        # 2023-03-13 and the guard was keeping a single day of it.
        bounded_left, bounded_right = bisect_change_day(
            ticker, left, right, _issuer
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
        provider_symbol=symbol,
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
