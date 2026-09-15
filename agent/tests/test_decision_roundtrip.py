"""Every field that is written must come back.

final_decision_text_tr was added to the schema, the column and the write path
— but not to the row→schema mapping. So translations were stored for every
decision and served as null, and the app showed English while the Turkish sat
in the column beside it. A write without its matching read is invisible until
someone checks the two against each other.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine

from tradingagents_us.schemas import AgentDecision, AgentReasoning
from tradingagents_us.storage.repository import TradeLogRepository


@pytest.fixture()
def repo(tmp_path) -> TradeLogRepository:
    return TradeLogRepository(
        engine=create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True)
    )


def _decision(**over) -> AgentDecision:
    base = dict(
        decision_id="d-roundtrip",
        ticker="AAPL",
        market="US",
        quote_currency="USD",
        rating="Hold",
        reasoning=[
            AgentReasoning(
                agent="market_analyst", model="m", summary="report body",
                tokens_in=1, tokens_out=2, latency_ms=3,
            )
        ],
        final_decision_text="Hold AAPL.",
        final_decision_text_tr="AAPL'ı tut.",
        timestamp_utc=datetime.now(UTC),
    )
    base.update(over)
    return AgentDecision(**base)


def test_the_translation_survives_a_round_trip(repo: TradeLogRepository) -> None:
    repo.save_decision(_decision())
    back = repo.list_recent_decisions(limit=1)[0]
    assert back.final_decision_text_tr == "AAPL\'ı tut."


def test_an_untranslated_decision_reads_back_as_none(repo: TradeLogRepository) -> None:
    # None must mean "not translated", never an empty string that the app would
    # render as a blank report.
    repo.save_decision(_decision(final_decision_text_tr=None))
    assert repo.list_recent_decisions(limit=1)[0].final_decision_text_tr is None


def test_every_schema_field_with_a_column_reads_back(repo: TradeLogRepository) -> None:
    """The general form of the bug, so the next added field cannot repeat it.

    final_decision_text_tr was written and never read, and nothing noticed
    because each field was wired by hand. This compares what went in against
    what came out for every field that HAS a column, rather than trusting a
    hand-written mapping to have stayed complete.

    list_recent_decisions returns rows, so the comparison is against column
    names — which is also what made the original omission possible.
    """
    from tradingagents_us.storage.models import AgentDecisionRow

    original = _decision(price_target=250.5, time_horizon="3-6 months")
    repo.save_decision(original)
    back = repo.list_recent_decisions(limit=1)[0]

    columns = {c.name for c in AgentDecisionRow.__table__.columns}

    def same(name: str) -> bool:
        a, b = getattr(original, name), getattr(back, name)
        if isinstance(a, datetime) and isinstance(b, datetime):
            # SQLite drops the tzinfo label but keeps the instant. Comparing
            # naively would report a loss that did not happen — and skipping
            # the field entirely would hide a real one.
            return a.replace(tzinfo=None) == b.replace(tzinfo=None)
        return a == b

    missing = [
        name
        for name in AgentDecision.model_fields
        if name in columns and getattr(original, name) is not None and not same(name)
    ]
    assert missing == [], f"written but not read back: {missing}"
    assert [r["summary"] for r in back.reasoning_json] == [
        r.summary for r in original.reasoning
    ]
