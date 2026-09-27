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

Exit codes, which daily_run.sh turns into pages:

    0  everything planned was done
    1  something failed, and every lot it touched is as protected as before
    3  a time exit may have left shares with no stop: a close ended `unknown`
       or `naked`, or the re-cover after it could not place what it had to.
       A cancel still on its way strips its stop after this run, while the
       coverage check at the end of the run still sees the stop standing, so
       this code is the only thing that can say so while the run is on. An
       `unknown` close can also mean the opposite: a stop that could not be
       confirmed off a book the exit already sold, which the coverage check
       never pages on either.

Safety, in the order it matters:

  * DRY RUN BY DEFAULT. `--submit` is the only way anything reaches the broker.
  * The kill switch is honoured: PAUSE_NEW still allows this pass, because
    ratcheting a stop and closing a stale position both REDUCE exposure and
    "pause new entries" is not "stop protecting what is open". FLATTEN_ALL skips
    the pass entirely — the flatten path owns the book at that point and two
    writers on the same positions is how you get a double sell.
  * A stop is only ever amended UP. `plan_actions` refuses to emit anything else.
  * A symbol whose protection is ambiguous is left alone. `stop_coverage` returns
    `indeterminate` for orders in a status it does not recognise, and acting on a
    guess there is how you end up with two stops on one lot — a short position
    waiting for a gap down.
  * A time exit releases the stop before it sells, because the stop reserves the
    shares, and it releases it in the one order that cannot double-sell:
    cancel, confirm the cancel, sell the holding read after that, verify, and
    re-arm the stop in the same pass if the sell did not go through
    (`execution.protected_close`).
  * Nothing here opens or grows a position.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.execution.protected_close import (
    CLOSED_STATUSES,
    RELEASED_STATUSES,
    CloseOutcome,
    close_with_protection,
    cover_beside_exit,
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

#: Enough history for a 14-period ATR with room for holidays. Calendar days.
BAR_LOOKBACK_DAYS = 60

EXIT_OK = 0
EXIT_FAILED = 1
#: A time exit may have left shares with no stop; see the module docstring.
EXIT_UNCOVERED = 3

#: Close outcomes after which shares may have no stop, now or once a cancel on
#: its way lands.
UNCOVERED_STATUSES = frozenset({"unknown", "naked"})


def _order_views(client: AlpacaClient) -> tuple[list[OrderView], dict[tuple[str, float], str]]:
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
    """
    raw = client.list_orders(status="all", limit=500, nested=True)
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
    return views, ids


def _read_book(
    client: AlpacaClient,
) -> tuple[list[OrderView], dict[tuple[str, float], str], list]:
    """The orders, then the holding, in that order and never the other.

    A sell that fills between two reads must show up as fewer shares held,
    never as a lot whose stop has gone. Read the holding first and a stop that
    fires in between reads as terminal while its shares still read as held: the
    lot looks naked, and the back-fill puts a stop on a book that is flat by
    then, which a margin account takes as a short-sale stop. Read the orders
    first and the same fill shows as a position that is gone. The same rule as
    `protected_close._read_truth`.
    """
    orders, stop_ids = _order_views(client)
    return orders, stop_ids, client.list_positions()


def _bars_for(ticker: str, session, end: date) -> tuple[list[Bar], list[date]]:
    """Daily bars from the local cache, oldest first, with their dates.

    An empty list means "do not act" and never "flat": `average_true_range`
    returns None on short history rather than a partial average, and the caller
    skips the symbol with a reason.
    """
    from tradingagents_us.storage.price_cache import read_bars

    rows = read_bars(session, ticker, end - timedelta(days=BAR_LOOKBACK_DAYS), end)
    bars = [Bar(high=r.high, low=r.low, close=r.close) for r in rows]
    dates = [date.fromisoformat(r.bar_date) for r in rows]
    return bars, dates


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
) -> tuple[list[ManagedPosition], dict[str, list[Bar]]]:
    """Turn broker state into the pure module's inputs, skipping what it cannot
    describe honestly. Every exclusion is logged with its reason — a position
    that silently vanishes from the pass is indistinguishable from one the pass
    decided to leave alone."""
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

            bars, bar_dates = _bars_for(p.symbol, session, today)
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


def _naked_now(client: AlpacaClient, ticker: str) -> float:
    """The shares of `ticker` no stop covers, off the broker's book as it is now.

    The plan's figure comes from a read taken before any of the pass's writes,
    and a time exit before this one (release, confirm, sell, look up) can take
    a minute. A lot sold in that time, by a council SELL approved on the phone
    or by hand, is flat when its back-fill goes, and a margin account takes a
    sell stop on a flat book as a short-sale stop. So the back-fill is sized
    off orders read now and the holding read after them, as `_read_book` has
    it: 0 when the lot is gone or not long. Raises when its protection is
    ambiguous now, so nothing is placed and the pass fails.
    """
    orders = _order_views(client)[0]
    lot = next((p for p in client.list_positions() if p.symbol == ticker), None)
    if lot is None or lot.side != "long":
        return 0.0
    (row,) = coverage([PositionView(symbol=ticker, qty=lot.qty, side="long")], orders).symbols
    if row.indeterminate_qty > QTY_EPSILON:
        raise RuntimeError(
            f"back-fill not placed: {row.indeterminate_qty:g} shares' protection is ambiguous now"
        )
    return row.naked_qty


def _place_backfill(client: AlpacaClient, act: PlaceStop) -> None:
    """One back-fill stop, for no more than the shares `_naked_now` finds naked."""
    qty = min(act.quantity, _naked_now(client, act.ticker))
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
) -> int:
    """Apply the plan. One bad symbol must not stop the rest of the pass.

    `trade_date` dates the time-exit stamp; the pass's UTC date when omitted.
    Each time exit that did not close its position is appended to `unclosed`,
    so the caller can cover what it left behind (`_recover_unclosed`), and each
    one that may have left shares with no stop to `uncovered`, so it can say so.
    One whose exit was sent and never ruled out goes into `unsettled`, keyed to
    its stamp: that exit can still land, and a back-fill must look for it.
    """
    failures = 0
    for act in actions:
        try:
            if isinstance(act, RatchetStop):
                replaced = client.replace_order(act.stop_order_id, stop_price=act.new_stop)
                log.info("%-6s ratcheted, new order %s", act.ticker, replaced.id)
            elif isinstance(act, PlaceStop):
                _place_backfill(client, act)
            else:
                # Not DELETE /positions/{symbol}: the GTC stop reserves every
                # share of a protected position, so the broker refused that
                # close on exactly the positions that have one, and what it did
                # close carried a broker id and booked as a flatten.
                # close_with_protection releases the stop, confirms it, sells
                # the holding read after the release under the time-exit stamp,
                # and re-arms the stop in the same pass if the sell fails.
                outcome = close_with_protection(
                    client, act.ticker, trade_date=trade_date or datetime.now(UTC).date()
                )
                if not outcome.ok:
                    failures += 1
                    if unclosed is not None:
                        unclosed.append(act.ticker)
                    if uncovered is not None and outcome.status in UNCOVERED_STATUSES:
                        uncovered.append(f"{act.ticker} {outcome.status}")
                    if unsettled is not None and outcome.client_order_id:
                        unsettled[act.ticker] = outcome.client_order_id
                _log_close(outcome)
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
    stop still stands on it. That covers a stop still in `pending_cancel`, which
    may yet fill; one in `stopped`, whose fill is on its way; an exit that
    landed after all; a take-profit. Each reserves the shares or is about to
    sell them, and a stop placed beside it is refused while the lot is held and
    becomes a short once that sell fills.

    A name whose exit was sent and never ruled out (`unsettled`) is back-filled
    all the same, since it may never land, but through
    `protected_close.cover_beside_exit`: that exit can land between this read
    and the stop's POST, fill, and leave the stop on a flat book, so the stamp
    and the book are read again once the stop stands, and it is taken back if
    the exit got there first. A name it settles either way is off `uncovered`.

    Returns how many placements failed. A book it could not read, or a
    placement that failed, is also appended to `uncovered`: the names it was
    handed may have shares with no stop, and it could not put one there.
    """
    try:
        # Orders first, holding last (see `_read_book`).
        orders, stop_ids, positions = _read_book(client)
    except Exception as exc:  # noqa: BLE001 — reported and counted, never guessed past
        log.error("re-cover after failed exits: book unreadable, nothing placed: %s", exc)
        uncovered.append(f"re-cover could not read the book: {exc}")
        return 1
    held = [p for p in positions if p.symbol in tickers]
    if not held:
        return 0

    standing: dict[str, str] = {}
    for o in orders:
        gone = o.status in RELEASED_STATUSES or o.status == "replaced"
        a_working_stop = o.order_type in PROTECTIVE_TYPES and o.status in LIVE_STATUSES
        if o.side == "sell" and o.remaining_qty > QTY_EPSILON and not gone and not a_working_stop:
            standing.setdefault(o.symbol, f"{o.order_type} sell in {o.status}")
    for p in held:
        if p.symbol in standing:
            log.info("%-6s SKIP  re-cover: a %s still stands on it", p.symbol, standing[p.symbol])
    held = [p for p in held if p.symbol not in standing]

    report = coverage([PositionView(symbol=p.symbol, qty=p.qty, side="long") for p in held], orders)
    by_symbol = {row.symbol: row for row in report.symbols}
    managed, bars_by_ticker = _build_managed(
        client, repo, held, by_symbol, orders, stop_ids, entries, today
    )
    never_old = dataclasses.replace(config, max_bars=sys.maxsize)
    actions, skips = plan_actions(managed, bars_by_ticker, never_old)
    for skip in skips:
        log.info("%-6s SKIP  re-cover: %-20s %s", skip.ticker, skip.reason, skip.detail)
    backfills = [a for a in actions if isinstance(a, PlaceStop)]
    for act in backfills:
        _describe(act, True)
    unsettled = unsettled or {}
    failed = _execute(client, [a for a in backfills if a.ticker not in unsettled], today)
    if failed:
        uncovered.append(f"re-cover could not place {failed} back-fill stop(s)")
    for act in (a for a in backfills if a.ticker in unsettled):
        failed += _cover_beside_exit(client, act, unsettled[act.ticker], uncovered)
    return failed


def _cover_beside_exit(
    client: AlpacaClient, act: PlaceStop, stamp: str, uncovered: list[str]
) -> int:
    """One back-fill beside an exit that may still land; 1 if it is not settled."""
    try:
        outcome = cover_beside_exit(
            client, act.ticker, stamp=stamp, qty=act.quantity, stop_price=act.stop_price
        )
    except Exception as exc:  # noqa: BLE001 — one bad symbol must not stop the pass
        log.error("%-6s FAILED back-fill beside exit %s: %s", act.ticker, stamp, exc)
        uncovered.append(f"{act.ticker} back-fill beside exit {stamp} died: {exc}")
        return 1
    _log_close(outcome)
    if outcome.status in CLOSED_STATUSES or outcome.status == "unchanged":
        # Protected, or gone with nothing standing: the close's doubt is settled.
        uncovered[:] = [u for u in uncovered if not u.startswith(f"{act.ticker} ")]
        return 0
    uncovered.append(f"{act.ticker} {outcome.status} beside exit {stamp}")
    return 1


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
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    install_log_redaction()

    ks = FileKillSwitchReader(os.environ.get("KILL_SWITCH_FILE", default_kill_switch_path())).read()
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
    from sqlalchemy import create_engine

    from tradingagents_us.storage import TradeLogRepository

    repo = TradeLogRepository(
        engine=create_engine(args.db_url, future=True) if args.db_url else None
    )

    with AlpacaClient() as client:
        orders, stop_ids, positions_raw = _read_book(client)
        if not positions_raw:
            log.info("no open positions")
            return 0

        report = coverage(
            [PositionView(symbol=p.symbol, qty=p.qty, side="long") for p in positions_raw],
            orders,
        )
        by_symbol = {row.symbol: row for row in report.symbols}
        entries = _entry_dates(client)
        today = datetime.now(UTC).date()

        managed, bars_by_ticker = _build_managed(
            client, repo, positions_raw, by_symbol, orders, stop_ids, entries, today
        )

        actions, skips = plan_actions(managed, bars_by_ticker, config)

        for skip in skips:
            log.info("%-6s SKIP  %-20s %s", skip.ticker, skip.reason, skip.detail)

        if not actions:
            log.info("nothing to do (%d positions examined)", len(managed))
            return 0

        for act in actions:
            _describe(act, args.submit)

        if not args.submit:
            log.info("dry run — pass --submit to act")
            return 0

        unclosed: list[str] = []
        uncovered: list[str] = []
        unsettled: dict[str, str] = {}
        failures = _execute(
            client, actions, today, unclosed=unclosed, uncovered=uncovered, unsettled=unsettled
        )
        if unclosed:
            log.warning(
                "re-cover: time exits left %s open; re-reading the book", ", ".join(unclosed)
            )
            failures += _recover_unclosed(
                client, repo, set(unclosed), entries, today, config, uncovered, unsettled
            )
        if uncovered:
            log.error(
                "UNCOVERED: shares may have no stop, now or once a pending cancel lands, "
                "or a stop may stand on a lot already sold: %s",
                "; ".join(uncovered),
            )
            return EXIT_UNCOVERED
        return EXIT_FAILED if failures else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
