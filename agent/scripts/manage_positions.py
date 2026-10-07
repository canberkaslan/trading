#!/usr/bin/env python3
"""Manage positions that are already open — the pass this system never had.

The daily run decides what to BUY. Nothing decided what to do with a position
afterwards: `atr_trailing_stop` and `time_exit` have had zero callers since they
were written, so a stop stayed wherever entry put it and no position was ever
closed on age. Of 33 closed trades, exactly one was an agent sell decision and
24 were kill-switch flattens.

This runs BEFORE the decision loop so capital released here is available to the
theses formed minutes later — which is the point. The book holds ~10 names
against an 11-name universe and a 10% per-name cap, so it is at the cap on
everything it knows and every subsequent buy is trimmed to zero (167 refusals
say exactly that). Without an exit path the freeze is permanent.

    python scripts/manage_positions.py                # report only (default)
    python scripts/manage_positions.py --submit       # actually amend/close
    python scripts/manage_positions.py --refresh-bars # bring held names' bars up to date first

Exit codes, which daily_run.sh turns into pages:

    0  everything planned was done
    1  something failed, and every lot it touched is as protected as before;
       or, with --submit, a held name's stop was not maintained because an
       input was refused (stale bars, a mark the bars do not back, an ATR
       that is not a measurement, a level at the market), or because the
       refresh could not fetch bars for a name the cache has none for. The
       stop stands where it was, and without a page it would stand there
       every night. Also with --submit, a due time exit the exit budget
       held back: a bad input that reads the book as due is held to three
       names only until a person looks. Also a time exit deferred because the
       market was open (a pass run by hand in the session), and a time exit
       queued by an earlier run that the broker rejected, cancelled or expired
       at the open, or left done for the day with shares unsold: the lot had
       neither stop nor exit from then until now.
       That last is read off the book for every held name, whatever this
       pass then does with it (closes it again, back-fills it, or leaves it
       to the exit budget), and named by the first run after it. Which open
       it met is the broker's calendar's to say, not its stamp's date: a run
       past 00:00 UTC stamps the date the next 22:30 run has too.
    3  a time exit may have left shares with no stop: a close ended `unknown`
       or `naked`, or the re-cover after it could not place what it had to.
       A cancel still on its way strips its stop after this run, while the
       coverage check at the end of the run still sees the stop standing, so
       this code is the only thing that can say so while the run is on. An
       `unknown` close can also mean an exit that may still land on shares no
       stop reserves, a sell standing on a book already flat, or an end state
       the broker would not show; the coverage check pages on none of them.

Safety, in the order it matters:

  * DRY RUN BY DEFAULT. `--submit` is the only way anything reaches the broker.
  * The kill switch is honoured: PAUSE_NEW still allows this pass, because
    ratcheting a stop and closing a stale position both REDUCE exposure and
    "pause new entries" is not "stop protecting what is open". FLATTEN_ALL skips
    the pass entirely — the flatten path owns the book at that point and two
    writers on the same positions is how you get a double sell. The switch is
    read again before the first write, so one flipped during the refresh
    stops the pass too.
  * A stop is only ever amended UP. `plan_actions` refuses to emit anything else.
  * A symbol whose protection is ambiguous is left alone. `stop_coverage` returns
    `indeterminate` for orders in a status it does not recognise, and acting on a
    guess there is how you end up with two stops on one lot — a short position
    waiting for a gap down.
  * A time exit releases the stop before it sells, because the stop reserves the
    shares, and only while the market is shut, when nothing but its own
    requests can move the lot: cancel, confirm the cancel, sell the holding
    read after that, verify, re-arm the stop if the sell is not working, and
    read the book once more for the verdict (`execution.protected_close`).
    Between the exit and a re-armed stop the broker's share reservation lets
    only one stand, whatever order lost replies come back in.
  * One pass time-exits at most 3 positions, or 25% of those examined if fewer
    (`position_manager.exit_budget`), so one bad input cannot liquidate the book
    through time exits in a single run. The budget is the trade date's: a pass
    run again the same day counts the exits already stamped today against it,
    and a name whose exit an earlier date queued still waits for an open is
    left to that exit, which counts against it too. That exit sells at the
    open this pass's exits queue for: a rerun past 00:00 UTC stamps the next
    date for the same open as the 22:30 run before it, and so does the run on
    a weekday exchange holiday.
    The rest are reported as deferred, keep their stops, and with --submit
    fail the pass so it pages. Stop maintenance does not count against the
    budget. A stop moved to the last price is a close all the same, so the
    claim holds for the whole pass only while the planner refuses such a
    level.
  * Nothing here opens or grows a position.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
import time
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient, Order
from tradingagents_us.dataflows.polygon import PolygonClient
from tradingagents_us.execution.protected_close import (
    CLOSED_STATUSES,
    FAILED_EXIT_STATUSES,
    LISTING_CATCH_UP_DELAYS_S,
    RELEASED_STATUSES,
    CloseOutcome,
    close_with_protection,
    cover_beside_exit,
    day_ended_exit,
    exit_stamp_date,
    listing_behind,
    missed_exits,
    opened_after,
)
from tradingagents_us.log_redaction import install as install_log_redaction
from tradingagents_us.risk.kill_switch import FileKillSwitchReader, default_kill_switch_path
from tradingagents_us.risk.position_manager import (
    DEFAULT_CONFIG,
    Action,
    Bar,
    ManagedPosition,
    ManagementConfig,
    PlaceStop,
    RatchetStop,
    TimeExit,
    exit_budget,
    plan_actions,
)
from tradingagents_us.risk.stop_coverage import (
    LIVE_STATUSES,
    PROTECTIVE_TYPES,
    QTY_EPSILON,
    OrderView,
    PositionView,
    coverage,
    flatten_orders,
    live_protective_orders,
)

log = logging.getLogger("manage_positions")

#: Statuses in which an order is still standing and can be amended. Narrower
#: than stop_coverage's LIVE_STATUSES on purpose: this set gates a WRITE.
LIVE_ORDER_STATUSES = frozenset({"held", "new", "accepted", "pending_new", "partially_filled"})

#: Statuses in which a sell never sold a share and never will.
_NEVER_SOLD = FAILED_EXIT_STATUSES | {"replaced"}

#: (symbol, client id, status) of each sell on the book.
Sells = frozenset[tuple[str, str, str]]

#: Enough history for a 14-period ATR with room for holidays. Calendar days.
BAR_LOOKBACK_DAYS = 60

#: Daily bars are dated in the exchange's time zone.
_EXCHANGE_TZ = ZoneInfo("America/New_York")

#: Seconds between the refresh's requests. Polygon's plan allows five a minute,
#: and PolygonClient's own backoff (2+4+8+16 s) spends its retries inside the
#: minute the first five used, so an unpaced sixth name is never refreshed.
#: The pace `aggregates` already keeps between pages.
REFRESH_PACE_S = 12.0

#: Skips that mean a name had no bars to manage it by: no age, no ATR.
NO_BARS_REASONS = frozenset({"no_bars", "insufficient_bars"})

#: Skips that mean an input was refused, not that nothing needed doing. Each
#: leaves a held name's stop where it stood, and each repeats every night
#: until the input changes, so with --submit any one of them fails the pass.
REFUSAL_REASONS = frozenset({"stale_bars", "mark_disagrees_with_bars", "bad_atr", "stop_at_market"})

EXIT_OK = 0
EXIT_FAILED = 1
#: A time exit may have left shares with no stop; see the module docstring.
EXIT_UNCOVERED = 3

#: Close outcomes after which shares may have no stop, now or once a cancel on
#: its way lands.
UNCOVERED_STATUSES = frozenset({"unknown", "naked"})


def _read_by_id(closes: Iterable[CloseOutcome]) -> tuple[set[str], set[str]]:
    """The orders this pass's time exits read gone by their own id, and the orders they placed."""
    closes = list(closes)
    gone = {oid for c in closes for oid in (*c.released, *c.dead)}
    placed = {oid for c in closes for oid in (*c.rearmed, c.exit_order_id) if oid}
    return gone, placed


def _listing(
    client: AlpacaClient, closes: Iterable[CloseOutcome], lagging: dict[str, str] | None = None
) -> list[Order]:
    """Every order, nested, off a listing no older than what this pass's time exits read by id.

    Alpaca's listing lags its per-order reads for seconds after a write
    (`protected_close.LISTING_CATCH_UP_DELAYS_S`). One that still shows a sell
    a close read cancelled, or not yet an order it placed, is the book before
    that close: off it the re-cover reads a lot covered by a stop that is
    gone, or naked under one that stands. Listed again until it catches up;
    raises if it never does, so nothing is placed off it. Given `lagging`,
    names there instead each lot whose own closes it is still behind, and
    returns it: what it says of the other lots is as current as their reads.
    """
    closes = list(closes)
    for delay in (0.0, *LISTING_CATCH_UP_DELAYS_S):
        if delay:
            time.sleep(delay)
        raw = client.list_orders(status="all", limit=500, nested=True)
        flat = flatten_orders(raw)
        behind = {
            t: ", ".join(b) for t in sorted({c.ticker for c in closes})
            if (b := listing_behind(flat, *_read_by_id([c for c in closes if c.ticker == t])))
        }
        if not behind:
            return raw
    if lagging is None:
        why = ", ".join(behind.values())
        raise RuntimeError(f"order listing still behind the time exits' reads: {why}")
    lagging.update(behind)
    return raw


def _order_views(
    client: AlpacaClient, closes: Iterable[CloseOutcome] = (), lagging: dict[str, str] | None = None
) -> tuple[list[OrderView], dict[tuple[str, float], str], list[Order]]:
    """Every live order as a pure view, plus a map back to the broker order id.

    Two things this gets right that the obvious version does not:

    `status="all"`, because the resting leg of a bracket sits in `held` and
    Alpaca's "open" filter excludes it — asking for open orders and concluding a
    bracketed book has no stops is a false negative, not an observation.

    Flatten FIRST, convert second. Bracket children arrive nested under their
    parent, and `OrderView` has no `legs`, so converting before flattening
    silently drops every protective leg — the exact false negative above, wearing
    a different hat.

    The id map exists because `OrderView` deliberately carries no id (it is the
    pure view the coverage rules are written against) while amending a stop needs
    one. Keyed on symbol + stop price, which is unique for a live protective leg.

    Last, every sell, whatever its status, off the same listing: what
    today's time exits have spent of the exit budget is counted from their
    stamps there (`_exited_on`), which names an earlier date's exit is still
    selling (`_exiting_before`), and which one the open refused
    (`_report_missed_exits`).

    `closes` are this pass's time exits so far: the listing is read again
    until it has caught up with them (`_listing`, and `lagging`).
    """
    raw = _listing(client, closes, lagging)
    flat = flatten_orders(raw)

    views = [
        OrderView(
            symbol=o.symbol,
            side=o.side.lower(),
            order_type=o.order_type.lower(),
            status=o.status.lower(),
            remaining_qty=max(0.0, o.qty - o.filled_qty),
            stop_price=o.stop_price,
        )
        for o in flat
    ]
    ids = {
        (o.symbol, o.stop_price): o.id
        for o in flat
        if o.stop_price is not None and o.status.lower() in LIVE_ORDER_STATUSES
    }
    return views, ids, [o for o in flat if o.side.lower() == "sell"]


def _sells(sell_orders: list[Order]) -> Sells:
    """(symbol, client id, status) of each sell, as the budget's helpers read them."""
    return frozenset((o.symbol, o.client_order_id, o.status.lower()) for o in sell_orders)


def _read_book(
    client: AlpacaClient, closes: Iterable[CloseOutcome] = (), lagging: dict[str, str] | None = None
) -> tuple[list[OrderView], dict[tuple[str, float], str], list, list[Order]]:
    """The orders, then the holding, in that order and never the other.

    A sell that fills between two reads must show up as fewer shares held,
    never as a lot whose stop has gone. Read the holding first and a stop that
    fires in between reads as terminal while its shares still read as held: the
    lot looks naked, and the back-fill puts a stop on a book that is flat by
    then, which a margin account takes as a short-sale stop. Read the orders
    first and the same fill shows as a position that is gone. The same rule as
    `protected_close._read_truth`.
    """
    orders, stop_ids, sells = _order_views(client, closes, lagging)
    return orders, stop_ids, client.list_positions(), sells


def _exited_on(sells: Sells, day: date) -> frozenset[str]:
    """The names a time exit sold, or is selling, under `day`'s stamp.

    The stamp or a retry of it (`-rN`), in a status that sold or still can:
    what earlier passes on the same trade date spent of the exit budget. A
    stop re-armed under a stamp (`<stamp>-r2-arm-...`) is a stop, not an
    exit, and spends nothing.
    """
    return frozenset(
        symbol for symbol, coid, status in sells
        if status not in _NEVER_SOLD and exit_stamp_date(coid, symbol) == day
    )


def _exiting_before(sells: Sells, day: date) -> frozenset[str]:
    """The names whose time exit, stamped on a trade date other than `day`, still works.

    Queued for an open that has not come since (a weekday exchange holiday,
    or a rerun past 00:00 UTC). The lot is on its way out: its close sends
    nothing, and its exit spends a share of `day`'s exit budget, since it
    sells at the open `day`'s exits queue for.
    """
    return frozenset(
        symbol for symbol, coid, status in sells
        if status in LIVE_ORDER_STATUSES
        and exit_stamp_date(coid, symbol) not in (None, day)
    )


#: Daily bars and their dates, oldest first, as `_bars_for` returns them.
Series = tuple[list[Bar], list[date]]


def _bars_for(
    ticker: str, session, end: date, fresh: dict[str, Series] | None = None
) -> Series:
    """Daily bars, oldest first, with their dates: this pass's fresh ones, or the cache's.

    An empty list means "do not act" and never "flat": `average_true_range`
    returns None on short history rather than a partial average, and the caller
    skips the symbol with a reason.
    """
    if fresh and ticker in fresh:
        return fresh[ticker]
    from tradingagents_us.storage.price_cache import read_bars

    rows = read_bars(session, ticker, end - timedelta(days=BAR_LOOKBACK_DAYS), end)
    bars = [Bar(high=r.high, low=r.low, close=r.close) for r in rows]
    dates = [date.fromisoformat(r.bar_date) for r in rows]
    return bars, dates


def _refresh_bars(tickers: list[str], today: date) -> tuple[dict[str, Series], list[str]]:
    """Fetch each held name's daily bars up to `today` from Polygon, for this pass.

    The bar cache is only written by the prices route, when someone opens a
    chart. A held name nobody charted kept the bars of the last time someone
    did, so its age stopped counting (the time exit never came due), and once
    it moved far enough the mark band refused its stop every night. The whole
    window is fetched adjusted, so a split's unadjusted rows are not read.

    Held in memory for this pass, never written to the cache. trade.py's BUY
    checks read that cache (the correlation cap over the held names, the
    price-anomaly gate, the liquidity floor), and nothing else in the daily
    run writes it: filling it here, minutes before the councils run, would
    change what those checks decide on a BUY.

    Paced at REFRESH_PACE_S, so a ten-name book takes about two minutes.
    Returns the series per name, and the names it could not refresh (or that
    came back empty). Those are judged by the cache and how far behind its
    bars are (`MAX_BARS_BEHIND`), and where the cache has none to judge them
    by, the pass fails (`_report_unrefreshed`).
    """
    start = today - timedelta(days=BAR_LOOKBACK_DAYS)
    fresh: dict[str, Series] = {}
    failed: list[str] = []
    try:
        polygon = PolygonClient()
    except Exception as exc:  # noqa: BLE001 — no key, no client: judged by the cache's age
        log.warning("bars not refreshed, no Polygon client: %s", exc)
        return fresh, list(tickers)
    with polygon:
        for n, ticker in enumerate(tickers):
            if n:
                time.sleep(REFRESH_PACE_S)
            try:
                aggs = polygon.aggregates(ticker, start, today, timespan="day")
            except Exception as exc:  # noqa: BLE001 — one name must not stop the rest
                log.warning("%-6s bars not refreshed: %s", ticker, exc)
                failed.append(ticker)
                continue
            dated = sorted(
                (datetime.fromtimestamp(a.timestamp_ms / 1000, _EXCHANGE_TZ).date(), a)
                for a in aggs
            )
            window = [(d, a) for d, a in dated if start <= d <= today]
            if not window:
                log.warning("%-6s bars not refreshed: Polygon returned none", ticker)
                failed.append(ticker)
                continue
            fresh[ticker] = (
                [Bar(high=a.high, low=a.low, close=a.close) for _, a in window],
                [d for d, _ in window],
            )
    return fresh, failed


def _sessions_since(last: date, today: date) -> int:
    """Weekdays after `last`, up to and including `today`."""
    return sum(
        1 for k in range(1, (today - last).days + 1)
        if (last + timedelta(days=k)).weekday() < 5
    )


def _entry_dates(client: AlpacaClient) -> dict[str, date]:
    """When the CURRENT lot of each symbol was opened.

    Alpaca's position payload has no entry timestamp, and the API route that
    pretends otherwise fills it with `datetime.now(UTC)` — every position reports
    as opened this instant. So the date is recovered from the fill stream through
    the FIFO matcher we already run for the realized ledger: whatever inventory is
    left after matching IS the open position, and the oldest surviving lot is when
    the current holding began.

    Oldest rather than newest on purpose. A position added to twice should be aged
    from its first share: the thesis started then, and the time exit is asking
    whether the thesis has gone anywhere.
    """
    from tradingagents_us.execution.reconcile import reconcile_fills

    fills = client.list_fill_activities()
    result = reconcile_fills(fills)
    oldest: dict[str, date] = {}
    for lot in result.open_lots:
        # `OpenLot.direction` is "LONG"/"SHORT" — upper case. Matching "long"
        # here silently discarded every lot and reported the whole book as
        # having no entry date, which is how this was caught: ten positions all
        # skipping for the same reason is a filter bug, not ten coincidences.
        if lot.direction.upper() != "LONG":
            continue
        when = lot.opened_at_utc.astimezone(UTC).date()
        if lot.symbol not in oldest or when < oldest[lot.symbol]:
            oldest[lot.symbol] = when
    return oldest


def _bars_since(bars_dates: list[date], entry: date) -> int:
    """Trading bars strictly after the entry date.

    Counted from the bar series rather than the calendar: a holiday or a halt is
    not a holding day, and counting it as one fires the time exit early — on a
    20-bar window, three market holidays is a 15% error in the wrong direction.
    """
    return sum(1 for d in bars_dates if d > entry)


def _build_managed(
    client: AlpacaClient,
    repo,
    positions_raw: list,
    by_symbol: dict,
    orders: list[OrderView],
    stop_ids: dict[tuple[str, float], str],
    entries: dict[str, date],
    today: date,
    fresh: dict[str, Series] | None = None,
) -> tuple[list[ManagedPosition], dict[str, list[Bar]]]:
    """Turn broker state into the pure module's inputs, skipping what it cannot
    describe honestly. Every exclusion is logged with its reason — a position
    that silently vanishes from the pass is indistinguishable from one the pass
    decided to leave alone. Bars are this pass's `fresh` ones where it has
    them, and the cache's otherwise."""
    managed: list[ManagedPosition] = []
    bars_by_ticker: dict[str, list[Bar]] = {}

    with repo.session() as session:
        for p in positions_raw:
            cov = by_symbol.get(p.symbol)
            if cov is None or cov.indeterminate_qty > 0:
                # An order in a status stop_coverage does not recognise is
                # neither protection nor its absence. Amending on that guess is
                # how a lot ends up with two stops, which is a short waiting for
                # a gap down.
                log.info("%-6s SKIP  protection ambiguous — left alone", p.symbol)
                continue

            bars, bar_dates = _bars_for(p.symbol, session, today, fresh)
            bars_by_ticker[p.symbol] = bars

            entry = entries.get(p.symbol)
            if entry is None:
                # No surviving open lot for a symbol we hold means the fill
                # stream and the broker disagree. Report it; do not age a
                # position off a number we had to invent.
                log.info("%-6s SKIP  no open lot in the fill stream", p.symbol)
                continue

            protective = live_protective_orders(p.symbol, "long", orders)
            stop_order = next(
                (o for o in protective if o.order_type in ("stop", "stop_limit") and o.stop_price),
                None,
            )
            stop_price = stop_order.stop_price if stop_order else None
            stop_id = stop_ids.get((p.symbol, stop_price)) if stop_price is not None else None

            managed.append(
                ManagedPosition(
                    ticker=p.symbol,
                    quantity=p.qty,
                    avg_entry_price=p.avg_entry_price,
                    current_price=p.market_value / p.qty if p.qty else 0.0,
                    # None, not 0, for an empty cache: 0 is "opened today",
                    # and a month-old name read that way never ages out.
                    bars_held=_bars_since(bar_dates, entry) if bar_dates else None,
                    current_stop=stop_price,
                    stop_order_id=stop_id,
                    naked_quantity=cov.naked_qty if cov.is_actionable else 0.0,
                    bars_behind=_sessions_since(bar_dates[-1], today) if bar_dates else None,
                )
            )

    return managed, bars_by_ticker


def _describe(act: Action, submitting: bool) -> None:
    """One line per action, identical in dry run and in earnest."""
    tail = "" if submitting else "   [dry run]"
    if isinstance(act, RatchetStop):
        log.info(
            "%-6s STOP  %.2f -> %.2f  (atr %.2f)%s",
            act.ticker, act.old_stop, act.new_stop, act.atr, tail,
        )
    elif isinstance(act, PlaceStop):
        log.info(
            "%-6s PLACE %g naked shares @ %.2f  (atr %.2f)%s",
            act.ticker, act.quantity, act.stop_price, act.atr, tail,
        )
    else:
        log.info(
            "%-6s EXIT  %g shares, %d bars held, pnl %+.2f%%%s",
            act.ticker, act.quantity, act.bars_held, act.pnl_pct * 100, tail,
        )


def _naked_now(client: AlpacaClient, ticker: str, closes: Iterable[CloseOutcome] = ()) -> float:
    """The shares of `ticker` no stop covers, off the broker's book as it is now.

    The plan's figure comes from a read taken before any of the pass's writes,
    and a time exit before this one (release, confirm, sell, look up) can take
    a minute. A lot sold in that time, by a council SELL approved on the phone
    or by hand, is flat when its back-fill goes, and a margin account takes a
    sell stop on a flat book as a short-sale stop. So the back-fill is sized
    off orders read now and the holding read after them, as `_read_book` has
    it: 0 when the lot is gone or not long. Raises when its protection is
    ambiguous now, so nothing is placed and the pass fails. It waits only on
    this lot's own closes: a listing behind on another lot's says nothing of this one.
    """
    orders = _order_views(client, [c for c in closes if c.ticker == ticker])[0]
    lot = next((p for p in client.list_positions() if p.symbol == ticker), None)
    if lot is None or lot.side != "long":
        return 0.0
    (row,) = coverage([PositionView(symbol=ticker, qty=lot.qty, side="long")], orders).symbols
    if row.indeterminate_qty > QTY_EPSILON:
        raise RuntimeError(
            f"back-fill not placed: {row.indeterminate_qty:g} shares' protection is ambiguous now"
        )
    return row.naked_qty


def _place_backfill(
    client: AlpacaClient, act: PlaceStop, closes: Iterable[CloseOutcome] = ()
) -> None:
    """One back-fill stop, for no more than the shares `_naked_now` finds naked."""
    qty = min(act.quantity, _naked_now(client, act.ticker, closes))
    if qty <= QTY_EPSILON:
        log.info(
            "%-6s SKIP  back-fill: no naked shares now, sold or covered since the plan",
            act.ticker,
        )
        return
    placed = client.submit_order(
        symbol=act.ticker,
        qty=qty,
        side="sell",
        order_type="stop",
        # GTC: a day stop expires at the close and leaves the position naked
        # overnight, which is the window the whole back-fill exists to close.
        time_in_force="gtc",
        stop_price=act.stop_price,
    )
    log.info("%-6s stop placed, order %s", act.ticker, placed.id)


def _execute(
    client: AlpacaClient,
    actions: list[Action],
    trade_date: date | None = None,
    *,
    unclosed: list[str] | None = None,
    uncovered: list[str] | None = None,
    unsettled: dict[str, str] | None = None,
    closes: list[CloseOutcome] | None = None,
) -> int:
    """Apply the plan. One bad symbol must not stop the rest of the pass.

    `trade_date` dates the time-exit stamp; the pass's UTC date when omitted.
    Each time exit that did not close its position is appended to `unclosed`,
    so the caller can cover what it left behind (`_recover_unclosed`), and each
    one that may have left shares with no stop to `uncovered`, so it can say so.
    One whose exit was sent and never ruled out goes into `unsettled`, keyed to
    its stamp: that exit can still land, and a back-fill must look for it.
    Each close's outcome goes into `closes`, and every listing after it waits
    for one that has caught up with what it read by id (`_listing`).
    """
    closes = [] if closes is None else closes
    failures = 0
    for act in actions:
        try:
            if isinstance(act, RatchetStop):
                replaced = client.replace_order(act.stop_order_id, stop_price=act.new_stop)
                log.info("%-6s ratcheted, new order %s", act.ticker, replaced.id)
            elif isinstance(act, PlaceStop):
                _place_backfill(client, act, closes)
            else:
                outcome = _close_on_age(client, act, trade_date)
                closes.append(outcome)
                failures += int(not outcome.ok)
                if not outcome.ok:
                    _hand_on(outcome, unclosed, uncovered, unsettled)
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not stop the pass
            failures += 1
            log.error("%-6s FAILED: %s", act.ticker, exc)
            if not isinstance(act, RatchetStop | PlaceStop):
                # Died somewhere inside the close: nothing says what it released.
                if unclosed is not None:
                    unclosed.append(act.ticker)
                if uncovered is not None:
                    uncovered.append(f"{act.ticker} close died: {exc}")
    return failures


def _close_on_age(client: AlpacaClient, act: TimeExit, trade_date: date | None) -> CloseOutcome:
    """One time exit through `protected_close`, logged.

    Not DELETE /positions/{symbol}: the GTC stop reserves every share of a
    protected position, so the broker refused that close on exactly the
    positions that have one, and what it did close carried a broker id and
    booked as a flatten. close_with_protection releases the stop, sells the
    holding read after the release under the time-exit stamp, re-arms the stop
    if the sell is not working, and reads the verdict off the broker.

    An earlier exit that the open refused is not counted here: the pass has
    named it already, off its own listing (`_report_missed_exits`), whether
    or not the name came due again.
    """
    outcome = close_with_protection(
        client, act.ticker, trade_date=trade_date or datetime.now(UTC).date()
    )
    _log_close(outcome)
    return outcome


def _hand_on(
    outcome: CloseOutcome,
    unclosed: list[str] | None,
    uncovered: list[str] | None,
    unsettled: dict[str, str] | None,
) -> None:
    """Record what a close that did not close left for the rest of the pass."""
    if unclosed is not None:
        unclosed.append(outcome.ticker)
    if uncovered is not None and outcome.status in UNCOVERED_STATUSES:
        uncovered.append(f"{outcome.ticker} {outcome.status}")
    if unsettled is not None and outcome.client_order_id:
        unsettled[outcome.ticker] = outcome.client_order_id


def _report_exit_budget(
    actions: list[Action],
    skips: list,
    examined: list[ManagedPosition],
    config: ManagementConfig,
    exited_today: frozenset[str],
    submitting: bool,
    exiting: frozenset[str] = frozenset(),
) -> bool:
    """Say what the exit budget held back, in one line a person will read.

    A deferred close is still held, its stop is still maintained, and the
    next pass takes it if it is still due. But the budget is there for the
    night a bad input reads every name as due at once (a bar cache that ages
    every name, a mark that reads every position flat), and it holds that
    fault to three names only until a person looks. A warning line is read
    by no one, and the nights after would take the rest of the book three
    names at a time. True when the pass must fail for it, so daily_run pages:
    with --submit. A dry run only reports.
    """
    deferred = [s.ticker for s in skips if s.reason == "exit_budget"]
    if not deferred:
        return False
    closing = sum(1 for a in actions if isinstance(a, TimeExit))
    book = len({m.ticker for m in examined} | exited_today)
    queued = len(exiting - exited_today)
    log.warning(
        "exit budget: closing %d of %d due (budget %d of %d positions today, "
        "%d closed earlier today%s); deferred: %s",
        closing,
        closing + len(deferred),
        exit_budget(book, config),
        book,
        len(exited_today),
        f", {queued} queued earlier for the same open" if queued else "",
        ", ".join(deferred),
    )
    return submitting


def _report_missed_exits(
    sell_orders: list[Order],
    positions: list,
    opened: Callable[[datetime], bool],
    submitting: bool,
) -> bool:
    """Name each held lot whose time exit, queued by an earlier run, the open did not fill.

    The broker rejected, cancelled or expired it there, or filled only part,
    and the lot spent the session with neither that exit nor the stop it
    replaced. Read for every held name, not only those this pass closes
    again: a lot the session moved out of the time exit's flat band is no
    longer due, and one the exit budget defers is not closed, so the close
    would never see the miss, and the back-fill that covers the lot tonight
    would make the next coverage check read it as recovered.

    `opened` says whether a session opened after an exit was sent
    (`protected_close.opened_after`): an exit that met none has missed
    nothing yet, whatever date its stamp carries.

    True when the pass must fail for it: with --submit. A dry run only reports.
    """
    missed = {
        p.symbol: found for p in positions
        if (found := missed_exits(sell_orders, p.symbol, opened))
    }
    for symbol, found in sorted(missed.items()):
        log.error(
            "%-6s MISSED EXIT: an earlier time exit did not sell the lot at the open, "
            "which then had no stop until this run: %s",
            symbol, "; ".join(found),
        )
    return bool(missed) and submitting


def _report_refusals(skips: list, submitting: bool) -> bool:
    """Name the held names whose stops a refused input kept where they were.

    True when the pass must fail for it: with --submit, since the stops were
    due to be maintained and were not. A dry run only reports.
    """
    refused = [s for s in skips if s.reason in REFUSAL_REASONS]
    if not refused:
        return False
    log.warning(
        "REFUSED: stops left where they stood, an input was refused: %s",
        ", ".join(f"{s.ticker} ({s.reason})" for s in refused),
    )
    return submitting


def _report_unrefreshed(skips: list, unrefreshed: list[str], submitting: bool) -> bool:
    """Name the held names the refresh missed that the cache cannot stand in for.

    A cache that is merely behind is `stale_bars`, a refusal. One that is
    empty or short is not: the name is skipped with no age for its time exit
    and no ATR for its stop, and the same name misses the same refresh every
    night. True when the pass must fail for it: with --submit, as for a
    refusal. A dry run only reports.
    """
    missed = [s for s in skips if s.ticker in unrefreshed and s.reason in NO_BARS_REASONS]
    if not missed:
        return False
    log.warning(
        "UNREFRESHED: not managed, no bars to manage them by and none could be fetched: %s",
        ", ".join(f"{s.ticker} ({s.reason})" for s in missed),
    )
    return submitting


def _log_close(outcome: CloseOutcome) -> None:
    """One line per time exit, naming the order and what was released or re-armed."""
    parts = [outcome.status, outcome.detail]
    if outcome.client_order_id:
        parts.append(f"order {outcome.exit_order_id or '?'} ({outcome.client_order_id})")
    if outcome.released:
        parts.append("released " + ",".join(outcome.released))
    if outcome.rearmed:
        parts.append("re-armed " + ",".join(outcome.rearmed))
    line = " | ".join(parts)
    if outcome.ok:
        log.info("%-6s closed on age: %s", outcome.ticker, line)
    else:
        log.error("%-6s FAILED time exit: %s", outcome.ticker, line)


def _recover_unclosed(
    client: AlpacaClient,
    repo,
    tickers: set[str],
    entries: dict[str, date],
    today: date,
    config: ManagementConfig,
    uncovered: list[str],
    unsettled: dict[str, str] | None = None,
    fresh: dict[str, Series] | None = None,
    closes: list[CloseOutcome] | None = None,
) -> int:
    """Back-fill, from a fresh broker read, the names a time exit left open.

    The plan was made from the book as it stood before the pass, and a name
    planned for a time exit got no back-fill in it: the close was going to make
    one moot. A close that did not happen (refused, naked, unknown, or cut short
    by a crash between the release and the sell) leaves that name exactly where
    the back-fill exists for, and the next pass would pick it for a time exit
    again rather than cover it. So those names are planned again here, from
    positions and orders read now, under a config in which nothing is old
    enough to exit: all that can come out of it is the back-fill, placed only
    if --backfill-stops asked for back-fills at all.

    Stricter than the main pass, because a failed close is where the book is
    least settled: a name is left alone while any sell other than a working
    stop or a working take-profit still stands on it. That covers a stop still
    in `pending_cancel`, which may yet fill; one in `stopped`, whose fill is on
    its way; an exit that landed after all; a take-profit whose cancel is on
    its way. Each reserves the shares or is about to sell them, and a stop
    placed beside it is refused while the lot is held and becomes a short once
    that sell fills. Not an exit of the lot whose day is over
    (`protected_close.day_ended_exit`): a day order, it sells nothing more, and
    left standing in the count it kept the lot naked for good.

    Nor a take-profit that works: it sells only the shares it holds back, the
    broker refuses a stop on those, and the back-fill is sized off the shares
    no stop covers (`_naked_now`), so it never stands beside the take-profit on
    the same shares, as the main pass's back-fill does not. Counted as standing,
    a bracket's take-profit kept a lot added to by brackets (GOOGL: a stop on
    most of it, three one-share brackets) from its back-fill on every run: a
    share whose stop a failed close could not put back stayed without one for
    as long as the lot stayed due.

    A name whose exit was sent and never found (`unsettled`) is back-filled all
    the same, since it may never land, but through
    `protected_close.cover_beside_exit`: the exit can land beside the stop's
    POST, and the broker then lets only the first of the two stand, so the
    book is read again to say which. A name it settles either way is off
    `uncovered`.

    Every listing it reads is one that has caught up with what the closes of
    the names it was handed (`closes`) read by id (`_listing`): right after
    them, Alpaca's may still show the stops they released, or not yet the
    ones they put back. A name whose own closes it never catches up with is
    a book it could not read; the rest are re-covered off it all the same.

    Returns how many placements failed. A book it could not read, or a
    placement that failed, is also appended to `uncovered`: the names it was
    handed may have shares with no stop, and it could not put one there.
    """
    closes = [c for c in closes or [] if c.ticker in tickers]
    lagging: dict[str, str] = {}
    try:
        # Orders first, holding last (see `_read_book`).
        orders, stop_ids, positions, sell_orders = _read_book(client, closes, lagging)
    except Exception as exc:  # noqa: BLE001 — reported and counted, never guessed past
        log.error("re-cover after failed exits: book unreadable, nothing placed: %s", exc)
        names = ", ".join(sorted(tickers))
        uncovered.append(f"re-cover could not read the book for {names}: {exc}")
        return 1
    for name, why in lagging.items():
        log.error("%-6s re-cover: book unreadable, nothing placed: listing behind: %s", name, why)
        uncovered.append(f"re-cover could not read the book for {name}: listing behind: {why}")
    held = [p for p in positions if p.symbol in tickers and p.symbol not in lagging]
    if not held:
        return len(lagging)

    standing: dict[str, str] = {}
    for o in sell_orders:
        kind, status = o.order_type.lower(), o.status.lower()
        gone = status in RELEASED_STATUSES or status == "replaced" or day_ended_exit(o, o.symbol)
        a_working_stop = kind in PROTECTIVE_TYPES and status in LIVE_STATUSES
        a_take_profit = kind == "limit" and status in LIVE_ORDER_STATUSES
        if o.qty - o.filled_qty > QTY_EPSILON and not (gone or a_working_stop or a_take_profit):
            standing.setdefault(o.symbol, f"{kind} sell in {status}")
    for p in held:
        if p.symbol in standing:
            log.info("%-6s SKIP  re-cover: a %s still stands on it", p.symbol, standing[p.symbol])
    held = [p for p in held if p.symbol not in standing]

    report = coverage([PositionView(symbol=p.symbol, qty=p.qty, side="long") for p in held], orders)
    by_symbol = {row.symbol: row for row in report.symbols}
    managed, bars_by_ticker = _build_managed(
        client, repo, held, by_symbol, orders, stop_ids, entries, today, fresh
    )
    never_old = dataclasses.replace(config, max_bars=sys.maxsize)
    actions, skips = plan_actions(managed, bars_by_ticker, never_old)
    for skip in skips:
        log.info("%-6s SKIP  re-cover: %-20s %s", skip.ticker, skip.reason, skip.detail)
    backfills = [a for a in actions if isinstance(a, PlaceStop)]
    for act in backfills:
        _describe(act, True)
    unsettled = unsettled or {}
    failed = _execute(
        client, [a for a in backfills if a.ticker not in unsettled], today, closes=closes
    )
    if failed:
        uncovered.append(f"re-cover could not place {failed} back-fill stop(s)")
    for act in (a for a in backfills if a.ticker in unsettled):
        row = by_symbol[act.ticker]
        covered = min(row.protected_qty, row.position_qty)
        failed += _cover_beside_exit(
            client, act, unsettled[act.ticker], uncovered, covered, closes
        )
    return failed + len(lagging)


def _cover_beside_exit(
    client: AlpacaClient, act: PlaceStop, stamp: str, uncovered: list[str], covered: float,
    closes: Iterable[CloseOutcome] = (),
) -> int:
    """One back-fill beside an exit that may still land; 1 if it is not settled.

    `covered` is what the stops standing beside it covered when the back-fill
    was sized: the verdict holds the lot to that and the back-fill both.
    """
    gone, placed = _read_by_id(c for c in closes if c.ticker == act.ticker)
    try:
        outcome = cover_beside_exit(
            client, act.ticker, stamp=stamp, qty=act.quantity, stop_price=act.stop_price,
            covered=covered, released=gone, rearmed=placed,
        )
    except Exception as exc:  # noqa: BLE001 — one bad symbol must not stop the pass
        log.error("%-6s FAILED back-fill beside exit %s: %s", act.ticker, stamp, exc)
        uncovered.append(f"{act.ticker} back-fill beside exit {stamp} died: {exc}")
        return 1
    _log_close(outcome)
    if outcome.status in CLOSED_STATUSES or outcome.status == "unchanged":
        # The back-fill stands beside what stood before it (`unchanged` holds
        # the lot to both), or the lot is gone with nothing standing: the
        # close's doubt is settled.
        uncovered[:] = [u for u in uncovered if not u.startswith(f"{act.ticker} ")]
        return 0
    uncovered.append(f"{act.ticker} {outcome.status} beside exit {stamp}")
    return 1


def _kill_switch() -> str:
    path = os.environ.get("KILL_SWITCH_FILE", default_kill_switch_path())
    return FileKillSwitchReader(path).read()


def _refresh_held(client: AlpacaClient, today: date) -> tuple[dict[str, Series], list[str]]:
    """`_refresh_bars` for the names held, run BEFORE the pass reads its book.

    Never between that read and the writes planned off it: at REFRESH_PACE_S a
    name the refresh takes minutes, and a lot sold in that time (a council
    SELL approved on the phone, a close by hand) would still have its stop
    moved, or a back-fill planned for it. A name bought meanwhile is judged by
    its cache.
    """
    held = [p.symbol for p in client.list_positions()]
    return _refresh_bars(held, today) if held else ({}, [])


def _may_write(submitting: bool) -> bool:
    """Whether the pass may write: with --submit, and the kill switch not flattening.

    Read again here, not only at the start: the refresh alone takes minutes,
    and a FLATTEN_ALL flipped in that time hands the book to the flatten path.
    Writing beside it is the double sell the check at the start exists for.
    """
    if not submitting:
        log.info("dry run — pass --submit to act")
        return False
    if _kill_switch() == "FLATTEN_ALL":
        log.info("kill switch FLATTEN_ALL since the pass began — nothing written")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submit", action="store_true", help="actually amend/close (default: report)"
    )
    parser.add_argument("--max-bars", type=int, default=DEFAULT_CONFIG.max_bars)
    parser.add_argument("--atr-mult", type=float, default=DEFAULT_CONFIG.atr_mult)
    parser.add_argument(
        "--backfill-stops",
        action="store_true",
        help="place protective stops on unambiguously naked shares (default: report them)",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="override the bar-cache DB (same flag scripts/trade.py takes)",
    )
    parser.add_argument(
        "--refresh-bars",
        action="store_true",
        help="fetch the held names' daily bars from Polygon for this pass (not into the cache)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    install_log_redaction()

    ks = _kill_switch()
    if ks == "FLATTEN_ALL":
        log.info(
            "kill switch FLATTEN_ALL — skipped; the flatten path owns the book "
            "and two writers on the same positions is how you get a double sell"
        )
        return 0
    log.info("kill switch %s", ks)

    config = ManagementConfig(
        max_bars=args.max_bars,
        atr_mult=args.atr_mult,
        backfill_missing_stops=args.backfill_stops,
    )

    # Local import: keeps the DB engine (and create_all) off the --help path.
    from tradingagents_us.storage import TradeLogRepository, make_engine

    repo = TradeLogRepository(
        engine=make_engine(args.db_url) if args.db_url else None
    )

    with AlpacaClient() as client:
        today = datetime.now(UTC).date()
        fresh, unrefreshed = _refresh_held(client, today) if args.refresh_bars else ({}, [])
        orders, stop_ids, positions_raw, sell_orders = _read_book(client)
        sells = _sells(sell_orders)
        if not positions_raw:
            log.info("no open positions")
            return 0

        report = coverage(
            [PositionView(symbol=p.symbol, qty=p.qty, side="long") for p in positions_raw],
            orders,
        )
        by_symbol = {row.symbol: row for row in report.symbols}
        entries = _entry_dates(client)

        managed, bars_by_ticker = _build_managed(
            client, repo, positions_raw, by_symbol, orders, stop_ids, entries, today, fresh
        )

        exited_today, exiting = _exited_on(sells, today), _exiting_before(sells, today)
        actions, skips = plan_actions(
            managed, bars_by_ticker, config, exited_today=exited_today, exiting=exiting
        )

        for skip in skips:
            log.info("%-6s SKIP  %-20s %s", skip.ticker, skip.reason, skip.detail)
        # Each names what it found in a line of its own; with --submit, any
        # of them fails the pass, and daily_run pages on that.
        failing = [
            _report_missed_exits(sell_orders, positions_raw, opened_after(client), args.submit),
            _report_exit_budget(
                actions, skips, managed, config, exited_today, args.submit, exiting
            ),
            _report_refusals(skips, args.submit),
            _report_unrefreshed(skips, unrefreshed, args.submit),
        ]
        refused = any(failing)

        if not actions:
            log.info("nothing to do (%d positions examined)", len(managed))
            return EXIT_FAILED if refused else EXIT_OK

        for act in actions:
            _describe(act, args.submit)

        if not _may_write(args.submit):
            return 0

        unclosed: list[str] = []
        uncovered: list[str] = []
        unsettled: dict[str, str] = {}
        closes: list[CloseOutcome] = []
        failures = _execute(
            client, actions, today, unclosed=unclosed, uncovered=uncovered, unsettled=unsettled,
            closes=closes,
        )
        if unclosed:
            log.warning(
                "re-cover: time exits left %s open; re-reading the book", ", ".join(unclosed)
            )
            failures += _recover_unclosed(
                client, repo, set(unclosed), entries, today, config, uncovered, unsettled, fresh,
                closes,
            )
        if uncovered:
            log.error(
                "UNCOVERED: shares may have no stop, now or once a pending cancel lands, "
                "or a stop may stand on a lot already sold: %s",
                "; ".join(uncovered),
            )
            return EXIT_UNCOVERED
        return EXIT_FAILED if failures or refused else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
