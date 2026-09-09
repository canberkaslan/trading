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

Two safety properties are structural rather than conventional:

- A stop only ever ratchets IN FAVOUR of the position. `atr_trailing_stop`
  guarantees it with a max(), and `plan_actions` refuses to emit a ratchet that
  would lower a long's stop even if it somehow arrived. Widening a stop is the
  one edit that turns a risk control into a loss amplifier.
- Nothing here opens anything. A `TimeExit` closes a position it was given; it
  cannot size, cannot reverse, and cannot act on a ticker with no position.
"""

from __future__ import annotations

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

#: ATR multiple for the trailing stop. 3.0 is the `atr_trailing_stop` default
#: and is deliberately loose: this stop exists to end a broken trade, not to
#: scalp a wick.
DEFAULT_ATR_MULT = 3.0


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
    #: calendar so holidays and halts cannot inflate it.
    bars_held: int
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
    "insufficient_bars",
    "stop_would_widen",
    "stop_unchanged",
    "non_positive_price",
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
        if atr is None:
            need = config.atr_period + 1
            skips.append(Skip(pos.ticker, "insufficient_bars", f"have={len(bars)} need={need}"))
            continue

        if pos.current_stop is None or pos.stop_order_id is None:
            if not config.backfill_missing_stops or pos.naked_quantity <= 0:
                # Unprotected is the loudest thing this pass can find, but
                # placing a stop is an ORDER. Reported unless explicitly asked.
                skips.append(
                    Skip(
                        pos.ticker,
                        "no_stop_order",
                        f"unprotected, atr={atr:.2f}, price={pos.current_price:.2f}",
                    )
                )
                continue

            # Seeded from the CURRENT price, not from entry: a name that has
            # doubled since entry would otherwise get a stop far below anything
            # it has traded at recently, which protects nothing. Floored at zero
            # so a violently wide ATR cannot produce a negative stop.
            level = max(0.01, pos.current_price - atr * config.atr_mult)
            if level >= pos.current_price:
                detail = f"level {level:.2f} >= price {pos.current_price:.2f}"
                skips.append(Skip(pos.ticker, "stop_would_widen", detail))
                continue
            actions.append(PlaceStop(pos.ticker, pos.naked_quantity, level, atr))
            continue

        candidate = atr_trailing_stop(
            current_close=pos.current_price,
            atr=atr,
            previous_stop=pos.current_stop,
            atr_mult=config.atr_mult,
            side="LONG",
        )

        if candidate < pos.current_stop:
            # atr_trailing_stop's max() makes this unreachable today. It is
            # asserted anyway because the day someone adds a SHORT branch or a
            # different stop source, a widened stop must fail loudly here
            # rather than quietly become an order.
            detail = f"{pos.current_stop:.2f} -> {candidate:.2f}"
            skips.append(Skip(pos.ticker, "stop_would_widen", detail))
            continue

        gain = (candidate - pos.current_stop) / pos.current_stop
        if gain < config.min_ratchet_pct:
            detail = f"+{gain:.3%} below {config.min_ratchet_pct:.1%}"
            skips.append(Skip(pos.ticker, "stop_unchanged", detail))
            continue

        actions.append(
            RatchetStop(pos.ticker, pos.stop_order_id, pos.current_stop, candidate, atr)
        )

    return actions, skips
