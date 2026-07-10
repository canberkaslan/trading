"""/v1/eval — live paper-trading scorecard for the mobile app.

Reuses the same scorecard logic as scripts/eval_report.py so the in-app
number and the weekly push always agree. Read-only; cached briefly since
the equity curve only moves once a day.
"""

from __future__ import annotations

import time
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from scripts.eval_report import (
    GATE_MAX_DD,
    GATE_SHARPE,
    MIN_TRADING_DAYS,
    build_gates,
    build_scorecard,
    _provisional_verdict,
    _verdict,
)

from ..deps import require_token

router = APIRouter()

_CACHE_TTL_S = 60.0
_cache: dict[str, tuple[float, "EvalResult"]] = {}
_lock = Lock()


class Gate(BaseModel):
    name: str
    passed: bool | None
    detail: str


class EvalResult(BaseModel):
    verdict: str
    provisional_verdict: str | None
    reasons: list[str]
    days: int
    days_required: int
    days_remaining: int
    eval_complete: bool
    total_return_pct: float
    sharpe: float
    sortino: float
    max_dd_pct: float
    calmar: float
    spy_return_pct: float | None
    risk_free_pct: float
    risk_free_source: str
    gate_sharpe: float
    gate_max_dd_pct: float
    gates: list[Gate]


def _build(period: str, benchmark: bool) -> EvalResult:
    sc = build_scorecard(period=period, benchmark=benchmark)
    verdict, reasons = _verdict(sc)
    return EvalResult(
        verdict=verdict,
        provisional_verdict=_provisional_verdict(sc),
        reasons=reasons,
        days=sc.days,
        days_required=MIN_TRADING_DAYS,
        days_remaining=max(0, MIN_TRADING_DAYS - sc.days),
        eval_complete=sc.days >= MIN_TRADING_DAYS,
        total_return_pct=round(sc.total_return * 100, 2),
        sharpe=round(sc.sharpe, 2),
        sortino=round(sc.sortino, 2),
        max_dd_pct=round(sc.max_dd * 100, 2),
        calmar=round(sc.calmar, 2),
        spy_return_pct=round(sc.spy_return * 100, 2) if sc.spy_return is not None else None,
        risk_free_pct=round(sc.rf_annual * 100, 2),
        risk_free_source=sc.rf_source,
        gate_sharpe=GATE_SHARPE,
        gate_max_dd_pct=GATE_MAX_DD * 100,
        gates=[Gate(**g) for g in build_gates(sc)],
    )


@router.get("", response_model=EvalResult)
async def get_eval(
    period: str = "1M",
    benchmark: bool = True,
    user: str = Depends(require_token),
) -> EvalResult:
    key = f"{period}:{benchmark}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < _CACHE_TTL_S:
            return hit[1]
    try:
        result = _build(period, benchmark)
    except SystemExit as exc:  # build_scorecard raises this when too little history
        raise HTTPException(409, str(exc)) from exc
    with _lock:
        _cache[key] = (now, result)
    return result
