"""What to do with a position AFTER it is open.

Until now: nothing. `atr_trailing_stop` and `time_exit` have sat in
`stop_loss.py` since the first commit with zero callers, so a position entered
by the daily run was never touched again — its broker-side stop stayed at the
level set at entry and no position was ever closed on age. The only exits the
realized ledger records are 5 take-profits, 3 stops, ONE agent sell decision,
and 24 kill-switch flattens.

That is not a detail; it is most of why the book is frozen. The universe is 11
names and the per-name cap is 10% of equity, so once ~10 positions are open the
book is at the cap on every name it knows about. `check_limits` then trims every
subsequent buy to zero — 167 of the recorded refusals are exactly that — and
with no exit path nothing ever releases the capital that would let a new thesis
be expressed. The measured Sharpe is therefore a week of entries followed by
three weeks of holding, not a strategy.

This module is the missing half, kept PURE so it can be tested without a broker:
it reads positions and bars and returns actions. Executing them is the runner's
job (`scripts/manage_positions.py`).

Three safety properties are structural rather than conventional:

- A stop only ever ratchets IN FAVOUR of the position. `atr_trailing_stop`
  guarantees it with a max(), and `plan_actions` refuses to emit a ratchet that
  would lower a long's stop even if it somehow arrived. Widening a stop is the
  one edit that turns a risk control into a loss amplifier.
- Nothing here opens anything. A `TimeExit` closes a position it was given; it
  cannot size, cannot reverse, and cannot act on a ticker with no position.
- No stop is set at or near the market. A sell stop at the last price fires on
  the first print of the next session, so it is a close that no close logic
  ever decided. One bad input (a flat tape, bars on the wrong scale) would put
  every stop in the book there in one pass; see MIN_ATR_FRACTION.
- No stop is set off a mark the bars do not back. The distance to the market
  is measured from the mark, so it cannot catch a mark that is itself wrong;
  see MAX_MARK_TO_CLOSE. Inside that band, a stop trails the lower of the
  mark and the last close, so a mark that is off upward by less than the band
  still cannot put a stop above the last price the bars recorded.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from tradingagents_us.risk.stop_loss import atr_trailing_stop, time_exit

#: Wilder's default. 14 bars is also the shortest window that survives a single
#: gap day without the ATR halving, which matters on a 3x multiplier.
ATR_PERIOD = 14

#: Bars a position may sit without going anywhere before it is closed on age.
#: 20 trading days ~ one month, which is the short end of the 1-18mo horizons
#: the agents actually write.
DEFAULT_MAX_BARS = 20

#: "Went nowhere" means inside this band. Wider than noise, narrower than any
#: thesis worth holding capital for.
DEFAULT_FLAT_PNL_PCT = 0.03

#: Alpaca refuses a stop price with a sub-penny increment on any stock priced
#: at or above $1.00 ("invalid stop_price … does not fulfill minimum pricing
#: criteria", HTTP 422). The ATR arithmetic produces prices like 308.10663…,
#: so the level is rounded here rather than at the transport: what price gets
#: placed is a decision this module makes, and rounding it downstream would
#: mean the number this module reasons about is not the number that reaches
#: the broker.
#:
#: DOWN, not nearest: rounding a long's protective stop up moves it closer to
#: the market, which is the one direction that must never happen by accident.
PRICE_DECIMALS = 2

#: The exit budget: one pass closes at most this many positions, and never more
#: than this fraction of the positions it examined (see `exit_budget`). The pass
#: acts on whatever it is handed, so without a cap one bad input — a bar cache
#: that ages every name at once, a mark that reads every position as flat —
#: liquidates the book in a single run, and nobody sees it until it is done.
#: With it, the same fault costs at most three names before a person can look.
#: The budget is a trade date's, not a process's: a pass run again the same day
#: (a missed run replayed at boot, a re-run by hand) spends what the earlier
#: ones left of it, counted off the exits they stamped (`plan_actions`).
#:
#: It counts time exits, the only closes this module plans. A stop moved to the
#: market is a close as well, and one it never counts: a flat tape or bars on
#: the wrong scale put every stop there in the same pass. Rationing stops is not
#: the answer, since a deferred stop is protection left unmaintained exactly when
#: the book looks wrong. The stop logic has to refuse such a level outright, and
#: this cap is the whole story only while it does.
DEFAULT_MAX_CLOSES_PER_RUN = 3
DEFAULT_MAX_CLOSE_FRACTION = 0.25

#: ATR multiple for the trailing stop. 3.0 is the `atr_trailing_stop` default
#: and is deliberately loose: this stop exists to end a broken trade, not to
#: scalp a wick.
DEFAULT_ATR_MULT = 3.0

#: A floor under any real daily ATR, as a fraction of the price. The calmest
#: name in the universe is SPY, whose 14-day ATR ran at roughly 0.4% of its
#: price in the quietest stretch of the last decade (2017); the single names sit
#: well above it. 0.25% leaves margin under that. An ATR below it is not a
#: measurement of the name: it is a flat tape (every bar h=l=c), a stale cache of
#: identical bars, or bars on another scale than the mark (1/10 after an
#: unadjusted reverse split).
#:
#: Its use is to bound how close to the market a stop may go. A stop sits
#: `atr_mult` ATRs under the price, so no real series puts one closer than
#: `atr_mult` x this: 0.75% at the default 3.0, against the ~1.2% a 3-ATR stop on
#: SPY sat at in 2017. A stop closer than that is a bad input showing through,
#: and it is refused rather than placed (`stop_at_market`).
MIN_ATR_FRACTION = 0.0025

#: How far the mark may sit from the last cached close before the stop logic
#: stops trusting the pair: 25% either way (log-symmetric, so 1/1.25 below),
#: widened to MARK_BAND_ATRS ATRs for a name volatile enough to move that far.
#:
#: The stop's distance is measured from the mark, and the ATR from the bars, so
#: a guard on the distance cannot see either being wrong. A doubled mark (the
#: broker's last print, doubled) puts every stop a normal distance under a
#: price that is not real, above the real market. Bars on another scale (1/10
#: after an unadjusted reverse split, or cents against a dollar mark) give an
#: ATR that is wrong by the same factor. All of them disagree with the last
#: close by far more than a session moves: the run is after the close, so the
#: last cached close is the session just ended, or the one before. A real gap
#: that large in one of this universe's names is a day not to move stops on
#: anyway, and refusing only leaves the old stop standing (or, for a
#: back-fill, the shares naked and paged) until the bars catch up.
#:
#: Inside the band a mark can still be wrong: 20% high on a name with a 2%
#: ATR passes it, and a trail from that mark sits above the last close. So
#: both stop paths measure from the lower of the two (`_reference`), and the
#: at-market floor is measured from the same. A real move above the last
#: close then waits one run for the bars; one below it is followed at once.
#:
#: 1.5 (|ln| <= 0.405: +50% / -33%), measured, not guessed. Over five years of
#: daily bars for this universe the band at 1.25 would have refused real crash
#: days: META -24.6% (2022-10-27) and UNH -22.4% (2025-04-17), and it cleared UNH
#: -19.61% (2026-01-27) by 0.005 in log terms. Those are the sessions a naked lot
#: most needs its stop. At 1.5 no real one-session move in five years is refused,
#: with the cache fresh or up to ten sessions behind (largest real |ln| 0.39),
#: while every input this guard exists for still is: a doubled mark, an
#: unadjusted 2:1 split, 1/10 scale and cents all sit at |ln| >= 0.69.
MAX_MARK_TO_CLOSE = 1.5
MARK_BAND_ATRS = 3.0

#: Sessions the bar cache may be behind the run: the session just ended and
#: the one before, or one and a holiday. The mark band's premise is that the
#: last cached close is that recent. A cache left weeks behind turns every name
#: that moved since into a mark the bars "do not back", and leaves a wrong mark
#: nothing recent to be checked against. The runner fetches fresh bars for the
#: pass first (`manage_positions --refresh-bars`); this is what happens when it
#: could not. A refusal on such bars is named `stale_bars`, not blamed on the
#: mark, and no stop is ratcheted off them. A back-fill still goes out when the
#: bars pass every other check: shares with no stop are the worse state, and it
#: is measured from the lower of mark and close, so a rise since the last bar
#: puts it lower, never higher.
MAX_BARS_BEHIND = 2

#: A close-to-close move no name in this universe makes in one session, and
#: that every split does: 2:1 halves the price. Bars on both sides of such a
#: join are on two scales. It is what a split leaves in the cache when only the
#: recent rows are fetched again adjusted (a 5-day chart view rewrites about
#: nine bars of the sixty the stop logic reads): the last close agrees with the
#: mark, so the mark band passes, and the true range across the join inflates
#: the ATR to half the price, which floors the back-fill at a cent.
SCALE_BREAK = 1.75

#: The furthest under the price a back-fill may go: half of it. A 3-ATR stop
#: that far down needs a daily ATR of a sixth of the price, which no name in
#: this universe has. A level below it is an ATR that is not this name's range,
#: and a stop there is worse than none: stop_coverage counts it as protection,
#: so the naked-book page goes quiet over shares that are naked in practice.
MIN_STOP_TO_PRICE = 0.5


def tick_round_down(price: float, decimals: int = PRICE_DECIMALS) -> float:
    """Round a long's stop DOWN to a tradeable increment.

    See PRICE_DECIMALS. Down rather than to-nearest because rounding a
    protective stop up tightens it, and a stop that creeps toward the market
    by half a cent per run is a loss the operator never chose.
    """
    factor = 10**decimals
    return math.floor(price * factor) / factor


@dataclass(frozen=True)
class Bar:
    """One daily OHLC bar. Volume is not needed here."""

    high: float
    low: float
    close: float


@dataclass(frozen=True)
class ManagedPosition:
    """A live long position plus whatever protects it right now."""

    ticker: str
    quantity: float
    avg_entry_price: float
    current_price: float
    #: Trading bars since entry, counted from the bar series rather than a
    #: calendar so holidays and halts cannot inflate it. None when the series
    #: for this name is EMPTY: the age is then unknown, and reading it as 0
    #: made a month-old position look opened today, so it silently never
    #: time-exited.
    bars_held: int | None
    #: The live broker-side stop, and the order carrying it. Both None means the
    #: position is UNPROTECTED — reported, and only fixed when explicitly asked.
    current_stop: float | None = None
    stop_order_id: str | None = None
    #: Shares `stop_coverage` calls UNAMBIGUOUSLY naked. A backfill sizes off
    #: this and never off `quantity`: sizing off the holding would re-protect
    #: shares that already have a stop, and two stops on one lot is a short.
    naked_quantity: float = 0.0
    #: Sessions (weekdays) between the last cached bar and the run, which the
    #: cache may be missing. None when the caller did not measure it.
    bars_behind: int | None = None


@dataclass(frozen=True)
class RatchetStop:
    """Move an existing stop up. Never down — see the module docstring."""

    ticker: str
    stop_order_id: str
    old_stop: float
    new_stop: float
    atr: float


@dataclass(frozen=True)
class PlaceStop:
    """Put a protective stop under shares that have none.

    Not a variant of RatchetStop: that one AMENDS an order that is already
    standing, this one creates protection where there is none, and the two fail
    in opposite directions. A ratchet that misfires leaves the old stop in place;
    a placement that misfires puts a SECOND stop on shares that already had one,
    and two stops on one lot is a short position waiting for a gap down. So this
    is only ever emitted for a quantity `stop_coverage` calls unambiguously
    naked, and the runner will not emit it at all unless asked.
    """

    ticker: str
    quantity: float
    stop_price: float
    atr: float


@dataclass(frozen=True)
class TimeExit:
    """Close a position that has gone nowhere for long enough."""

    ticker: str
    quantity: float
    bars_held: int
    pnl_pct: float


Action = RatchetStop | PlaceStop | TimeExit

#: Why a position was left alone. Surfaced rather than swallowed: "no action"
#: and "could not decide" look identical in a log that only records actions,
#: and this system has already been bitten by a guard that silently did nothing.
SkipReason = Literal[
    "exit_budget",
    "no_stop_order",
    "no_bars",
    "insufficient_bars",
    "stop_would_widen",
    "stop_unchanged",
    "non_positive_price",
    "bad_atr",
    "stop_at_market",
    "mark_disagrees_with_bars",
    "stale_bars",
]


@dataclass(frozen=True)
class Skip:
    ticker: str
    reason: SkipReason
    detail: str = ""


@dataclass(frozen=True)
class ManagementConfig:
    atr_period: int = ATR_PERIOD
    atr_mult: float = DEFAULT_ATR_MULT
    max_bars: int = DEFAULT_MAX_BARS
    flat_pnl_pct: float = DEFAULT_FLAT_PNL_PCT
    #: A ratchet smaller than this fraction of the current stop is not worth an
    #: order replacement. Alpaca rate-limits, and a stop that creeps a cent a
    #: day burns the budget for no protection.
    min_ratchet_pct: float = 0.002
    #: Emit PlaceStop for unprotected shares instead of only reporting them.
    #: Off by default: placing a stop is an order, and this module's caller
    #: decides when it is allowed to submit one.
    backfill_missing_stops: bool = False
    #: The per-pass cap on time exits; see DEFAULT_MAX_CLOSES_PER_RUN. Stop
    #: maintenance never counts against it, which is safe only while no stop is
    #: moved to the market (see there). 0 closes nothing.
    max_closes_per_run: int = DEFAULT_MAX_CLOSES_PER_RUN
    max_close_fraction: float = DEFAULT_MAX_CLOSE_FRACTION


def true_range(prev_close: float, high: float, low: float) -> float:
    """Wilder's true range for one bar."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def average_true_range(bars: Sequence[Bar], period: int = ATR_PERIOD) -> float | None:
    """Wilder-smoothed ATR, or None when there is not enough history.

    None rather than a partial average: a 3-bar ATR on a 14-bar setting is a
    different statistic wearing the same name, and it would place a stop far
    tighter than intended on exactly the names that have just started trading.
    """
    if period < 1 or len(bars) < period + 1:
        return None

    trs = [true_range(bars[i - 1].close, bars[i].high, bars[i].low) for i in range(1, len(bars))]
    if len(trs) < period:
        return None

    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


_NO_BARS_DETAIL = "bar cache holds nothing for a held name: age unknown, time exit not evaluated"


def _unfit_atr(
    pos: ManagedPosition, bars: Sequence[Bar], atr: float | None, config: ManagementConfig
) -> Skip | None:
    """Why there is no ATR a stop can be set from, or None when there is one.

    Zero is what a flat tape produces: every bar h=l=c, which is also what a
    stale cache of identical bars looks like. It put the stop exactly on the
    mark, a market sell of the position at the next open.
    """
    if atr is None:
        return _no_atr(pos, bars, config)
    if not (math.isfinite(atr) and math.isfinite(pos.current_price)):
        detail = f"atr={atr}, price={pos.current_price}: not a number a stop can be set from"
        return Skip(pos.ticker, "bad_atr", detail)
    if atr <= 0:
        detail = f"atr={atr:.4f}: a flat tape (every bar h=l=c) or a cache of identical bars"
        return Skip(pos.ticker, "bad_atr", detail)
    window = bars[-(config.atr_period + 1):]
    flat = sum(1 for b in window if not b.high > b.low)
    if flat:
        # A stale tail of identical bars decays the ATR toward zero without
        # reaching it, and the trail tightens onto the mark. No name in this
        # universe prints a full session with no range.
        detail = (
            f"{flat} of the last {len(window)} bars have no range (h<=l): a stale or "
            f"synthetic tail, not a measurement (atr {atr:.4f})"
        )
        return Skip(pos.ticker, "bad_atr", detail)
    return None


def _no_stop_basis(
    pos: ManagedPosition, bars: Sequence[Bar], atr: float | None, config: ManagementConfig
) -> Skip | None:
    """Why neither stop path may run for this position, or None when both may.

    No usable ATR, or a mark the bars do not back. Both stop paths measure from
    the mark with the ATR, so a wrong mark puts the stop above the real market,
    and a wrong scale puts it at the market or at a cent. On bars too far
    behind the run (`_stale`), the refusal is named for that.
    """
    unfit = _unfit_atr(pos, bars, atr, config)
    if unfit is None and atr is not None:
        unfit = _scale_break(pos, bars, atr) or _mark_off_bars(pos, bars, atr)
    stale = _stale(pos)
    if unfit is None or stale is None:
        return unfit
    return Skip(pos.ticker, "stale_bars", f"{stale.detail}; {unfit.reason}: {unfit.detail}")


def _stale(pos: ManagedPosition) -> Skip | None:
    """Why the bars are too far behind the run to move a stop off, or None."""
    if pos.bars_behind is None or pos.bars_behind <= MAX_BARS_BEHIND:
        return None
    detail = f"the bar cache is {pos.bars_behind} sessions behind (at most {MAX_BARS_BEHIND})"
    return Skip(pos.ticker, "stale_bars", detail)


def _scale_break(pos: ManagedPosition, bars: Sequence[Bar], atr: float) -> Skip | None:
    """Refuse an ATR measured across a join between two price scales.

    See SCALE_BREAK. Every bar the ATR reads is checked, not only the last
    `atr_period`: Wilder's smoothing carries a true range from anywhere in the
    series, and one across a join outweighs every real one for weeks.
    """
    for prev, bar in zip(bars, bars[1:], strict=False):
        if not (prev.close > 0 and bar.close > 0):
            continue
        if abs(math.log(bar.close / prev.close)) > math.log(SCALE_BREAK):
            detail = (
                f"close {prev.close:.2f} -> {bar.close:.2f} in one bar: the series is on "
                f"two scales (a split re-fetched in part), and atr {atr:.4f} spans the join"
            )
            return Skip(pos.ticker, "bad_atr", detail)
    return None


def _mark_off_bars(pos: ManagedPosition, bars: Sequence[Bar], atr: float) -> Skip | None:
    """Refuse to move a stop when the mark and the bars describe different prices.

    See MAX_MARK_TO_CLOSE. Checked after `_unfit_atr`, so there is a last close
    and a positive, finite ATR to measure with.
    """
    close = bars[-1].close
    if close > 0 and math.isfinite(close):
        limit = max(math.log(MAX_MARK_TO_CLOSE), MARK_BAND_ATRS * atr / close)
        if abs(math.log(pos.current_price / close)) <= limit:
            return None
    detail = (
        f"mark {pos.current_price:.2f} against last cached close {close:.2f} "
        f"(atr {atr:.4f}): one of them is not this name's price"
    )
    return Skip(pos.ticker, "mark_disagrees_with_bars", detail)


def _reference(pos: ManagedPosition, bars: Sequence[Bar]) -> float:
    """The price both stop paths measure from: the mark or the last close, the lower.

    See MAX_MARK_TO_CLOSE. Called once `_no_stop_basis` has passed, so there
    is a last close, and it is positive and finite.
    """
    return min(pos.current_price, bars[-1].close)


def _at_market(
    pos: ManagedPosition, level: float, atr: float, config: ManagementConfig, ref: float
) -> Skip | None:
    """Refuse a stop that would sit at or within the minimum distance of `ref`.

    `ref` is `_reference`: the mark, or the last close where that is lower. The
    distance is `atr_mult` x MIN_ATR_FRACTION of it, the closest any real
    series could put the stop. It is never less than one MIN_ATR_FRACTION, so
    an --atr-mult of 0 cannot shrink it onto the price.
    """
    distance = max(config.atr_mult, 1.0) * MIN_ATR_FRACTION
    # A wide multiplier pushes the distance past 100%, which only says "anywhere
    # under the market": floored at the cent the back-fill floors its level at.
    ceiling = max(0.01, ref * (1.0 - distance))
    if level <= ceiling and level < ref:
        return None
    detail = (
        f"level {level:.2f} is not {distance:.2%} under price {ref:.2f} "
        f"(at most {ceiling:.2f}; atr {atr:.4f}): closer than any real series puts a stop"
    )
    return Skip(pos.ticker, "stop_at_market", detail)


def _too_far(pos: ManagedPosition, level: float, atr: float, ref: float) -> Skip | None:
    """Refuse a back-fill further under `ref` than any real ATR puts one.

    See MIN_STOP_TO_PRICE. Back-fills only: a ratchet moves a standing stop
    up, so one from a stop already that low is an improvement, never a harm.
    """
    floor = ref * MIN_STOP_TO_PRICE
    if level >= floor:
        return None
    detail = (
        f"level {level:.2f} is under {MIN_STOP_TO_PRICE:.0%} of price "
        f"{ref:.2f}: atr {atr:.4f} is not this name's range"
    )
    return Skip(pos.ticker, "bad_atr", detail)


def _no_atr(pos: ManagedPosition, bars: Sequence[Bar], config: ManagementConfig) -> Skip:
    """Why a position has no ATR to set a stop from: no series, or a short one.

    With no bars at all the age was supplied from elsewhere, so the time exit
    was still evaluated; only the stop logic has nothing to work from.
    """
    if not bars:
        return Skip(pos.ticker, "no_bars", f"no ATR for a stop, age={pos.bars_held}")
    need = config.atr_period + 1
    return Skip(pos.ticker, "insufficient_bars", f"have={len(bars)} need={need}")


def exit_budget(n_positions: int, config: ManagementConfig) -> int:
    """How many positions one pass may close.

    The smaller of the count cap and the fraction of the book, but at least one
    while the count cap allows any. Without that floor a book of three names
    could never time-exit at all (25% of 3 rounds down to 0). A book that small
    is one close from flat whatever the cap says.
    """
    if config.max_closes_per_run <= 0 or n_positions <= 0:
        return 0
    by_fraction = max(1, math.floor(n_positions * config.max_close_fraction))
    return min(config.max_closes_per_run, by_fraction)


def _unusable(pos: ManagedPosition) -> Skip | None:
    """Why a position cannot be reasoned about at all, or None when it can."""
    if pos.current_price <= 0 or pos.avg_entry_price <= 0:
        return Skip(pos.ticker, "non_positive_price", f"price={pos.current_price}")
    if pos.bars_held is None:
        # A held name with no bars at all: its age cannot be read, so the time
        # exit cannot be evaluated, and there is no ATR for a stop. Said out
        # loud because the alternative, an age of 0, looks exactly like a
        # position opened today and never ages out.
        return Skip(pos.ticker, "no_bars", _NO_BARS_DETAIL)
    return None


def _time_exit_due(pos: ManagedPosition, config: ManagementConfig) -> bool:
    if _unusable(pos) is not None or pos.bars_held is None:
        return False
    pnl_pct = pos.current_price / pos.avg_entry_price - 1.0
    return time_exit(pos.bars_held, config.max_bars, pnl_pct, config.flat_pnl_pct)


def _ration_exits(
    positions: Sequence[ManagedPosition],
    config: ManagementConfig,
    exited_today: frozenset[str] = frozenset(),
    exiting: frozenset[str] = frozenset(),
) -> tuple[set[str], dict[str, Skip]]:
    """Which due time exits this pass takes, and a Skip for each one it defers.

    Most overdue first: the position that has sat longest past its window has
    the weakest claim on the capital. A deferred close is not dropped. It is
    reported, it keeps its stop maintenance this pass, and the next pass takes
    it if it is still due.

    The budget is the trade date's. It is sized off the book as it stood
    before today's exits, and each name `exited_today` has spent its share. A
    name among them that is still held and due (an exit queued for the open)
    is let through again without a second share: its close sends nothing new.

    So is a name in `exiting`, whose exit an earlier trade date queued still
    waits for an open, and it spends a share all the same: that exit sells at
    the same open as any this pass queues. The stamp carries the UTC date of
    the run that sent it, so the 22:30 run and a rerun past 00:00 UTC (after
    the overnight page, before the open) stamp different dates for one open,
    and a weekday exchange holiday puts two runs' exits on the next open too.
    Charged to neither, each run took a full budget and one open sold twice
    as many names as the budget allows.
    """
    due = sorted(
        (p for p in positions if _time_exit_due(p, config)),
        key=lambda p: (-(p.bars_held or 0), p.ticker),
    )
    book = len({p.ticker for p in positions} | exited_today)
    spent = exited_today | exiting
    left = max(0, exit_budget(book, config) - len(spent))
    fresh = [p for p in due if p.ticker not in spent]
    queued = len(exiting - exited_today)
    detail = (
        f"close deferred: {left} of {len(fresh)} due this pass "
        f"(cap {config.max_closes_per_run}, {config.max_close_fraction:.0%} "
        f"of {book} positions, {len(exited_today)} closed today"
        + (f", {queued} queued earlier for the same open" if queued else "")
        + ")"
    )
    allowed = {p.ticker for p in due if p.ticker in spent}
    allowed |= {p.ticker for p in fresh[:left]}
    deferred = {p.ticker: Skip(p.ticker, "exit_budget", detail) for p in fresh[left:]}
    return allowed, deferred


#: The defaults, as a singleton. A dataclass call in a parameter default is
#: evaluated once at import anyway — naming it says so instead of hiding it.
DEFAULT_CONFIG = ManagementConfig()


def plan_actions(
    positions: Sequence[ManagedPosition],
    bars_by_ticker: dict[str, Sequence[Bar]],
    config: ManagementConfig = DEFAULT_CONFIG,
    *,
    exited_today: frozenset[str] = frozenset(),
    exiting: frozenset[str] = frozenset(),
) -> tuple[list[Action], list[Skip]]:
    """Decide what to do with every open position.

    Pure: no clock, no network, no broker. Returns the actions to take and, for
    every position NOT acted on, why. Time exits are rationed by `exit_budget`;
    stop maintenance is not, so it must never set a stop at the market (see
    DEFAULT_MAX_CLOSES_PER_RUN). `exited_today` names the positions a time exit
    already sold, or is selling, under today's stamp: the budget is the trade
    date's, and they have spent part of it. `exiting` names those whose exit
    an earlier trade date queued still works: it sells at the open today's
    exits queue for, so they have spent part of it too.
    """
    actions: list[Action] = []
    skips: list[Skip] = []
    exits, deferred = _ration_exits(positions, config, exited_today, exiting)

    for pos in positions:
        unusable = _unusable(pos)
        if unusable is not None:
            skips.append(unusable)
            continue
        assert pos.bars_held is not None  # _unusable skipped the unknown age

        pnl_pct = pos.current_price / pos.avg_entry_price - 1.0

        # ---- Time exit ----------------------------------------------------
        # Checked first: closing a position makes ratcheting its stop moot, and
        # emitting both would have the runner replace a stop it is about to
        # cancel. A close the budget deferred is still held tonight, so it goes
        # on to the stop logic like any other position.
        if pos.ticker in exits:
            actions.append(TimeExit(pos.ticker, pos.quantity, pos.bars_held, pnl_pct))
            continue
        if pos.ticker in deferred:
            skips.append(deferred[pos.ticker])

        # Past the age window but still moving is not a skip: the thesis is
        # playing out, so the position simply carries on to the stop logic.

        # ---- Trailing stop ------------------------------------------------
        bars = bars_by_ticker.get(pos.ticker) or []
        atr = average_true_range(bars, config.atr_period)
        unfit = _no_stop_basis(pos, bars, atr, config)
        if unfit is not None:
            skips.append(unfit)
            continue
        assert atr is not None  # _no_stop_basis skipped a missing one
        ref = _reference(pos, bars)

        # ---- Back-fill the naked remainder ---------------------------------
        # Independent of the ratchet below, because a position can need BOTH: a
        # partially protected name has a stop to move up AND shares with nothing
        # under them. Gating the back-fill on "has no stop at all" is what left
        # GOOGL with 3 of 32 shares covered — the pass saw a stop, took the
        # ratchet branch, and never looked at the other 29.
        #
        # `naked_quantity` is only ever non-zero when stop_coverage called the
        # symbol actionable (naked shares, nothing indeterminate), so this can
        # never double-protect shares that already have a stop — which would
        # leave two stops on one lot, and a short position when both trigger.
        if config.backfill_missing_stops and pos.naked_quantity > 0:
            # Seeded from the CURRENT price, not from entry: a name that has
            # doubled since entry would otherwise get a stop far below anything
            # it has traded at recently, which protects nothing. The current
            # price is `ref`, the mark or the last close, the lower. Floored at
            # a cent so a violently wide ATR cannot produce a negative stop.
            level = tick_round_down(max(0.01, ref - atr * config.atr_mult))
            refused = _at_market(pos, level, atr, config, ref) or _too_far(pos, level, atr, ref)
            if refused is not None:
                # A stop at or near the market is a market sell wearing a
                # stop's clothes: it fires on the first print it sees. One far
                # under it is no protection that coverage would still count.
                skips.append(refused)
            else:
                actions.append(PlaceStop(pos.ticker, pos.naked_quantity, level, atr))

        # ---- Ratchet the stop that already stands --------------------------
        if pos.current_stop is None or pos.stop_order_id is None:
            if pos.naked_quantity <= 0 or not config.backfill_missing_stops:
                # Unprotected and not asked to fix it: report. Placing a stop is
                # an ORDER, and this module does not submit them uninvited.
                skips.append(
                    Skip(
                        pos.ticker,
                        "no_stop_order",
                        f"unprotected, atr={atr:.2f}, price={pos.current_price:.2f}",
                    )
                )
            continue

        ratchet = _ratchet(pos, pos.current_stop, pos.stop_order_id, atr, config, ref)
        if isinstance(ratchet, RatchetStop):
            actions.append(ratchet)
        else:
            skips.append(ratchet)

    return actions, skips


def _ratchet(
    pos: ManagedPosition,
    stop: float,
    stop_order_id: str,
    atr: float,
    config: ManagementConfig,
    ref: float,
) -> RatchetStop | Skip:
    """Move the standing stop up the ATR trail from `ref`, or say why it stays put."""
    stale = _stale(pos)
    if stale is not None:
        # A trail off bars that far behind is a move nothing recent backs. The
        # standing stop stays, and the runner fails the pass for it.
        return stale
    candidate = tick_round_down(
        atr_trailing_stop(
            current_close=ref,
            atr=atr,
            previous_stop=stop,
            atr_mult=config.atr_mult,
            side="LONG",
        )
    )

    if candidate < stop:
        # atr_trailing_stop's max() makes this unreachable today. It is
        # asserted anyway because the day someone adds a SHORT branch or a
        # different stop source, a widened stop must fail loudly here
        # rather than quietly become an order.
        return Skip(pos.ticker, "stop_would_widen", f"{stop:.2f} -> {candidate:.2f}")

    gain = (candidate - stop) / stop
    if gain < config.min_ratchet_pct:
        detail = f"+{gain:.3%} below {config.min_ratchet_pct:.1%}"
        return Skip(pos.ticker, "stop_unchanged", detail)

    # Checked on the move itself, after the no-op cases, so a standing stop the
    # price has fallen toward is reported as unchanged rather than as this.
    at_market = _at_market(pos, candidate, atr, config, ref)
    if at_market is not None:
        return at_market

    return RatchetStop(pos.ticker, stop_order_id, stop, candidate, atr)
