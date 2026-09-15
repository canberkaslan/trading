"""/v1/agents — agent decision history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from tradingagents_us.schemas import AgentDecision
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.models import AgentDecisionRow
from tradingagents_us.storage.repository import row_to_decision

from ..deps import get_repo, require_token

router = APIRouter()


# Characters of each agent report the LIST carries. Enough to read what the
# agent concluded, short enough that 25 of them stay a list rather than a
# download.
_PREVIEW_CHARS = 2000


def _preview(row: AgentDecisionRow) -> AgentDecisionRow:
    """Trim each agent report on a decision ROW for the list response.

    It operates on `reasoning_json` — a list of plain dicts — because
    `list_recent_decisions` returns SQLAlchemy rows, not schemas. The first
    version reached for `.reasoning` and `.model_copy()`, which a row has
    neither of, so `getattr(..., None)` found nothing and it returned the row
    untouched. It was a silent no-op: no error, no trimming, and a response
    that looked exactly like the one it was supposed to shrink.

    The row is mutated rather than copied. These are detached objects the
    session has already released, and they exist only to be serialised into
    this response; copying a SQLAlchemy instance to avoid touching something
    nobody else holds would be ceremony.
    """
    blocks = row.reasoning_json or []
    if not isinstance(blocks, list):
        return row

    trimmed = []
    for b in blocks:
        if not isinstance(b, dict):
            trimmed.append(b)
            continue
        summary = b.get("summary") or ""
        if len(summary) <= _PREVIEW_CHARS:
            trimmed.append(b)
            continue
        # Say it was trimmed. A report that simply stops mid-sentence reads as
        # a model that stopped mid-sentence, which is a different fault.
        trimmed.append(
            {**b, "summary": summary[:_PREVIEW_CHARS] + "\n\n[…tam metin için karara dokun]"}
        )
    row.reasoning_json = trimmed
    return row


@router.get("/decisions", response_model=list[AgentDecision])
async def list_decisions(
    ticker: str | None = None,
    limit: int = 50,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> list[AgentDecision]:
    rows = repo.list_recent_decisions(limit=limit, ticker=ticker)
    # Trimmed HERE rather than at write time. A page of 25 full decisions is
    # about a megabyte — real weight on a phone — but cutting at write time
    # destroyed the analysis permanently to save it. The list shows previews;
    # /decisions/{id} returns the whole thing.
    rows = [_preview(r) for r in rows]
    return [row_to_decision(r) for r in rows]


@router.get("/decisions/{decision_id}", response_model=AgentDecision)
async def get_decision(
    decision_id: str,
    user: str = Depends(require_token),
    repo: TradeLogRepository = Depends(get_repo),
) -> AgentDecision:
    row = repo.get_decision(decision_id)
    if row is None:
        raise HTTPException(404, f"decision not found: {decision_id}")
    return row_to_decision(row)
