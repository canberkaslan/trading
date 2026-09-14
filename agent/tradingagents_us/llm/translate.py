"""Translate the finished report, instead of making every agent write twice.

Setting `output_language` to two languages was the obvious move and the wrong
one. It would have doubled the output of all eighteen council calls — and
output is 53% of the bill, on models priced for reasoning rather than for
translation. Measured, that is about $0.61 per ticker, roughly $141 a month.

The reasoning only has to happen once. What the operator reads is the final
report, and rendering that same text in Turkish is a translation job: one call,
on the cheap tier, over ~2,000 tokens. About $0.012 per ticker — fifty times
less for the same thing on screen.

It also protects the analysis. The source material is English — SEC filings,
Benzinga headlines, analyst notes — and an agent asked to reason and translate
in one pass does both slightly worse. Reasoning in the language of the evidence
and translating afterwards keeps the two jobs separate.

A failed translation returns None rather than a partial or an apology. The
English text is always there; a missing Turkish one costs a convenience, while
a half-translated report that silently drops a caveat costs something else
entirely.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"

# Long enough for a full report and its formatting; the longest measured node
# output is ~3,800 tokens, and a translation is about the same size.
_MAX_TOKENS = 8000

_SYSTEM = (
    "You translate financial analysis from English into Turkish for the "
    "operator of a personal trading system.\n"
    "\n"
    "Rules:\n"
    "- Translate the meaning, not the words. Turkish that reads naturally.\n"
    "- Keep technical terms and tickers in English: RSI, MACD, SMA, EMA, "
    "Bollinger, P/E, PEG, ROE, FCF, YoY, TTM, Hold, Buy, Sell, Overweight, "
    "Underweight, stop, bracket, NVDA, MSFT.\n"
    "- Keep every number, ticker, date, percentage and currency figure "
    "EXACTLY as written. Do not convert currencies or reformat decimals.\n"
    "- Keep the markdown structure: headings, bold, lists, order of sections.\n"
    "- Translate every section. Never summarise, never omit, never add "
    "commentary of your own.\n"
    "- Any disclaimer or compliance sentence must be translated in full, not "
    "shortened — it is the part that must survive.\n"
    "\n"
    "Return only the translation."
)


def _model() -> str:
    return os.environ.get("TRANSLATION_MODEL", _DEFAULT_MODEL)


def is_enabled() -> bool:
    """Off unless asked for, so a deploy does not start spending on its own."""
    return (os.environ.get("TRANSLATE_REPORTS") or "").strip() in ("1", "true", "yes")


def translate_to_turkish(text: str, callbacks: list[Any] | None = None) -> str | None:
    """Turkish rendering of `text`, or None if it could not be produced.

    `callbacks` carries the same UsageCollector the council uses, so the
    translation appears in the cost record rather than being spent invisibly —
    the exact mistake the routed Haiku client made before it was fixed.
    """
    if not text or not text.strip():
        return None

    try:
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            model=_model(),
            temperature=0,  # a translation should not be creative
            max_tokens=_MAX_TOKENS,
            **({"callbacks": callbacks} if callbacks else {}),
        )
        result = llm.invoke([("system", _SYSTEM), ("human", text)])
    except Exception:  # noqa: BLE001 — a missing translation must not fail a decision
        log.warning("translation failed; the report stays English-only", exc_info=True)
        return None

    out = getattr(result, "content", None)
    if isinstance(out, list):
        # Some models return typed blocks; join the text ones.
        out = "".join(b.get("text", "") for b in out if isinstance(b, dict))
    if not isinstance(out, str) or not out.strip():
        return None
    return out.strip()
