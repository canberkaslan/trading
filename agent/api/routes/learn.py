"""/v1/learn — the bilingual lesson set behind the dashboard's Learn card.

Static content, served from a JSON file rather than the database: it changes
with a release, not with the book, and shipping it as data keeps one copy
authoritative for both the web dashboard and the mobile app.

Deliberately unauthenticated. It is general education about what Sharpe and
drawdown mean — it carries no position, no equity figure, and nothing about
this operator's account — so requiring the API token here would only mean the
dashboard has to be signed in to explain its own vocabulary.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_LESSONS_FILE = Path(__file__).resolve().parents[1] / "static" / "lessons.json"


@lru_cache(maxsize=1)
def _lessons() -> list[dict]:
    """Read once per process. The file ships with the deployment, so a restart
    is the only thing that can change it."""
    if not _LESSONS_FILE.exists():
        return []
    try:
        data = json.loads(_LESSONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


@router.get("", include_in_schema=False)
@router.get("/")
async def get_lessons() -> list[dict]:
    return _lessons()
