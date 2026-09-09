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
  * Nothing here opens or grows a position.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, date, datetime, timedelta

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
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
                    bars_held=_bars_since(bar_dates, entry),
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


def _execute(client: AlpacaClient, actions: list[Action]) -> int:
    """Apply the plan. One bad symbol must not stop the rest of the pass."""
    failures = 0
    for act in actions:
        try:
            if isinstance(act, RatchetStop):
                replaced = client.replace_order(act.stop_order_id, stop_price=act.new_stop)
                log.info("%-6s ratcheted, new order %s", act.ticker, replaced.id)
            elif isinstance(act, PlaceStop):
                placed = client.submit_order(
                    symbol=act.ticker,
                    qty=act.quantity,
                    side="sell",
                    order_type="stop",
                    # GTC: a day stop expires at the close and leaves the
                    # position naked overnight, which is the window the whole
                    # back-fill exists to close.
                    time_in_force="gtc",
                    stop_price=act.stop_price,
                )
                log.info("%-6s stop placed, order %s", act.ticker, placed.id)
            else:
                client.close_position(act.ticker)
                log.info("%-6s closed on age", act.ticker)
        except Exception as exc:  # noqa: BLE001 — one bad symbol must not stop the pass
            failures += 1
            log.error("%-6s FAILED: %s", act.ticker, exc)
    return failures


def main() -> int:
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

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
        positions_raw = client.list_positions()
        if not positions_raw:
            log.info("no open positions")
            return 0

        orders, stop_ids = _order_views(client)
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

        failures = _execute(client, actions)
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
