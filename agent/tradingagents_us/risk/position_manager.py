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
    "no_stop_order",
    "no_bars",
    "insufficient_bars",
    "stop_would_widen",
    "stop_unchanged",
    "non_positive_price",
    "bad_atr",
    "stop_at_market",
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
    return None


def _at_market(
    pos: ManagedPosition, level: float, atr: float, config: ManagementConfig
) -> Skip | None:
    """Refuse a stop that would sit at or within the minimum distance of the mark.

    The distance is `atr_mult` x MIN_ATR_FRACTION of the price, the closest any
    real series could put the stop. It is never less than one MIN_ATR_FRACTION,
    so an --atr-mult of 0 cannot shrink it onto the mark.
    """
    distance = max(config.atr_mult, 1.0) * MIN_ATR_FRACTION
    # A wide multiplier pushes the distance past 100%, which only says "anywhere
    # under the market": floored at the cent the back-fill floors its level at.
    ceiling = max(0.01, pos.current_price * (1.0 - distance))
    if level <= ceiling and level < pos.current_price:
        return None
    detail = (
        f"level {level:.2f} is not {distance:.2%} under price {pos.current_price:.2f} "
        f"(at most {ceiling:.2f}; atr {atr:.4f}): closer than any real series puts a stop"
    )
    return Skip(pos.ticker, "stop_at_market", detail)


def _no_atr(pos: ManagedPosition, bars: Sequence[Bar], config: ManagementConfig) -> Skip:
    """Why a position has no ATR to set a stop from: no series, or a short one.

    With no bars at all the age was supplied from elsewhere, so the time exit
    was still evaluated; only the stop logic has nothing to work from.
    """
    if not bars:
        return Skip(pos.ticker, "no_bars", f"no ATR for a stop, age={pos.bars_held}")
    need = config.atr_period + 1
    return Skip(pos.ticker, "insufficient_bars", f"have={len(bars)} need={need}")


#: The defaults, as a singleton. A dataclass call in a parameter default is
#: evaluated once at import anyway — naming it says so instead of hiding it.
DEFAULT_CONFIG = ManagementConfig()


def plan_actions(
    positions: Sequence[ManagedPosition],
    bars_by_ticker: dict[str, Sequence[Bar]],
    config: ManagementConfig = DEFAULT_CONFIG,
) -> tuple[list[Action], list[Skip]]:
    """Decide what to do with every open position.

    Pure: no clock, no network, no broker. Returns the actions to take and, for
    every position NOT acted on, why.
    """
    actions: list[Action] = []
    skips: list[Skip] = []

    for pos in positions:
        if pos.current_price <= 0 or pos.avg_entry_price <= 0:
            skips.append(Skip(pos.ticker, "non_positive_price", f"price={pos.current_price}"))
            continue

        if pos.bars_held is None:
            # A held name with no bars at all: its age cannot be read, so the
            # time exit cannot be evaluated, and there is no ATR for a stop.
            # Said out loud because the alternative, an age of 0, looks exactly
            # like a position opened today and never ages out.
            skips.append(Skip(pos.ticker, "no_bars", _NO_BARS_DETAIL))
            continue

        pnl_pct = pos.current_price / pos.avg_entry_price - 1.0

        # ---- Time exit ----------------------------------------------------
        # Checked first: closing a position makes ratcheting its stop moot, and
        # emitting both would have the runner replace a stop it is about to
        # cancel.
        if time_exit(pos.bars_held, config.max_bars, pnl_pct, config.flat_pnl_pct):
            actions.append(TimeExit(pos.ticker, pos.quantity, pos.bars_held, pnl_pct))
            continue

        # Past the age window but still moving is not a skip: the thesis is
        # playing out, so the position simply carries on to the stop logic.

        # ---- Trailing stop ------------------------------------------------
        bars = bars_by_ticker.get(pos.ticker) or []
        atr = average_true_range(bars, config.atr_period)
        unfit = _unfit_atr(pos, bars, atr, config)
        if unfit is not None:
            skips.append(unfit)
            continue
        assert atr is not None  # _unfit_atr skipped a missing one

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
            # it has traded at recently, which protects nothing. Floored at a
            # cent so a violently wide ATR cannot produce a negative stop.
            level = tick_round_down(max(0.01, pos.current_price - atr * config.atr_mult))
            at_market = _at_market(pos, level, atr, config)
            if at_market is not None:
                # A stop at or near the market is a market sell wearing a
                # stop's clothes: it fires on the first print it sees.
                skips.append(at_market)
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

        ratchet = _ratchet(pos, pos.current_stop, pos.stop_order_id, atr, config)
        if isinstance(ratchet, RatchetStop):
            actions.append(ratchet)
        else:
            skips.append(ratchet)

    return actions, skips


def _ratchet(
    pos: ManagedPosition, stop: float, stop_order_id: str, atr: float, config: ManagementConfig
) -> RatchetStop | Skip:
    """Move the standing stop up the ATR trail, or say why it stays put."""
    candidate = tick_round_down(
        atr_trailing_stop(
            current_close=pos.current_price,
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
    at_market = _at_market(pos, candidate, atr, config)
    if at_market is not None:
        return at_market

    return RatchetStop(pos.ticker, stop_order_id, stop, candidate, atr)
