"""End-to-end CLI: decide -> size -> (optional) Alpaca paper submit.

Single command for the daily flow:

    # Dry run (default): see what would happen, no broker call, no LLM cost
    python -m scripts.trade --ticker AAPL --use-cached

    # Real LLM call (~$0.50-1.50 cost, 5-10 min)
    python -m scripts.trade --ticker NVDA

    # Submit to Alpaca paper (after a fresh decision)
    python -m scripts.trade --ticker AAPL --use-cached --submit

The cached path replays the Phase 2 AAPL state — perfect for end-to-end
validation without spending LLM tokens.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# Make package + vendor importable when running as a script
_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
if str(_AGENT_ROOT / "vendor" / "tradingagents") not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT / "vendor" / "tradingagents"))

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient  # noqa: E402
from tradingagents_us.dataflows.polygon import PolygonClient  # noqa: E402
from tradingagents_us.dataflows.sector_map import sector_for  # noqa: E402
from tradingagents_us.execution import ExecutionConfig, submit_order  # noqa: E402
from tradingagents_us.execution.submit_lock import (  # noqa: E402
    SubmitLockUnavailableError,
    submit_section,
)
from tradingagents_us.graph.pipeline import (  # noqa: E402
    _parse_pm_output,
    _parse_trader_output,
    propagate,
)
from tradingagents_us.log_redaction import install as install_log_redaction  # noqa: E402
from tradingagents_us.risk.cash_budget import (  # noqa: E402
    PendingBuy,
    reserved_cash_for_open_buys,
    spendable_cash,
)
from tradingagents_us.risk.circuit_breaker import CircuitBreaker  # noqa: E402
from tradingagents_us.risk.kill_switch import (  # noqa: E402
    CachedKillSwitchReader,
    FileKillSwitchReader,
)
from tradingagents_us.risk.market_inputs import (  # noqa: E402
    average_dollar_volume,
    count_correlated,
    recent_loss_streak,
    rolling_price_stats,
)
from tradingagents_us.risk.portfolio_limits import PortfolioContext, PortfolioLimits  # noqa: E402
from tradingagents_us.risk.precouncil import should_council  # noqa: E402
from tradingagents_us.risk.sizer import MarketContext, size_from_decision  # noqa: E402
from tradingagents_us.schemas import AgentDecision, AgentReasoning  # noqa: E402
from tradingagents_us.storage import TradeLogRepository  # noqa: E402

log = logging.getLogger("trade")


def _load_env() -> None:
    env_path = _AGENT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"')
        if v:
            os.environ.setdefault(k, v)


def _decision_from_cached(ticker: str) -> AgentDecision:
    """Load a previously-generated full_states_log_*.json from disk."""
    home = Path.home() / ".tradingagents" / "logs" / ticker / "TradingAgentsStrategy_logs"
    if not home.exists():
        raise FileNotFoundError(f"no cached state for {ticker} at {home}")
    files = sorted(home.glob("full_states_log_*.json"))
    if not files:
        raise FileNotFoundError(f"no full_states_log_*.json in {home}")
    state = json.loads(files[-1].read_text())

    pm = state.get("final_trade_decision", "") or ""
    trader = state.get("trader_investment_decision") or state.get("trader_investment_plan") or ""
    rating, pt, horizon = _parse_pm_output(pm)
    entry, stop, size = _parse_trader_output(trader)

    reasoning = [
        AgentReasoning(agent="portfolio_manager", model="claude-opus-4-7",
                       summary=pm[:1500], tokens_in=0, tokens_out=0, latency_ms=0),
    ]

    # Use the ORIGINAL trade_date from the cached state as the decision
    # timestamp — never "now". Otherwise the stale-decision guard is silently
    # bypassed for replays (which is exactly the bug that caused the
    # 2026-06-01 AAPL -$5.64 incident).
    trade_date = state.get("trade_date")
    if trade_date:
        try:
            decision_ts = datetime.fromisoformat(trade_date).replace(tzinfo=UTC)
        except ValueError:
            decision_ts = datetime.now(UTC)
    else:
        decision_ts = datetime.now(UTC)

    return AgentDecision(
        ticker=ticker, market="US", quote_currency="USD",
        rating=rating, entry_price=entry, stop_loss=stop,
        suggested_size_pct=size, price_target=pt, time_horizon=horizon,
        reasoning=reasoning, final_decision_text=pm[:8000],
        timestamp_utc=decision_ts, decision_id=str(uuid.uuid4()),
    )


def _fetch_current_price(ticker: str) -> float | None:
    """Pull the most recent close from Polygon (Stocks Starter is 15-min
    delayed but close enough for sanity checks). Returns None if unavailable."""
    try:
        with PolygonClient() as p:
            resp = p.previous_close(ticker)
            results = resp.get("results") or []
            if not results:
                return None
            return float(results[0].get("c", 0)) or None
    except Exception as e:
        log.warning("could not fetch current price for %s: %s", ticker, e)
        return None


def _print_decision(d: AgentDecision) -> None:
    print("\n=== AGENT DECISION ===")
    print(f"  Ticker:      {d.ticker} ({d.market}/{d.quote_currency})")
    print(f"  Rating:      {d.rating}")
    print(f"  Entry:       ${d.entry_price}")
    print(f"  Stop:        ${d.stop_loss}")
    print(f"  Price Tgt:   ${d.price_target}")
    print(f"  Horizon:     {d.time_horizon}")
    print(f"  Size (LLM):  {d.suggested_size_pct * 100:.2f}% of portfolio")
    print(f"  Decision ID: {d.decision_id}")


def _cap_fraction(raw: str) -> float:
    """argparse type for a cap: a fraction in (0, 1], never a percent.

    `10` meaning ten percent is an easy thing to type, and the easier still once
    a flag is fed from an env var. Taken literally it is a 1000% single-name
    cap, or a cash utilization that spends ten times the cash the account has,
    and nothing downstream would object.
    Refusing it here fails the ticker loudly before any broker call or model
    spend. Zero is refused too: it cannot be a deliberate cap, and it would turn
    every order into a silent policy refusal.
    """
    problem = f"must be a fraction in (0, 1], e.g. 0.10 for 10%; got {raw!r}"
    try:
        value = float(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(problem) from None
    if not (math.isfinite(value) and 0.0 < value <= 1.0):
        raise argparse.ArgumentTypeError(problem)
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Decide + size + (optional) submit to Alpaca paper."
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--use-cached", action="store_true",
                        help="Replay the most recent cached state for this ticker (no LLM cost)")
    parser.add_argument("--method", choices=["atr", "llm_pct"], default="atr",
                        help="Risk sizing method")
    parser.add_argument("--risk-per-trade", type=float, default=0.005)
    # The caps. These defaults ARE the live values: daily_run.sh passes none of
    # these flags, and no env var feeds them (see the note in daily_run.sh).
    parser.add_argument("--max-position-pct", type=_cap_fraction, default=0.10,
                        help="Single-name cap as a fraction of equity (default 0.10)")
    parser.add_argument("--max-sector-pct", type=_cap_fraction, default=0.30,
                        help="Per-sector cap as a fraction of equity (default 0.30)")
    parser.add_argument("--max-cash-utilization", type=_cap_fraction, default=1.0,
                        help="Fraction of spendable cash one new BUY may use; 1.0 = all "
                             "of it but never borrow, < 1.0 keeps dry powder (default 1.0)")
    parser.add_argument("--submit", action="store_true",
                        help="Actually submit the order to Alpaca (default: dry run)")
    parser.add_argument("--hold", action="store_true",
                        help="Save the order as PENDING (no broker call) — "
                             "wait for mobile approval")
    parser.add_argument("--refuse-outside-hours", action="store_true")
    parser.add_argument("--no-persist", action="store_true",
                        help="Skip writing to the trade log DB")
    parser.add_argument("--db-url", default=os.environ.get("LOCAL_DATABASE_URL", "sqlite:///./local.db"),
                        help="Trade log DB URL (default: SQLite file in CWD). "
                             "Production sets DATABASE_URL to Aurora — this flag overrides.")
    return parser


@dataclass(frozen=True)
class _Book:
    """What the account holds and has committed, read in one pass."""

    acct: Any
    existing_by_ticker: dict[str, float]
    held_qty: int
    open_buys: list[PendingBuy]
    reserved: float | None
    spendable: float | None


def _read_book(
    ticker: str,
    limits: PortfolioLimits,
    *,
    verbose: bool,
    price_of: Callable[[str], float | None] = _fetch_current_price,
) -> _Book:
    """Equity, positions and open BUYs from Alpaca, and the cash they leave.

    Called twice per ticker: before the council, for the pre-council gate, and
    again under the submit lock, for the sizer. The daily run councils several
    tickers at once, so what the first read saw can be minutes stale by the time
    the order is sized; another ticker may have spent the cash in between.
    """
    # 1. Live Alpaca context (equity + CURRENT positions so we don't re-buy
    #    a name we already hold up to its cap — the daily run would otherwise
    #    accumulate the same Overweight ticker every day).
    existing_by_ticker: dict[str, float] = {}
    held_qty = 0
    with AlpacaClient() as ac:
        acct = ac.account()
        for p in ac.list_positions():
            existing_by_ticker[p.symbol] = abs(p.market_value)
            if p.symbol == ticker:
                held_qty = int(p.qty)
        # daily_run.sh runs this script once per ticker as a separate process, all
        # before any of the post-close orders fill. Without reserving what earlier
        # tickers already committed, all eleven size against the same cash balance
        # and the sum blows straight through it.
        #
        # Pagination: Alpaca list_orders has a 500-order max per call. When the book
        # has >500 open orders, a single call truncates and under-reserves cash.
        # Loop until fewer than 500 orders are returned (final page).
        open_buys: list[PendingBuy] = []
        page_limit = 500
        fetched_count = page_limit
        until_timestamp: str | None = None

        while fetched_count >= page_limit:
            # AlpacaClient.list_orders does not expose 'until' directly; use httpx
            query = f"/orders?status=open&limit={page_limit}&direction=desc"
            if until_timestamp is not None:
                query += f"&until={until_timestamp}"
            page = ac._http.get(ac.base_url + query).json()
            if not isinstance(page, list):
                break
            fetched_count = len(page)
            if fetched_count == 0:
                break

            for o_dict in page:
                if o_dict.get("side", "").upper() != "BUY":
                    continue
                qty = float(o_dict["qty"])
                filled = float(o_dict.get("filled_qty", 0))
                limit_price = (
                    float(o_dict["limit_price"]) if o_dict.get("limit_price") else None
                )
                open_buys.append(
                    PendingBuy(
                        symbol=o_dict["symbol"],
                        unfilled_qty=qty - filled,
                        limit_price=limit_price,
                    )
                )

            # Next page starts BEFORE the oldest (last in desc order) of this page
            if fetched_count >= page_limit and page:
                until_timestamp = page[-1]["submitted_at"]
    if verbose:
        print("\n=== ALPACA ACCOUNT ===")
        print(f"  Number:    {acct.account_number} ({acct.status})")
        print(f"  Equity:    ${acct.portfolio_value:,.2f}")
        print(f"  Cash:      ${acct.cash:,.2f}")
        print(f"  Buying pw: ${acct.buying_power:,.2f}")
        print(f"  PDT:       {acct.pattern_day_trader}")
        print(f"  Already holding {ticker}: {held_qty} shares "
              f"(${existing_by_ticker.get(ticker, 0.0):,.0f})")
        print(f"  Caps:      name {limits.max_position_pct * 100:g}% / "
              f"sector {limits.max_sector_pct * 100:g}% / "
              f"cash {limits.max_cash_utilization * 100:g}% of spendable")

    reserved = reserved_cash_for_open_buys(open_buys, price_of)
    spendable = spendable_cash(acct.cash, reserved)
    if verbose:
        if reserved is None:
            print(f"  Open BUYs: {len(open_buys)} — UNPRICEABLE, refusing new exposure")
        else:
            print(f"  Open BUYs: {len(open_buys)} reserving ${reserved:,.2f} "
                  f"-> spendable ${spendable:,.2f}")
    return _Book(acct, existing_by_ticker, held_qty, open_buys, reserved, spendable)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    # httpx logs every request URL at INFO, and Polygon carries its key in the
    # query string — so a correctly-configured run writes a live credential
    # into its own log, once per call. Installed immediately after
    # basicConfig so it covers the handler basicConfig just created.
    install_log_redaction()
    _load_env()

    args = build_parser().parse_args()
    # One set of caps for the whole run. The pre-council gate and the sizer must
    # budget with the same cash utilization, or the gate councils names the
    # sizer then cannot afford (or skips names it could).
    limits = PortfolioLimits(
        max_position_pct=args.max_position_pct,
        max_sector_pct=args.max_sector_pct,
        max_cash_utilization=args.max_cash_utilization,
    )

    from sqlalchemy import create_engine
    repo = (
        None if args.no_persist
        else TradeLogRepository(engine=create_engine(args.db_url, future=True))
    )

    # The RUN's trading date anchors the idempotency key. Decisions finishing
    # after midnight UTC must not shift the key to the next day (silent
    # duplicate-block tomorrow / missed dedupe on retry).
    try:
        run_date = date.fromisoformat(args.date)
    except ValueError:
        run_date = datetime.now(UTC).date()

    # Prices are fetched once per process: the book is read twice, and the
    # second read happens under the submit lock, where a slow Polygon call
    # would hold every other ticker up.
    prices: dict[str, float | None] = {}

    def price_of(symbol: str) -> float | None:
        if symbol not in prices:
            prices[symbol] = _fetch_current_price(symbol)
        return prices[symbol]

    book = _read_book(args.ticker, limits, verbose=True, price_of=price_of)
    held_qty, spendable = book.held_qty, book.spendable

    # 2. Can this name possibly be acted on today? Asked BEFORE the council.
    #
    # The council costs five to ten minutes and roughly a dollar of model
    # spend, and the cash budget above already determines, with certainty, that
    # some names cannot be bought at all. Paying for reasoning whose conclusion
    # the arithmetic has already overruled is pure waste — at eleven tickers a
    # rounding error, at a hundred about $68 a day.
    #
    # The gate only ever rejects the provably impossible. "Unpromising" is not
    # its business: that judgement belongs to the sizer, with the council's
    # output in hand. And a held name is never skipped, because the council is
    # this system's only discretionary exit.
    _gate = should_council(
        args.ticker,
        held_qty=held_qty,
        spendable=spendable,
        price=price_of(args.ticker),
        max_cash_utilization=limits.max_cash_utilization,
    )
    if not _gate.run:
        # No decision row is written, so this line is the only record that the
        # ticker was considered at all.
        log.info("skipping council for %s: %s", args.ticker, _gate.reason)
        print(f"\n=== COUNCIL SKIPPED ===\n  {args.ticker}: {_gate.reason}")
        return 0
    log.info("councilling %s: %s", args.ticker, _gate.reason)

    # 3. Get decision
    if args.use_cached:
        log.info("loading cached decision for %s", args.ticker)
        decision = _decision_from_cached(args.ticker)
    else:
        log.info(
            "running fresh LLM pipeline for %s @ %s (~5-10 min, ~$0.50-1.50)",
            args.ticker, args.date,
        )
        decision = propagate(args.ticker, args.date)
    _print_decision(decision)

    # Recorded before anything below can fail: fifteen minutes of council is
    # the expensive part, and a lock or broker problem must not lose it.
    if repo is not None:
        repo.save_decision(decision)
        log.info("persisted decision %s", decision.decision_id)

    # Market inputs for sizing, fetched OUTSIDE the submit lock: each can be a
    # slow Polygon call, and every other ticker waits while the lock is held.
    market = _market_inputs(args.ticker, repo, book, price_of, prices)

    # 4. Size and submit, one ticker at a time.
    #
    # The daily run councils several tickers at once. Everything up to here is
    # independent per ticker; what follows is not. A BUY is sized against the
    # cash the others left, so the book is read again under a lock every order
    # path takes, and nobody else can submit between that read and this order.
    # An exit spends no cash and must not be lost to a lock problem, so it
    # goes ahead without the lock when it cannot have it (submit_lock.py).
    exit_only = decision.rating not in _CASH_SPENDING_RATINGS
    try:
        with submit_section(exit_only=exit_only) as locked:
            fresh = _fresh_book(args.ticker, limits, price_of)
            if fresh is None:
                if not exit_only:
                    log.error("%s: the book could not be re-read; no BUY is sized "
                              "from a stale one", args.ticker)
                    return 1
                log.warning("%s: the book could not be re-read; sizing the exit "
                            "from the pre-council read", args.ticker)
                fresh = book
            if fresh.spendable != spendable or fresh.held_qty != held_qty:
                print(f"  Book moved during the council: spendable "
                      f"{_money(spendable)} -> {_money(fresh.spendable)}, "
                      f"held {held_qty} -> {fresh.held_qty} shares")
            if not locked:
                print("  Submit lock NOT held: exit sent unordered")
            return _act_on_decision(args, limits, repo, run_date, decision, fresh, market)
    except SubmitLockUnavailableError as exc:
        log.error("%s: %s — decision recorded, nothing sized or submitted", args.ticker, exc)
        return 1


#: Ratings whose order spends cash. Everything else sells or does nothing.
_CASH_SPENDING_RATINGS = ("Buy", "Overweight")
#: Pause before the one retry of a failed account read.
_BOOK_RETRY_PAUSE_S = 2.0


def _fresh_book(
    ticker: str, limits: PortfolioLimits, price_of: Callable[[str], float | None]
) -> _Book | None:
    """The book as it stands now, printed for the audit trail; None if unreadable."""
    for attempt in (1, 2):
        try:
            print("\n=== BOOK AT SIZING (under the submit lock) ===")
            return _read_book(ticker, limits, verbose=True, price_of=price_of)
        except Exception as exc:  # noqa: BLE001 — any read failure: retry once, then report
            log.warning("%s: account re-read failed (attempt %d): %s", ticker, attempt, exc)
            if attempt == 1:
                time.sleep(_BOOK_RETRY_PAUSE_S)
    return None


@dataclass(frozen=True)
class _Market:
    """Per-ticker market inputs to sizing, gathered before the submit lock."""

    adv: float | None
    current_price: float | None
    stats: tuple[float, float] | None
    sectors: dict[str, str | None]
    prices: dict[str, float | None]


def _market_inputs(
    ticker: str,
    repo: TradeLogRepository | None,
    book: _Book,
    price_of: Callable[[str], float | None],
    prices: dict[str, float | None],
) -> _Market:
    symbols = {ticker, *book.existing_by_ticker, *(b.symbol for b in book.open_buys)}
    return _Market(
        adv=average_dollar_volume(ticker),
        current_price=price_of(ticker),
        stats=rolling_price_stats(ticker, repo=repo),
        sectors={sym: sector_for(sym) for sym in sorted(symbols)},
        prices=prices,
    )


def _sector_of(symbol: str, known: dict[str, str | None]) -> str | None:
    """Sector from the pre-lock lookups; a symbol new since then is looked up."""
    if symbol not in known:
        known[symbol] = sector_for(symbol)
    return known[symbol]


def _known_price(symbol: str, market: _Market) -> float | None:
    """A price already in hand, never a fetch: this runs under the submit lock."""
    return market.prices.get(symbol)


def _money(value: float | None) -> str:
    return "unpriceable" if value is None else f"${value:,.2f}"


def _pending_buy_exposure(
    open_buys: list[PendingBuy], price_of: Callable[[str], float | None]
) -> dict[str, float]:
    """Notional of open BUYs per symbol: exposure the book is about to hold.

    Tonight's earlier tickers' BUYs have not filled yet, so the positions list
    does not show them, and the single-name and sector caps would let several
    BUYs into one sector that together break it. An unpriceable one is left
    out here; the cash budget already refuses new exposure in that case.
    """
    exposure: dict[str, float] = {}
    for buy in open_buys:
        price = buy.limit_price or price_of(buy.symbol)
        if price and buy.unfilled_qty > 0:
            exposure[buy.symbol] = exposure.get(buy.symbol, 0.0) + buy.unfilled_qty * price
    return exposure


def _act_on_decision(
    args: argparse.Namespace,
    limits: PortfolioLimits,
    repo: TradeLogRepository | None,
    run_date: date,
    decision: AgentDecision,
    book: _Book,
    market: _Market,
) -> int:
    """Size the decision against ``book`` and submit (or hold/dry-run)."""
    acct = book.acct
    existing_by_ticker = book.existing_by_ticker
    held_qty = book.held_qty
    spendable = book.spendable

    # An entry and a stop are what size a BUY. A Sell closes a position and is
    # sized off the holding, so requiring them there discarded exit signals: the
    # trader agent has no reason to quote an entry price for a name it wants out
    # of, and this returned 1 before writing any row — so the exit never reached
    # the broker, never reached the DB, and daily_run.sh counted it as a generic
    # ticker failure rather than a dropped trade.
    #
    # Hold/Underweight are non-actionable by design: the sizer rejects them with
    # "non-actionable rating=X", writes a row with risk_approved=False, and the
    # run completes successfully. Missing entry/stop is only a failure for
    # actionable ratings (Buy/Overweight).
    actionable = decision.rating in ("Buy", "Overweight")
    if actionable and not (decision.entry_price and decision.stop_loss):
        log.warning("decision missing entry/stop — cannot size; aborting before risk layer")
        return 1


    # Real liquidity, from the bars the price cache already holds. The old
    # hardcoded $1B meant the $100k floor could never reject anything, so a
    # thinly traded name looked as liquid as SPY to the risk layer.
    adv = market.adv
    if adv is None:
        # No bars is not "infinitely liquid". Fall back to the floor itself so
        # the check neither waves the order through nor blocks on a data gap.
        adv = limits.min_liquidity_adv
        print(f"  ADV:       unavailable for {args.ticker} — using the floor")
    else:
        print(f"  ADV:       ${adv:,.0f} (20d average dollar volume)")

    # One Polygon read, used both as the sizing reference below and as the live
    # price the execution guards check against further down.
    current_price = market.current_price

    # Use entry as the price proxy for sizing (a live quote would be better, but
    # the entry is what the stop is measured against, so they stay consistent).
    # A Sell is sized off the holding and sent as a market sell, so its entry
    # prices nothing, and it is never the reference: the last close, and only
    # then the position's own mark. Taking the model's entry first let it stand
    # in for a missing print (no_reference_price could not see the gap) and put
    # the model's number, not the market's, in front of the anomaly gate, which
    # refused a good exit whenever the entry was off.
    #
    # All three can still come up empty, and that is not a run failure: a Sell
    # for a name with no quote and nothing held, or for a held name whose mark
    # reads 0. The risk layer refuses a sell at 0.0 as `no_reference_price` (and
    # the first also as `nothing_held_to_sell`). Let it through at 0.0 so that
    # refusal is written as an order row. Returning early here is what turned a
    # dropped exit into a generic ticker failure in daily_run.sh, and left the
    # run with zero rows — which `actionability` reads as `idle` and the alerter
    # stays silent on.
    if decision.rating == "Sell":
        ref_price = current_price
    else:
        ref_price = decision.entry_price or current_price
    if not ref_price and held_qty:
        ref_price = existing_by_ticker.get(args.ticker, 0.0) / held_qty
    if not ref_price:
        log.warning("no reference price for %s — risk layer will refuse", args.ticker)
        ref_price = 0.0

    # The circuit breaker's price-anomaly check used to be handed
    # `rolling_mean = ref_price` and a 2% band, which makes its z-score
    # |p - p| / std — exactly zero, for every ticker, on every run. It could not
    # fire, while reading as a working control in every log. Real stats now come
    # from the same bar cache the rest of the risk layer uses.
    #
    # None means "not enough history", and the check is then SKIPPED rather than
    # given a fabricated band: rolling_std=0.0 makes the breaker's own
    # `if rolling_std > 0` guard skip it, which is the honest branch. Inventing
    # a width is what made this inert in the first place.
    stats = market.stats
    if stats is None:
        rolling_mean, rolling_std = ref_price, 0.0
        log.info("price-anomaly check skipped for %s — not enough bars", args.ticker)
    else:
        rolling_mean, rolling_std = stats

    market_ctx = MarketContext(
        current_price=ref_price,
        rolling_mean=rolling_mean,
        rolling_std=rolling_std,
        # No synthesized ATR: the sizer now measures the real entry-to-stop
        # distance itself, and a proxy here is what made positions 2.5x.
        atr=None,
        avg_daily_volume_usd=adv,
        sector=_sector_of(args.ticker, market.sectors),
    )
    # Sector exposure from the live book. `sector_for` resolves through the
    # static map, the DB cache and Polygon's reference data, then falls back to
    # "Unknown". Unknowns share one bucket on purpose: the cap used to be
    # skipped for them entirely, which is unlimited concentration in the names
    # we know least about. The cost is a false positive when that bucket fills
    # with unrelated names.
    #
    # Exposure counts open BUYs as well as positions: with tickers sized one
    # after another in the same night, the earlier ones' orders have not filled,
    # and the caps must see them. Priced from the limit (no Polygon call here,
    # under the lock) and, failing that, from the prices already fetched.
    exposure_by_ticker = dict(existing_by_ticker)
    for sym, value in _pending_buy_exposure(
        book.open_buys, lambda s: _known_price(s, market)
    ).items():
        exposure_by_ticker[sym] = exposure_by_ticker.get(sym, 0.0) + value
    existing_by_sector: dict[str, float] = {}
    for sym, value in exposure_by_ticker.items():
        sec = _sector_of(sym, market.sectors)
        if sec:
            existing_by_sector[sec] = existing_by_sector.get(sec, 0.0) + value

    portfolio_ctx = PortfolioContext(
        equity=acct.portfolio_value,
        existing_position_values_by_ticker=exposure_by_ticker,
        existing_position_values_by_sector=existing_by_sector,
        high_correlation_count=count_correlated(args.ticker, list(exposure_by_ticker)),
        # Settled cash net of pending BUYs, so an order can't be sized off
        # appreciating equity and borrow. The live paper book already drifted to
        # negative cash on equity-only sizing (2026-08-13: -$856 on $108k).
        # An unpriceable pending BUY collapses to 0.0 — refuse, never skip: passing
        # None here means "no cash figure supplied" and would DISABLE the cap.
        available_cash=0.0 if spendable is None else spendable,
    )
    # Real mobile kill switch (was a hardcoded RUN stub): the API writes
    # KILL_SWITCH_PATH. At the circuit breaker PAUSE_NEW blocks an entry but
    # lets an exit through, and FLATTEN_ALL blocks both (the flatten owns the
    # book). daily_run.sh additionally pre-checks + flattens.
    cb = CircuitBreaker(kill_switch=CachedKillSwitchReader(FileKillSwitchReader()))
    # The consecutive-loss halt counts what `record_trade_result` was told, and
    # nothing ever called it — the counter sat at zero from process start to
    # exit, so a five-loss run halted nothing. Each ticker runs in its own
    # process, so the streak cannot be accumulated in memory either; it is
    # replayed from the realized ledger, which already knows the answer.
    streak = recent_loss_streak(repo=repo)
    for _ in range(streak):
        cb.record_trade_result(profitable=False)
    if streak:
        log.info("replayed %d consecutive realized losses into the breaker", streak)

    # 4. Risk sizing -> TradeOrder
    order = size_from_decision(
        decision=decision,
        account_equity=acct.portfolio_value,
        market_ctx=market_ctx,
        portfolio_ctx=portfolio_ctx,
        circuit_breaker=cb,
        method=args.method,
        risk_per_trade=args.risk_per_trade,
        portfolio_limits=limits,
        # Alpaca's last_equity is the previous close — the session's opening
        # equity, which is what the daily-drawdown halt measures against. 0.0
        # means Alpaca omitted it; pass None so the check is skipped rather
        # than run against a bogus baseline.
        session_open_equity=acct.last_equity or None,
        # Bounds a sell to what is actually held. Without it a sell is sized off
        # the risk budget and can exceed the position, which the executor would
        # submit as an unprotected short.
        held_quantity=held_qty,
    )

    notional = order.quantity * (decision.entry_price or ref_price or 0)
    pct = notional / acct.portfolio_value * 100 if acct.portfolio_value > 0 else 0
    print("\n=== TRADE ORDER ===")
    print(f"  Side:        {order.side}")
    print(f"  Quantity:    {order.quantity} shares (method={args.method})")
    print(f"  Notional:    ${notional:,.2f}  ({pct:.2f}% of equity)")
    print(f"  Stop:        ${order.stop_loss}")
    print(f"  Approved:    {order.risk_approved}")
    if order.rejection_reasons:
        print(f"  Reasons:     {order.rejection_reasons}")

    # 5. Submit / hold / dry-run
    if current_price:
        print(f"\n  Current price (Polygon, delayed): ${current_price:.2f}")

    if args.hold:
        # Persist the order in PENDING state — mobile approval will submit later.
        # Still run all the guards so we don't store a bad order.
        from tradingagents_us.schemas import OrderUpdate

        guard_config = ExecutionConfig(dry_run=True, refuse_outside_hours=False)
        guard_result = submit_order(order, config=guard_config, decision=decision,
                                    current_price=current_price, trade_date=run_date)
        if not guard_result.dry_run or guard_result.update.status == "REJECTED":
            # Guards failed even in dry-run -> reject before persisting
            if repo is not None:
                repo.save_order(order, broker_order_id=None)
                repo.append_update(guard_result.update)
            print(f"\n=== HOLD: REJECTED by guards ===\n  Reasons: {guard_result.refusal_reasons}")
            return 1

        if repo is not None:
            repo.save_order(order, broker_order_id=None)
            repo.append_update(OrderUpdate(
                order_id=order.order_id, status="PENDING",
                error_message="awaiting_mobile_approval",
                timestamp_utc=datetime.now(UTC),
            ))
        print("\n=== HOLD: order persisted as PENDING ===")
        print(f"  Order ID:    {order.order_id}")
        print(f"  Approve via: POST /v1/orders/{order.order_id}/approve (mobile)")
        print(f"  Or CLI:      curl -X POST http://localhost:8000/v1/orders/{order.order_id}/approve")
        return 0

    config = ExecutionConfig(
        dry_run=not args.submit, refuse_outside_hours=args.refuse_outside_hours
    )
    result = submit_order(order, config=config, decision=decision,
                          current_price=current_price, trade_date=run_date)

    if repo is not None:
        repo.save_order(order, broker_order_id=result.broker_order_id)
        repo.append_update(result.update)
        log.info("persisted order %s + update status=%s", order.order_id, result.update.status)

    print("\n=== EXECUTION RESULT ===")
    print(f"  Dry run:     {result.dry_run}")
    print(f"  Submitted:   {result.submitted}")
    print(f"  Status:      {result.update.status}")
    if result.broker_order_id:
        print(f"  Broker ID:   {result.broker_order_id}")
    if result.refusal_reasons:
        print(f"  Refusals:    {result.refusal_reasons}")
    if not args.submit:
        print(
            "\n(--submit not set — no broker call made. "
            "Re-run with --submit to send to Alpaca paper.)"
        )
    # Exit non-zero ONLY on an operational failure (broker/API error). A policy
    # refusal — non-actionable Hold, risk guard, PDT, market closed — is the
    # intended "no trade today" outcome and must not mark the daily run failed.
    return 1 if result.error else 0


if __name__ == "__main__":
    sys.exit(main())
