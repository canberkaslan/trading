"""The list trims; the detail does not.

Reports used to be cut at 2,000 characters when WRITTEN — every one of one
night's eleven sentiment reports came back exactly 2,000 characters long,
which is how the cut was found. What the agents reasoned is the record, and a
cap at write time destroys it permanently. The cap moved to read time, where
it is reversible.
"""

from __future__ import annotations

from api.routes.agents import _PREVIEW_CHARS, _preview
from tradingagents_us.schemas import AgentDecision, AgentReasoning


def _block(n: int) -> AgentReasoning:
    return AgentReasoning(
        agent="market_analyst", model="m", summary="x" * n,
        tokens_in=0, tokens_out=0, latency_ms=0,
    )


def _decision(*blocks: AgentReasoning) -> AgentDecision:
    from datetime import UTC, datetime

    return AgentDecision(
        decision_id="d1",
        ticker="AAPL",
        market="US",
        quote_currency="USD",
        rating="Hold",
        reasoning=list(blocks),
        timestamp_utc=datetime.now(UTC),
    )


def test_a_long_report_is_trimmed() -> None:
    out = _preview(_decision(_block(9000)))
    assert len(out.reasoning[0].summary) < 9000


def test_it_says_that_it_trimmed() -> None:
    # A report that simply stops mid-sentence reads as a model that stopped
    # mid-sentence, which is a different fault.
    out = _preview(_decision(_block(9000)))
    assert "tam metin" in out.reasoning[0].summary


def test_a_short_report_is_untouched() -> None:
    out = _preview(_decision(_block(500)))
    assert out.reasoning[0].summary == "x" * 500
    assert "tam metin" not in out.reasoning[0].summary


def test_exactly_at_the_limit_is_not_marked() -> None:
    # An off-by-one here would stamp the marker on a report that fits.
    out = _preview(_decision(_block(_PREVIEW_CHARS)))
    assert "tam metin" not in out.reasoning[0].summary


def test_the_original_is_not_mutated() -> None:
    # The caller holds rows from the repository; trimming them in place would
    # corrupt whatever else reads them.
    original = _decision(_block(9000))
    _preview(original)
    assert len(original.reasoning[0].summary) == 9000


def test_a_decision_with_no_reasoning_passes_through() -> None:
    assert _preview(_decision()) is not None
