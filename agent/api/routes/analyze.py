"""/v1/analyze — on-demand single-ticker analysis (WarrenAI-style).

The 7-agent pipeline takes minutes and costs ~$1, so analysis runs as a
background job: POST returns a job id immediately, the mobile app polls
GET until the decision is ready.

Analysis-only by design — this path NEVER submits an order. It reuses the
same pipeline the autonomous daily run uses, so what you see on demand is
exactly what the agent would decide.
"""

from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from tradingagents_us.graph.pipeline import propagate
from tradingagents_us.schemas import AgentDecision

from ..deps import require_token

log = logging.getLogger(__name__)
router = APIRouter()

JobStatus = Literal["queued", "running", "done", "error"]

# One worker so a burst of taps can't fan out into N concurrent ~$1 runs.
# This is a personal single-user backend; a process-local store is fine.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analyze")
_jobs: dict[str, "AnalyzeJob"] = {}
_lock = Lock()
# Keep the registry from growing unbounded over a long-lived process.
_MAX_JOBS = 100


@dataclass
class AnalyzeJob:
    job_id: str
    ticker: str
    status: JobStatus = "queued"
    decision: AgentDecision | None = None
    error: str | None = None
    created_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_utc: datetime | None = None


class AnalyzeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=6)


class AnalyzeJobView(BaseModel):
    job_id: str
    ticker: str
    status: JobStatus
    decision: AgentDecision | None = None
    error: str | None = None
    created_utc: datetime
    finished_utc: datetime | None = None


def _view(job: AnalyzeJob) -> AnalyzeJobView:
    return AnalyzeJobView(
        job_id=job.job_id,
        ticker=job.ticker,
        status=job.status,
        decision=job.decision,
        error=job.error,
        created_utc=job.created_utc,
        finished_utc=job.finished_utc,
    )


def _evict_old() -> None:
    """Drop the oldest finished jobs once we exceed the cap (caller holds lock)."""
    if len(_jobs) <= _MAX_JOBS:
        return
    finished = sorted(
        (j for j in _jobs.values() if j.status in ("done", "error")),
        key=lambda j: j.finished_utc or j.created_utc,
    )
    for j in finished[: len(_jobs) - _MAX_JOBS]:
        _jobs.pop(j.job_id, None)


def _run(job_id: str, ticker: str, trade_date: str) -> None:
    with _lock:
        job = _jobs[job_id]
        job.status = "running"
    try:
        decision = propagate(ticker, trade_date)
        # Persist so the on-demand decision also shows in /v1/agents history.
        try:
            from ..deps import get_repo

            get_repo().save_decision(decision)
        except Exception:  # persistence is best-effort, never fail the job
            log.exception("analyze: failed to persist decision for %s", ticker)
        with _lock:
            job.decision = decision
            job.status = "done"
            job.finished_utc = datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001 — surface any pipeline failure to the client
        log.exception("analyze pipeline failed for %s", ticker)
        with _lock:
            job.error = str(exc)
            job.status = "error"
            job.finished_utc = datetime.now(timezone.utc)


@router.post("", response_model=AnalyzeJobView, status_code=202)
async def start_analysis(
    req: AnalyzeRequest,
    user: str = Depends(require_token),
) -> AnalyzeJobView:
    ticker = req.ticker.strip().upper()
    if not ticker.isalpha():
        raise HTTPException(422, f"invalid ticker: {req.ticker!r}")
    with _lock:
        # Reuse an in-flight job for the same ticker to avoid duplicate spend.
        for j in _jobs.values():
            if j.ticker == ticker and j.status in ("queued", "running"):
                return _view(j)
        job = AnalyzeJob(job_id=uuid.uuid4().hex[:12], ticker=ticker)
        _jobs[job.job_id] = job
        _evict_old()
    trade_date = datetime.now(timezone.utc).date().isoformat()
    _executor.submit(_run, job.job_id, ticker, trade_date)
    log.info("analyze job %s queued for %s", job.job_id, ticker)
    return _view(job)


@router.get("/{job_id}", response_model=AnalyzeJobView)
async def get_analysis(
    job_id: str,
    user: str = Depends(require_token),
) -> AnalyzeJobView:
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"job not found: {job_id}")
    return _view(job)
