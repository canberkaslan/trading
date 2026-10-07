"""Polymarket prediction-market vendor.

Surfaces live, market-implied probabilities for forward-looking events (Fed
decisions, recession, elections, geopolitics, crypto) to the news analyst, as a
complement to news (what happened) and FRED macro data (where things stand):
what the crowd actually prices to happen next.

Uses Polymarket's public Gamma API (https://gamma-api.polymarket.com) — no key,
no auth. Each market's ``outcomePrices`` are the implied probabilities of its
outcomes (a "Yes" at 0.76 means the market prices a 76% chance).
"""
import json
import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"

# --- fork patch (trading repo): fail fast on an optional signal ---
# Upstream waited 30 s per call, and the news analyst may call this several
# times per ticker; on 2026-10-05 a slow Gamma held councils on that timeout.
# Prediction markets are optional enrichment, so give up quickly instead:
# (connect, read) seconds, and one retry for a failure that is likely to clear
# (connection refused/reset, connect timeout, 429/5xx). A read timeout is not
# retried: the server is up but slow, and waiting again only doubles the stall.
REQUEST_TIMEOUT = (3, 5)
RETRY_PAUSE_S = 1.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# Default number of markets to return, ranked by traded volume.
DEFAULT_LIMIT = 6


def _get(path: str, params: dict) -> requests.Response:
    return requests.get(f"{GAMMA_BASE}/{path}", params=params, timeout=REQUEST_TIMEOUT)


def _request(path: str, params: dict) -> dict:
    try:
        response = _get(path, params)
        retry = response.status_code in _RETRY_STATUS
        reason = f"HTTP {response.status_code}"
    except requests.ConnectionError as e:  # includes ConnectTimeout, not ReadTimeout
        retry, reason = True, e
    if retry:
        logger.info("Polymarket %s failed (%s); retrying once", path, reason)
        time.sleep(RETRY_PAUSE_S)
        response = _get(path, params)
    response.raise_for_status()
    return response.json()
# --- end fork patch ---


def _parse_json_list(value) -> list:
    """Gamma encodes ``outcomes``/``outcomePrices`` as JSON-string arrays."""
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return []


def _is_forward_looking(market: dict, now: datetime) -> bool:
    """Keep only open markets that resolve in the future.

    ``closed`` is the reliable resolved flag (``active`` stays True even for
    settled markets), and a past ``endDate`` means the event already resolved —
    either way it is not a forward-looking signal.
    """
    if market.get("closed"):
        return False
    end_date = market.get("endDate")
    if end_date:
        try:
            if datetime.fromisoformat(end_date.replace("Z", "+00:00")) < now:
                return False
        except ValueError:
            pass
    return bool(_parse_json_list(market.get("outcomePrices"))) and bool(
        _parse_json_list(market.get("outcomes"))
    )


def get_prediction_markets(topic: str, limit: int | None = None) -> str:
    """Return live prediction-market probabilities for an event topic.

    Args:
        topic: Event keyword(s), e.g. "Fed rate cut", "recession 2026",
            "US election", or a sector/company event.
        limit: Max markets to return (ranked by traded volume); ``None`` uses
            DEFAULT_LIMIT.

    Returns:
        A markdown report of the most-traded open markets matching the topic,
        each with its implied probability, traded volume, resolution date, and
        recent (1-week) move.
    """
    if limit is None:
        limit = DEFAULT_LIMIT

    try:
        data = _request("public-search", {"q": topic, "limit_per_type": 20})
    except requests.RequestException as e:
        logger.warning("Polymarket search failed for %r: %s", topic, e)
        # fork patch (trading repo): say outright that this is a failed lookup,
        # so the analyst cannot read it as "no markets" or as a neutral signal.
        return (
            f"Polymarket data is currently unavailable ({e}). This is a failed "
            f"lookup, not an absence of markets: proceed without "
            f"prediction-market signal for '{topic}'."
        )

    now = datetime.now(timezone.utc)
    candidates = [
        m
        for event in data.get("events", [])
        for m in event.get("markets", [])
        if _is_forward_looking(m, now)
    ]
    candidates.sort(key=lambda m: m.get("volumeNum") or 0, reverse=True)

    header = (
        f'## Polymarket prediction markets: "{topic}"\n'
        f"Live, market-implied probabilities (higher traded volume = deeper, "
        f"more reliable). A probability is the crowd's priced odds of the event, "
        f"not a forecast you should take as certain.\n\n"
    )

    if not candidates:
        return header + (
            f"No open prediction markets matched '{topic}'. Polymarket coverage "
            f"is concentrated in macro, political, geopolitical, and crypto "
            f"events; a specific equity may have none."
        )

    lines = []
    for m in candidates[:limit]:
        prices = _parse_json_list(m.get("outcomePrices"))
        outcomes = _parse_json_list(m.get("outcomes"))
        try:
            prob = float(prices[0])
        except (ValueError, IndexError):
            continue
        label = outcomes[0] if outcomes else "Yes"
        volume = m.get("volumeNum") or 0
        end_date = (m.get("endDate") or "")[:10]
        wk = m.get("oneWeekPriceChange")
        wk_str = (
            f", 1-week {wk * 100:+.1f}pp"
            if isinstance(wk, (int, float)) and wk
            else ""
        )
        lines.append(
            f"- **{m.get('question')}** — {label} {prob:.0%} "
            f"(${volume:,.0f} volume, resolves {end_date}{wk_str})"
        )

    return header + "\n".join(lines) + "\n"
