"""GET /v1/portfolio/* — live portfolio state from Alpaca."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from scripts.eval_report import _equity_series
from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.dataflows.sector_map import sector_for
from tradingagents_us.risk.concentration import (
    PositionWeight,
    compute_concentration,
    parse_trend,
)
from tradingagents_us.schemas import PortfolioSnapshot, Position

from ..deps import get_alpaca, require_token

router = APIRouter()


class EquityPointOut(BaseModel):
    date: str          # ISO date (UTC) of the daily equity bar
    equity: float
    return_pct: float  # cumulative return vs the first bar
    drawdown_pct: float  # vs running peak, <= 0


class EquityHistoryOut(BaseModel):
    period: str
    days: int
    start_equity: float
    end_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    points: list[EquityPointOut]


class TrendPointOut(BaseModel):
    ts: str
    n_positions: int
    top_weight_pct: float
    equity: float


class ConcentrationOut(BaseModel):
    n_positions: int
    gross_exposure_pct: float
    cash_pct: float
    top_weight_pct: float
    top3_weight_pct: float
    hhi: float
    effective_n: float
    flags: list[str]
    trend: list[TrendPointOut]


def _load_snapshot_records() -> list[dict]:
    """Read the snapshot JSONL (written by scripts/snapshot.py). Empty if absent."""
    path = Path(os.environ.get("EVAL_SNAPSHOT_FILE", "./data/eval_snapshots.jsonl"))
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


@router.get("/snapshot", response_model=PortfolioSnapshot)
async def get_snapshot(
    user: str = Depends(require_token),
    alpaca: AlpacaClient = Depends(get_alpaca),
) -> PortfolioSnapshot:
    """Current equity + positions + daily P&L for the authenticated user."""
    try:
        acct = alpaca.account()
        positions_raw = alpaca.list_positions()
    except Exception as e:
        raise HTTPException(502, f"alpaca_error: {e}") from e
    finally:
        alpaca.close()

    positions = [
        Position(
            ticker=p.symbol,
            market="US",
            quantity=int(p.qty),
            avg_entry_price=p.avg_entry_price,
            current_price=p.market_value / p.qty if p.qty else 0.0,
            unrealized_pnl=p.unrealized_pl,
            unrealized_pnl_pct=p.unrealized_plpc,
            stop_loss=0.0,                # broker-side leg lives on order, not position
            sector=sector_for(p.symbol),  # static GICS map, display-only (off trading path)
            opened_at_utc=datetime.now(timezone.utc),  # Alpaca doesn't expose open ts on position
        )
        for p in positions_raw
    ]

    # Alpaca account.cash is settled cash; equity is portfolio_value
    daily_pnl = acct.portfolio_value - 100_000.0  # paper starts at $100k; close enough for dev
    daily_pnl_pct = daily_pnl / 100_000.0
    return PortfolioSnapshot(
        user_id=user,
        cash_usd=acct.cash,
        positions=positions,
        total_equity_usd=acct.portfolio_value,
        daily_pnl_usd=daily_pnl,
        daily_pnl_pct=daily_pnl_pct,
        max_drawdown_today=0.0,  # computed once we have intraday equity series in DB
        timestamp_utc=datetime.now(timezone.utc),
    )


@router.get("/history", response_model=EquityHistoryOut)
async def get_history(
    period: str = "1M",
    user: str = Depends(require_token),
    alpaca: AlpacaClient = Depends(get_alpaca),
) -> EquityHistoryOut:
    """Cleaned daily equity curve for charting. Reuses the same equity-series
    cleaning + EVAL_START_DATE cutoff as the eval scorecard so the chart and
    the GO/NO-GO numbers agree. Read-only, off the trading path.
    """
    try:
        history = alpaca.portfolio_history(period=period, timeframe="1D")
    except Exception as e:
        raise HTTPException(502, f"alpaca_error: {e}") from e
    finally:
        alpaca.close()

    s = _equity_series(history)
    start = os.environ.get("EVAL_START_DATE")
    if start:
        s = s[s.index >= pd.Timestamp(start, tz="UTC")]

    if s.empty:
        return EquityHistoryOut(
            period=period,
            days=0,
            start_equity=0.0,
            end_equity=0.0,
            total_return_pct=0.0,
            max_drawdown_pct=0.0,
            points=[],
        )

    base = float(s.iloc[0])
    peak = float("-inf")
    points: list[EquityPointOut] = []
    max_dd = 0.0
    for ts, val in s.items():
        eq = float(val)
        peak = max(peak, eq)
        dd = (eq / peak - 1.0) if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
        points.append(
            EquityPointOut(
                date=ts.date().isoformat(),
                equity=round(eq, 2),
                return_pct=round((eq / base - 1.0) * 100, 2) if base else 0.0,
                drawdown_pct=round(dd * 100, 2),
            )
        )

    return EquityHistoryOut(
        period=period,
        days=len(points),
        start_equity=round(base, 2),
        end_equity=round(float(s.iloc[-1]), 2),
        total_return_pct=round((float(s.iloc[-1]) / base - 1.0) * 100, 2) if base else 0.0,
        max_drawdown_pct=round(max_dd * 100, 2),
        points=points,
    )


@router.get("/concentration", response_model=ConcentrationOut)
async def get_concentration(
    user: str = Depends(require_token),
    alpaca: AlpacaClient = Depends(get_alpaca),
) -> ConcentrationOut:
    """Read-only diversification diagnostics for the current book plus the
    position-count / top-weight trend from the snapshot log. Off the trading
    path — purely for the human to eyeball concentration risk.
    """
    try:
        acct = alpaca.account()
        positions_raw = alpaca.list_positions()
    except Exception as e:
        raise HTTPException(502, f"alpaca_error: {e}") from e
    finally:
        alpaca.close()

    weights = [PositionWeight(ticker=p.symbol, market_value=p.market_value) for p in positions_raw]
    m = compute_concentration(weights, acct.portfolio_value)
    trend = parse_trend(_load_snapshot_records())
    return ConcentrationOut(
        n_positions=m.n_positions,
        gross_exposure_pct=m.gross_exposure_pct,
        cash_pct=m.cash_pct,
        top_weight_pct=m.top_weight_pct,
        top3_weight_pct=m.top3_weight_pct,
        hhi=m.hhi,
        effective_n=m.effective_n,
        flags=m.flags,
        trend=[
            TrendPointOut(
                ts=t.ts,
                n_positions=t.n_positions,
                top_weight_pct=t.top_weight_pct,
                equity=t.equity,
            )
            for t in trend
        ],
    )
