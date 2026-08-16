"""The eval window cutoff — one definition of "when the clean book started".

The paper-trading eval deliberately ignores everything before 2026-06-24: on
that day the over-buying bug (`fix(trade): load current Alpaca positions`) was
fixed, the stacked AAPL/MSFT lots it had accumulated were flattened, and the
universe went from 3 names to 11. `scripts/eval_report.build_scorecard` has
honored the cutoff since that day via `EVAL_START_DATE`, so the equity curve,
Sharpe and drawdown all measure the clean book.

The realized-P&L ledger did not, and the two therefore described different
periods: the 24 round trips the re-baseline flatten closed on 2026-06-24 are
bug-era positions the eval excludes by construction, yet they dominated the
ledger's win rate, expectancy and profit factor. Same env var, same date, one
meaning — that is what this module exists to keep true.

A malformed value raises instead of falling back to "no cutoff": silently
reporting all-time numbers under an eval-window label is precisely the
self-flattery the ledger was built to prevent.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Mapping

ENV_VAR = "EVAL_START_DATE"


def parse_eval_start(raw: str | None) -> datetime | None:
    """`YYYY-MM-DD` (or a full ISO timestamp) -> tz-aware UTC midnight.

    None/empty means "no cutoff configured". Anything else that does not parse
    raises ValueError — an operator typo must surface, not silently widen the
    window.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError as exc:
            raise ValueError(
                f"{ENV_VAR}={raw!r} is not a date (expected YYYY-MM-DD)"
            ) from exc
    # A naive value is a calendar date the operator wrote in UTC terms; the
    # broker's fill timestamps are UTC, so anchoring anywhere else would shift
    # the boundary by hours and reclassify trades on the cutoff day itself.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def eval_start_utc(env: Mapping[str, str] | None = None) -> datetime | None:
    """The configured cutoff, or None when the deployment sets no window."""
    return parse_eval_start((env if env is not None else os.environ).get(ENV_VAR))
