"""Decide whether a ticker can possibly be acted on, BEFORE paying for a council.

The order of operations was backwards. `scripts/trade.py` runs the full LLM
council first (~5-10 minutes, roughly a dollar of model spend) and only then
fetches the account and computes spendable cash — so a name the cash budget was
always going to refuse still pays the full fee to produce a decision nobody can
execute. At eleven tickers that is a rounding error. At a hundred it is about
$68 a day spent computing predetermined rejections.

This module answers the cheap question first: is there any arithmetic under
which an order for this name could be placed today? If provably not, the
council is skipped and the money is not spent.

The discipline that makes this safe to trust:

**Skip only the impossible, never the unlikely.** A gate that also skipped
names it judged unpromising would be a strategy change wearing a cost
optimisation's clothes — quietly narrowing what the agents are allowed to
consider, with no record that it happened. Every rule here rejects on
arithmetic that cannot come out differently after five minutes of reasoning.
"Probably too small to bother with" is the sizer's call, made with the council's
output in hand, and it stays there.

**A held name is never skipped.** This is the hard invariant. The council is
the only discretionary exit this system has — the alternatives are a trailing
stop or a time exit — so a position dropped from evaluation can effectively
never be sold on judgement. Saving a dollar by not reconsidering a position is
the most expensive dollar in the system.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CouncilGate:
    """Whether to run the council, and the reason either way.

    The reason is not decoration: a skipped ticker produces no decision row, so
    the log line is the only trace that the name was considered at all. Without
    it, a gate bug is indistinguishable from a name that was never in the
    universe.
    """

    run: bool
    reason: str


def should_council(
    ticker: str,
    *,
    held_qty: float,
    spendable: float | None,
    price: float | None,
    max_cash_utilization: float = 1.0,
) -> CouncilGate:
    """Can an order for `ticker` possibly be placed today?

    `spendable` is `cash_budget.spendable_cash` — settled cash minus the claims
    of open BUYs, or None when one of those orders could not be priced.
    `price` is a last/previous close used only to ask whether a single share is
    affordable; it does not size anything.
    """
    # The invariant, checked first so no later rule can reach past it.
    if held_qty > 0:
        return CouncilGate(True, f"holding {held_qty:g} shares — exit path must stay open")

    # `None` means an open BUY could not be priced, so the true claim on cash is
    # unknown. trade.py already refuses new exposure in that state; running a
    # council to reach the same refusal is the pure-waste case.
    if spendable is None:
        return CouncilGate(False, "open BUYs unpriceable — new exposure already refused")

    budget = max(0.0, spendable) * max_cash_utilization

    # An unknown price cannot prove impossibility. Unknown is not zero, and a
    # gate that guessed here would skip names on a data gap — silently shrinking
    # the universe for a reason that has nothing to do with the book.
    if price is None or price <= 0:
        return CouncilGate(True, "price unavailable — cannot prove unaffordable, so evaluate")

    if budget < price:
        return CouncilGate(
            False,
            f"spendable ${budget:,.2f} < one share at ${price:,.2f} — no BUY is arithmetically possible",
        )

    return CouncilGate(True, f"spendable ${budget:,.2f} covers at least one share at ${price:,.2f}")
