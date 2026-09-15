"""/v1/agents — agent decision history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from tradingagents_us.schemas import AgentDecision
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.repository import row_to_decision

from ..deps import get_repo, require_token

router = APIRouter()


# Characters of each agent report the LIST carries. Enough to read what the
# agent concluded, short enough that 25 of them stay a list rather than a
# download.
_PREVIEW_CHARS = 2000


def _preview(decision):
    """A copy whose reasoning summaries are trimmed for the list response."""
    blocks = getattr(decision, "reasoning", None)
    if not blocks:
        return decision
    trimmed = []
    for b in blocks:
        summary = getattr(b, "summary", "") or ""
        if len(summary) <= _PREVIEW_CHARS:
            trimmed.append(b)
            continue
        # Say it was trimmed. A report that simply stops mid-sentence reads as
        # a model that stopped mid-sentence, which is a different fault.
        trimmed.append(
            b.model_copy(
                update={
                    "summary": summary[:_PREVIEW_CHARS]
                    + "\n\n[…tam metin için karara dokun]"
                }
            )
        )
    return decision.model_copy(update={"reasoning": trimmed})


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
