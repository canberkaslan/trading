"""FLATTEN_ALL execution — shared by the API kill-switch handler (immediate)
and scripts/kill_check.py (daily-run backstop).

Alpaca's DELETE /v2/positions returns HTTP 207 multi-status: a per-position
array where EACH item carries its own status code. Individual closes can
fail (halted/LULD symbol, per-symbol reject) while others succeed — and
cancel_orders=true has already cancelled that position's protective stop
leg. Treating len(results) as success would report a flat book while an
unprotected position stays open, so every item is inspected and any
failure makes the whole flatten a failure.

Wording is honest about market hours: close orders SUBMIT immediately but
only FILL while the market is open — outside hours they queue for the next
open. We report orders submitted, never "positions closed".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..dataflows.alpaca_broker import AlpacaClient


@dataclass
class FlattenResult:
    ok: bool
    summary: str
    submitted: list[str] = field(default_factory=list)   # tickers with accepted close orders
    failed: list[str] = field(default_factory=list)      # "TICKER: <status/detail>"
    noop: bool = False                                   # book was already flat


def flatten_all(client: AlpacaClient | None = None) -> FlattenResult:
    """Cancel all open orders and submit market close orders for every
    position. Raises only on transport-level errors (caller alerts);
    per-position failures are reported in the result with ok=False."""
    own = client is None
    cli = client or AlpacaClient()
    try:
        positions = cli.list_positions()
        if not positions:
            cli.cancel_all_orders()
            return FlattenResult(
                ok=True, noop=True,
                summary="book already flat; open orders cancelled",
            )

        results = cli.close_all_positions(cancel_orders=True)

        submitted: list[str] = []
        failed: list[str] = []
        seen: set[str] = set()
        for item in results if isinstance(results, list) else []:
            sym = str(item.get("symbol") or "?")
            seen.add(sym)
            status = item.get("status")
            if isinstance(status, int) and 200 <= status < 300:
                submitted.append(sym)
            else:
                failed.append(f"{sym}: status={status!r}")
        # Positions the 207 body never mentioned got NO close order.
        for p in positions:
            if p.symbol not in seen:
                failed.append(f"{p.symbol}: missing from broker response")

        ok = not failed
        if ok:
            summary = (
                f"close orders submitted for {len(submitted)}/{len(positions)} "
                f"position(s) [{', '.join(submitted)}] — fills at next market open if closed"
            )
        else:
            summary = (
                f"PARTIAL flatten: {len(submitted)}/{len(positions)} submitted, "
                f"FAILED: {'; '.join(failed)} — failed positions may be UNPROTECTED "
                f"(stop legs were cancelled)"
            )
        return FlattenResult(ok=ok, summary=summary, submitted=submitted, failed=failed)
    finally:
        if own:
            cli.close()
