"""Finnhub dataflow — analyst recommendations, earnings surprises, company news.

Finnhub's free tier (60 req/min) gives institutional-grade signals the other
sources don't: sell-side analyst recommendation trends and earnings beats/
misses. We fetch these and format a compact plaintext block for the LLM
analysts. Everything degrades gracefully — no key or an API error returns a
clear placeholder string instead of raising, so the pipeline never breaks.

Env: FINNHUB_API_KEY (free at https://finnhub.io). Absent -> placeholders.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

_BASE = "https://finnhub.io/api/v1"
_TIMEOUT = 10.0


def _key() -> str | None:
    return os.environ.get("FINNHUB_API_KEY") or None


@dataclass(frozen=True)
class Recommendation:
    period: str
    strong_buy: int
    buy: int
    hold: int
    sell: int
    strong_sell: int

    @property
    def total(self) -> int:
        return self.strong_buy + self.buy + self.hold + self.sell + self.strong_sell

    def tilt(self) -> str:
        bullish = self.strong_buy + self.buy
        bearish = self.sell + self.strong_sell
        if bullish > bearish * 1.5:
            return "bullish"
        if bearish > bullish * 1.5:
            return "bearish"
        return "mixed"


class FinnhubClient:
    """Thin Finnhub REST client. Methods raise on transport errors; the
    module-level `finnhub_block` wraps them for graceful degradation."""

    def __init__(self, api_key: str | None = None, timeout_s: float = _TIMEOUT) -> None:
        self.api_key = api_key or os.environ.get("FINNHUB_API_KEY", "")
        self._http = httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FinnhubClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, params: dict) -> object:
        params = {**params, "token": self.api_key}
        r = self._http.get(f"{_BASE}{path}", params=params)
        r.raise_for_status()
        return r.json()

    def recommendations(self, symbol: str) -> list[Recommendation]:
        rows = self._get("/stock/recommendation", {"symbol": symbol.upper()})
        out: list[Recommendation] = []
        if isinstance(rows, list):
            for d in rows:
                out.append(
                    Recommendation(
                        period=str(d.get("period", "")),
                        strong_buy=int(d.get("strongBuy", 0)),
                        buy=int(d.get("buy", 0)),
                        hold=int(d.get("hold", 0)),
                        sell=int(d.get("sell", 0)),
                        strong_sell=int(d.get("strongSell", 0)),
                    )
                )
        return out

    def earnings_surprises(self, symbol: str, limit: int = 4) -> list[dict]:
        rows = self._get("/stock/earnings", {"symbol": symbol.upper(), "limit": limit})
        return rows if isinstance(rows, list) else []


def _fmt_recommendation(recs: list[Recommendation]) -> str:
    if not recs:
        return "  (no analyst recommendations available)"
    latest = recs[0]  # Finnhub returns newest first
    return (
        f"  Latest ({latest.period}): {latest.tilt().upper()} — "
        f"StrongBuy {latest.strong_buy} · Buy {latest.buy} · Hold {latest.hold} · "
        f"Sell {latest.sell} · StrongSell {latest.strong_sell} (n={latest.total})"
    )


def _fmt_earnings(rows: list[dict]) -> str:
    if not rows:
        return "  (no earnings history available)"
    lines = []
    for d in rows[:4]:
        actual = d.get("actual")
        est = d.get("estimate")
        period = d.get("period", "?")
        if actual is None or est is None:
            continue
        beat = "BEAT" if actual >= est else "MISS"
        surprise = d.get("surprisePercent")
        sp = f" ({surprise:+.1f}%)" if isinstance(surprise, (int, float)) else ""
        lines.append(f"  {period}: actual {actual} vs est {est} — {beat}{sp}")
    return "\n".join(lines) if lines else "  (no comparable earnings data)"


def finnhub_block(symbol: str) -> str:
    """Compact analyst-recommendation + earnings block for the LLM. Never raises."""
    if not _key():
        return "[Finnhub] FINNHUB_API_KEY not set — analyst/earnings data unavailable."
    try:
        with FinnhubClient() as fc:
            recs = fc.recommendations(symbol)
            earns = fc.earnings_surprises(symbol)
        return (
            f"[Finnhub analyst + earnings — {symbol.upper()}]\n"
            f"Analyst recommendations:\n{_fmt_recommendation(recs)}\n"
            f"Recent earnings surprises:\n{_fmt_earnings(earns)}"
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never break the pipeline
        return f"[Finnhub] fetch failed for {symbol}: {exc}"
