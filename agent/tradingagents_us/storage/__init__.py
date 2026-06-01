"""Persistence layer — SQLAlchemy models + repositories for trade history.

Aurora Serverless v2 PostgreSQL in production (infra/modules/rds-aurora).
SQLite in dev (DATABASE_URL=sqlite:///./local.db) for offline development.

The single source of truth is `models.py` — Alembic migrations are
generated from these declarative tables.
"""

from .models import (
    AgentDecisionRow,
    Base,
    OrderUpdateRow,
    TradeOrderRow,
)
from .repository import TradeLogRepository

__all__ = [
    "AgentDecisionRow",
    "Base",
    "OrderUpdateRow",
    "TradeLogRepository",
    "TradeOrderRow",
]
