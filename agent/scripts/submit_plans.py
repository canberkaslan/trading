"""The submit pass of a parallel daily run: recorded decisions -> orders, one at a time.

With COUNCIL_PARALLELISM above 1, daily_run.sh councils tickers side by side
with `scripts.trade --plan-dir`, which records each decision and sends nothing.
This one process then takes the records in the order its tickers are given (the
universe order) and, for each, does what `scripts.trade` does after its council:
reads the account, positions and open orders from the broker, sizes the decision
under the kill switch and every cap, and runs the executor with all its guards.
Each ticker sees the orders the tickers before it just placed, exactly as in the
sequential run: their cost comes off the spendable cash, so the cash cap holds
across the batch without any lock between processes. The name and sector caps
count positions only, as the sequential run's do.

    python -m scripts.submit_plans --plan-dir DIR --run-id ID --date 2026-10-07 \\
        [--submit] AAPL MSFT ...

A record is taken (renamed) before anything is read, so it is acted on at most
once, and one written by another run, date or ticker is refused. Each ticker's
output ends in `  -> TICKER done` or `  -> TICKER FAILED (rc=N)`, which is what
daily_run.sh counts, and the exit code is 1 when any failed. A failure is what
it is in the sequential run (a broker error, an unsizable decision), plus a
record that is missing or refused.

The price reads are the one thing paced. The sequential run's are a council
apart; here they come back to back, and past the price feed's budget a read
comes back None, which skips the entry checks for a BUY (see PacedPrices).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
if str(_AGENT_ROOT / "vendor" / "tradingagents") not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT / "vendor" / "tradingagents"))

from scripts import trade  # noqa: E402
from tradingagents_us.execution.plans import (  # noqa: E402
    PlanRefusedError,
    claim_plan,
    load_claimed,
)
from tradingagents_us.log_redaction import install as install_log_redaction  # noqa: E402
from tradingagents_us.storage import TradeLogRepository, make_engine  # noqa: E402

log = logging.getLogger("submit_plans")

#: The price feed's budget: Polygon answers five requests a minute and a 429
#: past it (dataflows/polygon.py).
POLYGON_REQUESTS_PER_WINDOW = 5
POLYGON_WINDOW_S = 60.0


class PacedPrices:
    """`trade._fetch_current_price` for this pass: each name read once, and the
    reads spaced to the price feed's budget.

    Past the budget a read gives up and returns None. For a BUY that skips the
    executor's TP-headroom and stop-proximity checks and the gate's one-share
    cash check, and an open BUY nobody can price refuses every BUY after it, so
    the batch would not be the sequential run's. The first read waits out a
    whole window, which the last councils of the first pass may just have
    spent, and each later one its share of it. A last close does not move
    during the pass, so a name is read once; a None is not kept, so the next
    caller tries again.
    """

    def __init__(self, read: Callable[[str], float | None]) -> None:
        self._read = read
        self._prices: dict[str, float] = {}
        self._next_read = time.monotonic() + POLYGON_WINDOW_S

    def __call__(self, ticker: str) -> float | None:
        if ticker in self._prices:
            return self._prices[ticker]
        wait = self._next_read - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        price = self._read(ticker)
        self._next_read = time.monotonic() + POLYGON_WINDOW_S / POLYGON_REQUESTS_PER_WINDOW
        if price is not None:
            self._prices[ticker] = price
        return price


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send the decisions a parallel daily run recorded, one at a time."
    )
    parser.add_argument("--plan-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--submit", action="store_true",
                        help="Actually submit to Alpaca (default: dry run)")
    parser.add_argument("--db-url", default=os.environ.get("LOCAL_DATABASE_URL", "sqlite:///./local.db"))
    parser.add_argument("tickers", nargs="+", help="In the order to submit them")
    return parser


def submit_one(opts: argparse.Namespace, ticker: str, repo: TradeLogRepository) -> int:
    """`scripts.trade` after its council, for one recorded decision."""
    claimed = claim_plan(opts.plan_dir, ticker)
    if claimed is None:
        print(f"  no recorded decision for {ticker} in {opts.plan_dir} — nothing sent")
        return 1
    try:
        decision = load_claimed(
            claimed, run_id=opts.run_id, run_date=opts.date.isoformat(), ticker=ticker
        )
    except PlanRefusedError as exc:
        print(f"  REFUSED record: {exc} — nothing sent")
        return 1

    # The arguments the sequential run passes for this ticker, so every default
    # (method, risk per trade, caps) is trade.py's own.
    argv = ["--ticker", ticker, "--date", opts.date.isoformat()]
    args = trade.build_parser().parse_args(argv + (["--submit"] if opts.submit else []))
    limits = trade.portfolio_limits(args)

    trade._print_decision(decision)
    book = trade.read_book(ticker, limits)
    # The sequential run's pre-council gate, against the book as it stands now.
    # Pass 1 asked it before any of this run's orders existed, and the sizer's
    # cash cap is no substitute: it prices shares at the decision's entry, while
    # the order is a market order at whatever the price is.
    gate = trade.council_gate(ticker, book, limits)
    if not gate.run:
        print(f"\n=== NOT SENT ===\n  {ticker}: {gate.reason}")
        return 0
    sized = trade.size_order(args, limits, repo, decision, book)
    if sized is None:
        return 1
    order, current_price = sized
    return trade.execute(args, repo, order, decision, current_price, opts.date)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    install_log_redaction()
    trade._load_env()
    # One process, many tickers: keep each ticker's lines together in the run log.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)

    opts = build_parser().parse_args(argv)
    repo = TradeLogRepository(engine=make_engine(opts.db_url))

    failed: list[str] = []
    read_price = trade._fetch_current_price
    trade._fetch_current_price = PacedPrices(read_price)
    try:
        for ticker in opts.tickers:
            print(f"\n--- {ticker} submit @ {opts.date.isoformat()} ---")
            try:
                rc = submit_one(opts, ticker, repo)
            except Exception:  # noqa: BLE001 — one ticker's crash must not cost the rest
                log.exception("submit of %s failed", ticker)
                rc = 1
            if rc == 0:
                print(f"  -> {ticker} done")
            else:
                print(f"  -> {ticker} FAILED (rc={rc})")
                failed.append(ticker)
    finally:
        trade._fetch_current_price = read_price
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
