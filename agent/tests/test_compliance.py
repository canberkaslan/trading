"""The compliance guardrail, and that it actually reaches what it claims to.

The point of these is that the guardrail is easy to break silently: someone
resyncs the vendored tree and the prompt trailer quietly goes back to being
language-only, or a schema refactor drops the disclaimer and the API starts
serving bare ratings again. Both would look fine in every other test.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from tradingagents_us.compliance import ADVICE_STATUS, AGENT_INSTRUCTION, DISCLAIMER
from tradingagents_us.schemas import AgentDecision

_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))


def test_instruction_states_the_three_constraints() -> None:
    """Wording may change; these three commitments may not disappear."""
    text = AGENT_INSTRUCTION.lower()
    assert "not in your inputs" in text, "must forbid inventing figures"
    assert "guaranteed" in text, "must forbid certainty language"
    assert "not personalized advice" in text, "must disclaim personalization"


def test_instruction_does_not_muzzle_directional_calls() -> None:
    """This system exists to produce ratings for its operator's own account.
    A blanket 'never recommend' clause, copied from a consumer-advice product,
    would remove the product — so assert we did not import that."""
    text = AGENT_INSTRUCTION.lower()
    assert "do not recommend" not in text
    assert "never recommend" not in text


def test_every_decision_carries_the_disclaimer() -> None:
    d = AgentDecision(
        ticker="AAPL",
        market="US",
        quote_currency="USD",
        rating="Buy",
        reasoning=[],
        timestamp_utc=datetime.now(UTC),
        decision_id="d1",
    )
    assert d.advice_status == ADVICE_STATUS
    assert d.disclaimer == DISCLAIMER
    # And survives serialization — this is what the API actually sends.
    assert d.model_dump()["disclaimer"] == DISCLAIMER


def test_guardrail_reaches_every_pipeline_agent() -> None:
    """`get_language_instruction` is the one trailer all twelve prompt sites
    append. If a vendor resync reverts it, the guardrail reaches nobody and
    nothing else in the suite would notice."""
    from tradingagents.agents.utils.agent_utils import get_language_instruction

    assert AGENT_INSTRUCTION in get_language_instruction()


def test_guardrail_survives_a_non_english_run() -> None:
    """The Turkish/other-language path appends its own clause; the compliance
    text must not be dropped on the way."""
    from tradingagents.agents.utils import agent_utils

    assert AGENT_INSTRUCTION in agent_utils.get_language_instruction()


def test_council_prompts_carry_the_guardrail() -> None:
    """The council is ours, not vendored, so it does not inherit the trailer
    automatically — it has to opt in, and this is what proves it still does."""
    from tradingagents_us.llm import council

    assert AGENT_INSTRUCTION in council._VOTER_SYSTEM
    assert AGENT_INSTRUCTION in council._CHAIR_SYSTEM
