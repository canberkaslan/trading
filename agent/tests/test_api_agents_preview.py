"""The list trims; the detail does not.

Reports used to be cut at 2,000 characters when WRITTEN — every one of one
night's eleven sentiment reports came back exactly 2,000 characters long,
which is how the cut was found. What the agents reasoned is the record, and a
cap at write time destroys it permanently. The cap moved to read time, where
it is reversible.
"""

from __future__ import annotations

from api.routes.agents import _PREVIEW_CHARS, _preview
from tradingagents_us.storage.models import AgentDecisionRow


def _block(n: int) -> dict:
    return {
        "agent": "market_analyst", "model": "m", "summary": "x" * n,
        "tokens_in": 0, "tokens_out": 0, "latency_ms": 0,
    }


def _decision(*blocks: dict) -> AgentDecisionRow:
    """A ROW, because that is what list_recent_decisions returns.

    The first version of these tests built schemas, so they passed against a
    _preview that could never run on the real objects.
    """
    row = AgentDecisionRow()
    row.decision_id = "d1"
    row.ticker = "AAPL"
    row.rating = "Hold"
    row.reasoning_json = list(blocks)
    return row


def test_a_long_report_is_trimmed() -> None:
    out = _preview(_decision(_block(9000)))
    assert len(out.reasoning_json[0]["summary"]) < 9000


def test_it_says_that_it_trimmed() -> None:
    # A report that simply stops mid-sentence reads as a model that stopped
    # mid-sentence, which is a different fault.
    out = _preview(_decision(_block(9000)))
    assert "tam metin" in out.reasoning_json[0]["summary"]


def test_a_short_report_is_untouched() -> None:
    out = _preview(_decision(_block(500)))
    assert out.reasoning_json[0]["summary"] == "x" * 500
    assert "tam metin" not in out.reasoning_json[0]["summary"]


def test_exactly_at_the_limit_is_not_marked() -> None:
    # An off-by-one here would stamp the marker on a report that fits.
    out = _preview(_decision(_block(_PREVIEW_CHARS)))
    assert "tam metin" not in out.reasoning_json[0]["summary"]


def test_it_trims_in_place_on_a_detached_row() -> None:
    # These rows are detached objects the session has already released, held
    # only to be serialised into this response — so trimming in place is the
    # honest thing rather than copying to protect a holder that does not exist.
    row = _decision(_block(9000))
    out = _preview(row)
    assert out is row
    assert len(row.reasoning_json[0]["summary"]) < 9000


def test_a_decision_with_no_reasoning_passes_through() -> None:
    assert _preview(_decision()) is not None
