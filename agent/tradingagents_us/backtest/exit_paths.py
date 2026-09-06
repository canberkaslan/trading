"""Replay bracket exits over historical bars so each exit path gets its own sample.

`execution/exit_quality.py` split the live realized ledger by the thing that
actually closed each position, and the split is what turned a blended
"−$29.69 per trade" into four separate claims. It also made the real problem
visible: after operator flattens are removed, the strategy's own exits are
**n=8** — four take-profit legs, three stops, one decision sell. A protective
stop losing $138 a trade across *three* trades is not a finding about stops, it
is three trades. The blended number was answering the wrong question; the split
number is answering the right question with no sample.

More live days is one way to get a sample, and it is the slow way — the box has
been dark since 2026-08-24, and even running it would take months to reach a
count that separates a bad exit rule from a bad fortnight. The other way is to
replay the exit *structure* the live system actually uses (a bracket: a stop
leg and a take-profit leg resting at the broker, plus decision sells from the
daily run) over historical bars, where the sample is however long the history
is. That is what this module does. It changes no decision, submits nothing, and
needs neither the box nor the broker.

It deliberately reuses `exit_quality`'s vocabulary — `ExitClass`,
`AttributedTrade`, `bucket_by_exit`, `strategy_bucket` — rather than computing
its own expectancy. A backtest scored on a second implementation of "win rate"
would eventually disagree with the ledger for reasons that have nothing to do
with the strategy, and the whole point is to read the two on the same axes.

## What a daily bar cannot tell you

The honest constraint, and the reason this module has a bucket most backtests
do not: **when a bar's low reaches the stop and its high reaches the take-profit,
daily data cannot say which happened first.** Both orders were resting; one of
them filled; the bar does not record the order of events inside itself.

Resolving that coin flip is the single easiest way to make a backtest lie, and
it lies by a lot: calling it a stop makes every such bar a loss, calling it a
take-profit makes every such bar a win, and on a bracket whose levels sit close
together those bars are a large share of all exits. So this module refuses to
resolve them. They are counted as `ambiguous`, kept out of the buckets, and
reported next to them — the same rule `stop_coverage` uses for an unrecognised
order status and `exit_quality` uses for a trade it never attributed. If the
ambiguous count is large relative to the resolved one, the correct conclusion
is "daily bars cannot answer this question, go get intraday data", and that is
a conclusion worth being able to reach.

Two other rules exist for the same reason — to avoid inventing money:

  * **Gaps fill at the open, not at the level.** A stop at $100 on a bar that
    opens at $92 becomes a market order and fills near $92. Modelling it at
    $100 hands the strategy $8 a share it never had, and it does so precisely
    on the worst days, which is exactly where a risk model must not be
    optimistic. Filled-at-open exits are flagged (`gapped`) so the cost of the
    real behaviour is countable rather than assumed.
  * **The entry bar is not scanned.** Its high and low include prices from
    before the entry filled, so a stop "touched" there may have been touched an
    hour before the position existed. Scanning from the next bar loses the
    genuine same-day stop-out and invents nothing; the reverse trade would
    invent exits that could not have happened.

`flatten` and `unknown` — two of `exit_quality`'s five classes — never occur
here, and that is a property of the simulation rather than of the strategy:
there is no operator to flatten a book and no broker to prune an order record.
It is worth stating out loud, because it means a backtest's strategy bucket and
a live account's strategy bucket are comparable while their *blended* ledgers
are not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from tradingagents_us.execution.exit_quality import (
    STRATEGY_CLASSES,
    AttributedTrade,
    ExitClass,
)
from tradingagents_us.execution.reconcile import ClosedTrade

#: What one simulated position can end as. The first three are real
#: `ExitClass` values so the result feeds `exit_quality`'s roll-ups unchanged;
#: the last two are simulation outcomes that are *not* exits and must never be
#: scored as one.
#:
#: `ambiguous` = a bar touched both levels and daily data cannot order them.
#: `open` = the history ran out with the position still on. Keeping them apart
#: matters: "we could not tell" and "it never closed" are different sentences,
#: and folding either into a bucket would put a number on a money screen that
#: no evidence supports.
ExitOutcome = Literal["stop", "take_profit", "decision_sell", "ambiguous", "open"]

#: Outcomes that are a real exit path, i.e. the ones that become trades.
RESOLVED_OUTCOMES: frozenset[str] = frozenset({"stop", "take_profit", "decision_sell"})


@dataclass(frozen=True)
class Bar:
    """One daily OHLC bar. Adjusted prices, since the levels are too."""

    day: date
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise ValueError(f"{self.day}: high {self.high} is below low {self.low}")


@dataclass(frozen=True)
class BracketEntry:
    """A long position opened with broker-side protective legs attached.

    Mirrors what `executor.submit_order` actually sends: an entry, an optional
    stop leg and an optional take-profit leg. `decision_exit_day` is the other
    way the live system closes a position — a sell the agent decided on at a
    later daily run — and it is optional because most positions never get one.

    Long only. The live book is `direction="longonly"` and the touch rules
    invert for a short; a short passed in here would be simulated with the
    wrong comparisons and quietly produce plausible numbers, so it is refused
    rather than approximated.
    """

    trade_id: str
    symbol: str
    entry_day: date
    entry_price: float
    quantity: float
    stop_price: float | None = None
    take_profit_price: float | None = None
    decision_exit_day: date | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"{self.trade_id}: quantity must be positive")
        if self.entry_price <= 0:
            raise ValueError(f"{self.trade_id}: entry price must be positive")
        stop, tp = self.stop_price, self.take_profit_price
        if stop is not None and tp is not None and stop >= tp:
            # Not an intrabar ambiguity — a bracket whose stop sits at or above
            # its target is malformed input, and every bar would "touch both".
            # Failing here keeps a data bug from arriving as a suspiciously
            # large ambiguous count.
            raise ValueError(
                f"{self.trade_id}: stop {stop} is not below take-profit {tp}"
            )
        if stop is not None and stop >= self.entry_price:
            raise ValueError(
                f"{self.trade_id}: stop {stop} is not below entry {self.entry_price}"
            )
        if tp is not None and tp <= self.entry_price:
            raise ValueError(
                f"{self.trade_id}: take-profit {tp} is not above entry {self.entry_price}"
            )


@dataclass(frozen=True)
class SimulatedExit:
    """How one replayed position ended, and on what evidence."""

    entry: BracketEntry
    outcome: ExitOutcome
    exit_day: date | None
    exit_price: float | None
    gapped: bool
    reason: str

    @property
    def resolved(self) -> bool:
        return self.outcome in RESOLVED_OUTCOMES

    @property
    def realized_pnl(self) -> float | None:
        """Gross P&L, or None when nothing closed.

        Gross on purpose: Alpaca charges no commission and the live realized
        ledger this is compared against is on the same basis. Adding a modelled
        fee here would make the two incomparable in the one place they are
        meant to line up.
        """
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry.entry_price) * self.entry.quantity

    @property
    def holding_days(self) -> float | None:
        if self.exit_day is None:
            return None
        return float((self.exit_day - self.entry.entry_day).days)

    def to_closed_trade(self) -> ClosedTrade | None:
        """The round trip as the realized ledger's own type, or None if unresolved.

        Returning the ledger's `ClosedTrade` is what lets `bucket_by_exit` and
        `strategy_bucket` score a backtest without a second copy of the
        arithmetic. Timestamps are midnight UTC of the bar's date: a daily bar
        carries no time of day, and `holding_days` — the only field downstream
        reads from them — is computed from the dates directly.
        """
        if not self.resolved or self.exit_price is None or self.exit_day is None:
            return None
        entry = self.entry
        pnl = (self.exit_price - entry.entry_price) * entry.quantity
        cost = entry.entry_price * entry.quantity
        return ClosedTrade(
            trade_id=entry.trade_id,
            symbol=entry.symbol,
            direction="LONG",
            quantity=entry.quantity,
            entry_price=entry.entry_price,
            exit_price=self.exit_price,
            opened_at_utc=datetime.combine(entry.entry_day, datetime.min.time(), UTC),
            closed_at_utc=datetime.combine(self.exit_day, datetime.min.time(), UTC),
            realized_pnl=pnl,
            realized_pnl_pct=(pnl / cost) if cost else 0.0,
            holding_days=float((self.exit_day - entry.entry_day).days),
            open_activity_id=f"sim-open-{entry.trade_id}",
            close_activity_id=f"sim-close-{entry.trade_id}",
        )


@dataclass(frozen=True)
class ExitPathCensus:
    """What the replay could and could not answer, in counts.

    Reported alongside the buckets and never folded into them. A split that
    quotes expectancy without saying how many positions it had to set aside
    reads as the whole sample — the same failure the live `/v1/trades`
    `unattributed` count exists to prevent.
    """

    simulated: int
    resolved: int
    ambiguous: int
    still_open: int
    gapped: int

    @property
    def ambiguous_share(self) -> float:
        """Ambiguous as a share of everything that ended, resolved or not.

        The denominator excludes positions still open at the end of history:
        those are not a failure to read the bar, they are history running out,
        and mixing them in would dilute exactly the ratio a reader is checking
        before trusting the buckets.
        """
        ended = self.resolved + self.ambiguous
        return self.ambiguous / ended if ended else 0.0

    def summary(self) -> str:
        return (
            f"{self.simulated} simulated · {self.resolved} resolved · "
            f"{self.ambiguous} ambiguous ({self.ambiguous_share:.0%} of ended) · "
            f"{self.still_open} still open · {self.gapped} filled on a gap"
        )


def _resolve_gap(
    bar: Bar, stop_price: float | None, take_profit_price: float | None
) -> tuple[ExitOutcome, float, bool, str] | None:
    """A level the bar had already blown through before it opened, or None.

    Separate from the intrabar test because it is a different claim about the
    fill: a gap resolves the order of events for us (the level was passed at
    the first print), and the fill lands at the open rather than at the level.
    `BracketEntry` guarantees `stop < tp`, so at most one of these can fire.
    """
    if take_profit_price is not None and bar.open >= take_profit_price:
        return (
            "take_profit",
            bar.open,
            True,
            f"gapped up through the target: open {bar.open} >= tp {take_profit_price}",
        )
    if stop_price is not None and bar.open <= stop_price:
        return (
            "stop",
            bar.open,
            True,
            f"gapped down through the stop: open {bar.open} <= stop {stop_price}",
        )
    return None


def resolve_bar(
    bar: Bar,
    stop_price: float | None,
    take_profit_price: float | None,
    decision_today: bool = False,
) -> tuple[ExitOutcome | None, float | None, bool, str]:
    """What one bar does to a resting bracket.

    Returns `(outcome, price, gapped, reason)`, with `outcome=None` meaning the
    bar did nothing and the position carries to the next one.

    Order of the checks is the order of the trading day, and it is load-bearing:

    1. **The open.** A gap through a level resolves it before any intrabar
       question exists, and it fills at the open — better than the level for a
       take-profit, worse for a stop. `BracketEntry` guarantees `stop < tp`, so
       at most one of these can fire on a given open.
    2. **The range.** Both levels touched inside the bar is the case daily data
       cannot order, and it returns `ambiguous` rather than a guess.
    3. **The daily run.** A decision sell fills at the close, and only when no
       level fired: the legs rest at the broker all session while the decision
       sell is submitted once, so a day that did both is another ordering the
       bar cannot supply.
    """
    gapped_through = _resolve_gap(bar, stop_price, take_profit_price)
    if gapped_through is not None:
        return gapped_through

    touched_stop = stop_price is not None and bar.low <= stop_price
    touched_tp = take_profit_price is not None and bar.high >= take_profit_price

    if touched_stop and touched_tp:
        return (
            "ambiguous",
            None,
            False,
            (
                f"bar spans both levels (low {bar.low} <= stop {stop_price}, "
                f"high {bar.high} >= tp {take_profit_price}); a daily bar cannot "
                "say which filled first"
            ),
        )
    if touched_stop:
        return ("stop", stop_price, False, f"low {bar.low} reached the stop {stop_price}")
    if touched_tp:
        return (
            "take_profit",
            take_profit_price,
            False,
            f"high {bar.high} reached the target {take_profit_price}",
        )
    if decision_today:
        return ("decision_sell", bar.close, False, f"agent sold at the close {bar.close}")
    return (None, None, False, "")


def simulate_exit(entry: BracketEntry, bars: Sequence[Bar]) -> SimulatedExit:
    """Replay one position's bracket over `bars` and report how it ended.

    `bars` may be the symbol's whole history; only bars strictly after
    `entry.entry_day` are scanned, for the reason in the module docstring — the
    entry bar's extremes include prices from before the fill.
    """
    for bar in bars:
        if bar.day <= entry.entry_day:
            continue
        decision_today = entry.decision_exit_day == bar.day
        outcome, price, gapped, reason = resolve_bar(
            bar, entry.stop_price, entry.take_profit_price, decision_today
        )
        if outcome is None:
            continue
        return SimulatedExit(
            entry=entry,
            outcome=outcome,
            exit_day=bar.day,
            exit_price=price,
            gapped=gapped,
            reason=reason,
        )

    return SimulatedExit(
        entry=entry,
        outcome="open",
        exit_day=None,
        exit_price=None,
        gapped=False,
        reason="history ended with the position still on the book",
    )


def simulate_all(
    entries: Iterable[BracketEntry], bars_by_symbol: Mapping[str, Sequence[Bar]]
) -> list[SimulatedExit]:
    """Replay every entry against its symbol's bars.

    A symbol with no bars yields `open`, not a dropped row: silently losing a
    position would shrink the denominator of every number downstream, and an
    absent price history is a gap in the *data*, not evidence about the exit.
    """
    return [simulate_exit(e, bars_by_symbol.get(e.symbol, ())) for e in entries]


def replay_signals(
    symbol: str,
    signal_days: Sequence[date],
    bars: Sequence[Bar],
    stop_pct: float,
    take_profit_pct: float,
    quantity: float = 1.0,
) -> list[SimulatedExit]:
    """Turn entry signals into bracketed positions and replay each one's exit.

    The entry rules are the two that keep a backtest from cheating:

    * **Fill at the next bar's open.** A signal derived from a close could not
      have been traded at that close. Filling there is the classic look-ahead,
      and on a signal that fires *because* of a big move it is worth most of
      the strategy's apparent edge.
    * **One position per symbol at a time.** The live sizer will not pyramid,
      and letting the replay stack entries would inflate the sample with
      positions the real system would never have held — which, in a module
      whose entire purpose is sample size, would be a self-inflicted wound.

    A position that ended `ambiguous` still frees the symbol on its bar. The
    ambiguity is about *which leg* filled, not about whether the position
    closed: both levels were touched, so one of them filled and the book was
    flat that evening. Blocking re-entry for the rest of history over an
    unknown fill price would throw away the sample the ambiguity did not
    actually cost. A position still open when history ends blocks everything
    after it, because it genuinely never closed.

    Levels are set as percentages of the fill, matching how the live bracket is
    shaped relative to entry rather than trying to reproduce any particular
    LLM's price target. That is the point: this isolates the *exit* rule from
    the thing that chose the entry.
    """
    if not 0.0 < stop_pct < 1.0:
        raise ValueError("stop_pct must be a fraction between 0 and 1")
    if take_profit_pct <= 0.0:
        raise ValueError("take_profit_pct must be positive")

    by_day = {bar.day: i for i, bar in enumerate(bars)}
    signals = sorted(set(signal_days))
    results: list[SimulatedExit] = []
    blocked_until: date | None = None

    for n, signal_day in enumerate(signals):
        if blocked_until is not None and signal_day < blocked_until:
            continue
        index = by_day.get(signal_day)
        if index is None or index + 1 >= len(bars):
            continue

        fill_bar = bars[index + 1]
        entry = BracketEntry(
            trade_id=f"{symbol}-{n:04d}-{fill_bar.day.isoformat()}",
            symbol=symbol,
            entry_day=fill_bar.day,
            entry_price=fill_bar.open,
            quantity=quantity,
            stop_price=fill_bar.open * (1.0 - stop_pct),
            take_profit_price=fill_bar.open * (1.0 + take_profit_pct),
        )
        # The fill bar itself is the entry bar, which `simulate_exit` skips —
        # so the scan starts on the bar after the fill, as it should.
        sim = simulate_exit(entry, bars[index + 1 :])
        results.append(sim)
        blocked_until = sim.exit_day if sim.exit_day is not None else date.max

    return results


def census(exits: Iterable[SimulatedExit]) -> ExitPathCensus:
    """Count the replay's outcomes, including the ones it could not resolve."""
    rows = list(exits)
    return ExitPathCensus(
        simulated=len(rows),
        resolved=sum(1 for r in rows if r.resolved),
        ambiguous=sum(1 for r in rows if r.outcome == "ambiguous"),
        still_open=sum(1 for r in rows if r.outcome == "open"),
        gapped=sum(1 for r in rows if r.gapped),
    )


def attributed(exits: Iterable[SimulatedExit]) -> list[AttributedTrade]:
    """Resolved exits as `exit_quality`'s type, ready for its roll-ups.

    Unresolved rows are dropped here and counted by `census` instead — the
    caller is expected to report both, and `bucket_by_exit` has no honest place
    to put a position that never closed.
    """
    rows: list[AttributedTrade] = []
    for sim in exits:
        trade = sim.to_closed_trade()
        if trade is None:
            continue
        # Safe by construction: `to_closed_trade` returns None unless the
        # outcome is one of the three resolved classes, all of which are real
        # `ExitClass` members.
        exit_class: ExitClass = sim.outcome  # type: ignore[assignment]
        rows.append(AttributedTrade(trade=trade, exit_class=exit_class, order=None))
    return rows


@dataclass(frozen=True)
class LevelMix:
    """The only numbers a bracket's level exits can actually tell you.

    A per-bucket win rate is a **tautology** for level exits and must never be
    read as a finding: a take-profit leg fills above the entry by definition, so
    its win rate is 100%, and a stop's is 0%. Printing those next to the live
    ledger's win rates invites exactly the wrong comparison — the live numbers
    are a mix of paths, these are a definition.

    What a bracket is actually judged on is here instead:

    * `hit_rate` — of the positions that ended at a level, the share that ended
      at the target. This is the strategy's real coin.
    * `payoff_ratio` — average winner over average loser, **from the prices
      that actually filled**, not from the levels that were asked for. The two
      differ because a stop gaps: nominal 2:1 is not realised 2:1, and the
      difference is the cost of overnight risk, which a backtest that fills at
      the level would report as zero.
    * `breakeven_hit_rate` — 1 / (1 + payoff). The hit rate the strategy has to
      beat for this bracket shape to make money at all. Comparing it to
      `hit_rate` is the whole verdict, and it is one subtraction rather than an
      expectancy the reader has to trust.
    """

    target_exits: int
    stop_exits: int
    avg_win: float | None
    avg_loss: float | None
    nominal_payoff: float | None
    payoff_ratio: float | None
    breakeven_hit_rate: float | None

    @property
    def hit_rate(self) -> float | None:
        """None, not 0.0, when nothing reached a level — there is no coin to read."""
        ended = self.target_exits + self.stop_exits
        return self.target_exits / ended if ended else None

    @property
    def edge(self) -> float | None:
        """Hit rate minus the rate it must beat. Positive is an edge, and that is all."""
        if self.hit_rate is None or self.breakeven_hit_rate is None:
            return None
        return self.hit_rate - self.breakeven_hit_rate


def level_mix(exits: Iterable[SimulatedExit], nominal_payoff: float | None = None) -> LevelMix:
    """Score the bracket on its level exits, using the prices that really filled.

    Decision sells are left out on purpose: they are the agent changing its
    mind, not the bracket resolving, and folding them in would blur the one
    question this answers — whether *these two levels*, at this distance, on
    this book, win more than they need to.
    """
    rows = [e for e in exits if e.outcome in {"stop", "take_profit"}]
    wins = [p for e in rows if e.outcome == "take_profit" and (p := e.realized_pnl) is not None]
    losses = [-p for e in rows if e.outcome == "stop" and (p := e.realized_pnl) is not None]

    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    payoff = (avg_win / avg_loss) if (avg_win and avg_loss and avg_loss > 0) else None
    return LevelMix(
        target_exits=sum(1 for e in rows if e.outcome == "take_profit"),
        stop_exits=sum(1 for e in rows if e.outcome == "stop"),
        avg_win=avg_win,
        avg_loss=avg_loss,
        nominal_payoff=nominal_payoff,
        payoff_ratio=payoff,
        breakeven_hit_rate=(1.0 / (1.0 + payoff)) if payoff else None,
    )


def sample_shortfall(rows: Iterable[AttributedTrade], minimum: int) -> dict[str, int]:
    """Per strategy exit path, how many more trades it needs to reach `minimum`.

    The point of the whole replay is sample size, so the shortfall is a first-
    class output rather than something a reader works out from the buckets. A
    path already at or above `minimum` is reported as 0 and kept in the map:
    dropping it would make "this path is fine" and "this path does not exist"
    look identical, which is the confusion the live n=8 split already caused
    once.
    """
    counts = dict.fromkeys(sorted(STRATEGY_CLASSES), 0)
    for row in rows:
        if row.exit_class in counts:
            counts[row.exit_class] += 1
    return {c: max(0, minimum - n) for c, n in counts.items()}
