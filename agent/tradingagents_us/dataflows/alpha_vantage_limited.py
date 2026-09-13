"""Make the Alpha Vantage news path obey the article limit the config declares.

`news_article_limit: 20` and `global_news_article_limit: 10` have been in
`default_config.py` since before this, and only `yfinance_news.py` has ever
read them. The Alpha Vantage functions send no limit at all, so a live call for
NVDA returns 50 articles — 137 KB, roughly 34k tokens — and `get_news` is
called by BOTH the news analyst and the sentiment analyst. Switching the vendor
without this would have raised input tokens by about 39% on a run whose input
is already 176k, and it would have done so while a config line sat there
claiming the number was 20.

Alpha Vantage earns its place: it carries a per-article `ticker_sentiment`
score and a topic taxonomy (fiscal policy, M&A, IPO) that yfinance does not,
which is the reason to want it. Paying for fifty articles to read twenty is
not part of that reason.

So this trims the payload to the configured count, keeping the highest-
relevance articles — Alpha Vantage already ranks them by `relevance_score` for
the requested ticker, so "the first N" is its own judgement of what matters,
not ours.

Registered through `VENDOR_METHODS` like the Alpaca adapter, and it does NOT
change the default vendor: `data_vendors.news_data` still decides.
"""

from __future__ import annotations

import json
from typing import Any

# Fallbacks matching default_config.py, used only if the config is unreadable.
_DEFAULT_TICKER_LIMIT = 20
_DEFAULT_GLOBAL_LIMIT = 10


def _limit_from_config(key: str, fallback: int) -> int:
    try:
        from tradingagents.dataflows.config import get_config

        value = int(get_config().get(key, fallback))
        return value if value > 0 else fallback
    except Exception:  # noqa: BLE001 — a missing config must not mean "no limit"
        return fallback


def trim_feed(payload: Any, limit: int) -> Any:
    """Keep the first `limit` articles of an Alpha Vantage NEWS_SENTIMENT body.

    Accepts the dict or the JSON string the upstream functions may return, and
    gives back the same shape it was handed — a caller that expected a string
    must not suddenly receive a dict.
    """
    was_str = isinstance(payload, str)
    try:
        data = json.loads(payload) if was_str else payload
    except (TypeError, ValueError):
        # Not JSON: an error string from the vendor. Pass it through untouched
        # rather than swallowing the message that explains the failure.
        return payload

    if not isinstance(data, dict):
        return payload

    feed = data.get("feed")
    if not isinstance(feed, list) or len(feed) <= limit:
        return payload

    trimmed = dict(data)
    trimmed["feed"] = feed[:limit]
    # Say what happened. A silently shortened feed reads as "that is all the
    # news there was", which is a different claim from "we asked for twenty".
    trimmed["items"] = str(limit)
    trimmed["_trimmed_from"] = len(feed)
    return json.dumps(trimmed, indent=4) if was_str else trimmed


def get_news_limited(ticker: str, start_date: str, end_date: str) -> Any:
    from tradingagents.dataflows.alpha_vantage_news import get_news

    return trim_feed(
        get_news(ticker, start_date, end_date),
        _limit_from_config("news_article_limit", _DEFAULT_TICKER_LIMIT),
    )


def get_global_news_limited(
    curr_date: str,
    look_back_days: int | None = None,
    limit: int | None = None,
) -> Any:
    from tradingagents.dataflows.alpha_vantage_news import get_global_news

    configured = _limit_from_config("global_news_article_limit", _DEFAULT_GLOBAL_LIMIT)
    effective = limit if limit is not None else configured
    return trim_feed(
        get_global_news(curr_date, look_back_days or 7, effective),
        effective,
    )


def register() -> bool:
    """Add "alpha_vantage_limited" as a news_data vendor. Idempotent."""
    try:
        from tradingagents.dataflows import interface
    except ImportError:
        return False

    methods = getattr(interface, "VENDOR_METHODS", None)
    if not isinstance(methods, dict):
        return False

    added = False
    for name, impl in (
        ("get_news", get_news_limited),
        ("get_global_news", get_global_news_limited),
    ):
        table = methods.get(name)
        if isinstance(table, dict):
            table["alpha_vantage_limited"] = impl
            added = True

    vendor_list = getattr(interface, "VENDOR_LIST", None)
    if isinstance(vendor_list, list) and "alpha_vantage_limited" not in vendor_list:
        vendor_list.append("alpha_vantage_limited")
    return added
