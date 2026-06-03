"""GET /v1/portfolio/* — live portfolio state from Alpaca."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.schemas import PortfolioSnapshot, Position

from ..deps import get_alpaca, require_token

router = APIRouter()


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
            sector=None,
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
