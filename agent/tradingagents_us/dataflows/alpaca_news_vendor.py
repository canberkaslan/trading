"""Register Alpaca/Benzinga as a `news_data` vendor in the upstream router.

Upstream routes every news call through `route_to_vendor`, which looks the
method up in `VENDOR_METHODS` and walks the vendor chain configured in
`data_vendors`. That is a real extension point, so this adds a vendor to it
rather than patching the tool functions — a subtree pull can then move the
tools freely without silently dropping our feed.

Why add one at all. The configured chain is `news_data: "yfinance"`, and it
works: yfinance returns real articles today. But its wire is general financial
media (Motley Fool, Forkast), tagged loosely and written for readers, while
Benzinga is a trading wire with per-article symbol tags and minute timestamps.
For a decision made at a specific moment about a specific name, that difference
is the whole point.

This does NOT switch the default. Registration only makes "alpaca" selectable;
the run keeps whatever `data_vendors` says until someone changes it
deliberately. Upstream is explicit (#988/#289) that an unrequested fallback is
a bug, and quietly re-pointing the news feed from under a config would be the
same mistake in the other direction.

Signature note: the router calls implementations positionally with the tool's
own arguments, so the adapters here must accept yfinance's exact signature —
`(ticker, start_date, end_date)` and `(curr_date, look_back_days, limit)` — and
translate, rather than expose `news_block`'s shape.
"""

from __future__ import annotations

from tradingagents_us.dataflows.alpaca_news import AlpacaNewsClient, _creds, _fmt

# The window the router hands us is a date range; the client takes a lookback in
# days. Anything longer than this is a backtest-shaped request that the live news
# endpoint is the wrong tool for, so it is clamped rather than silently served.
_MAX_LOOKBACK_DAYS = 30


def _days_between(start_date: str, end_date: str) -> int:
    from datetime import date

    try:
        s = date.fromisoformat(start_date[:10])
        e = date.fromisoformat(end_date[:10])
    except ValueError:
        return 5
    return max(1, min((e - s).days or 1, _MAX_LOOKBACK_DAYS))


def get_alpaca_news(ticker: str, start_date: str, end_date: str) -> str:
    """Ticker news. Raises on failure — `news_data` is a core category upstream,
    and a core vendor that fails must be loud so the chain can move on."""
    if _creds() is None:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET not set")
    symbol = ticker.upper()
    with AlpacaNewsClient() as nc:
        articles = nc.news(symbols=symbol, lookback_days=_days_between(start_date, end_date))
    return f"## {symbol} News (Alpaca/Benzinga), {start_date} to {end_date}:\n{_fmt(articles, focus=symbol)}"


def get_alpaca_global_news(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> str:
    """The untagged wire, for the macro/global view."""
    if _creds() is None:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_API_SECRET not set")
    with AlpacaNewsClient() as nc:
        articles = nc.news(
            symbols=None,
            limit=limit or 15,
            lookback_days=min(look_back_days or 5, _MAX_LOOKBACK_DAYS),
        )
    return f"## Global Market News (Alpaca/Benzinga), as of {curr_date}:\n{_fmt(articles, focus=None)}"


def register() -> bool:
    """Add "alpaca" to the news_data vendor tables. Idempotent.

    Returns False rather than raising when the upstream module has moved, so a
    subtree pull that reshapes the router costs us this feed and not the run.
    """
    try:
        from tradingagents.dataflows import interface
    except ImportError:
        return False

    methods = getattr(interface, "VENDOR_METHODS", None)
    if not isinstance(methods, dict):
        return False

    added = False
    for name, impl in (
        ("get_news", get_alpaca_news),
        ("get_global_news", get_alpaca_global_news),
    ):
        table = methods.get(name)
        if isinstance(table, dict):
            table["alpaca"] = impl
            added = True

    vendor_list = getattr(interface, "VENDOR_LIST", None)
    if isinstance(vendor_list, list) and "alpaca" not in vendor_list:
        vendor_list.append("alpaca")

    return added
