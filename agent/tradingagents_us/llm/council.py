"""LLM council — diverse-model review of the house decision.

After the 7-agent pipeline produces a decision, a council of models from
*different families* (DeepSeek, Zhipu GLM) independently re-rates the same
research. Opus 4.8 (the chair) then weighs every vote against the house view
and issues the final call. Cross-family diversity catches blind spots a single
model family shares.

Wiring:
- Voters (DeepSeek, GLM) via **OpenRouter** (OPENROUTER_API_KEY, reused from
  the agentmesh stack) — OpenAI-compatible chat completions.
- Chair (Opus 4.8) via the **Anthropic** Messages API (ANTHROPIC_API_KEY).

Fail-safe by design: a missing key, a dead provider, or an unparseable reply
never breaks the pipeline — the affected vote is dropped, and if the chair
itself can't run, `council_review` returns None so the caller keeps the house
decision unchanged.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

import httpx

from ..schemas import Rating

log = logging.getLogger(__name__)

_RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")

# Council voters (cross-family). DeepSeek/GLM via OpenRouter (fast, cheap);
# qwen-local via on-box ollama (free, but CPU inference so it gets a longer
# timeout and is dropped if it can't answer in time).
VOTERS = [
    {"name": "deepseek", "provider": "openrouter", "model": "deepseek/deepseek-chat"},
    {"name": "glm", "provider": "openrouter", "model": "z-ai/glm-4.6"},
    {"name": "qwen-local", "provider": "ollama", "model": os.environ.get("OLLAMA_COUNCIL_MODEL", "qwen2.5:7b")},
]
CHAIR_MODEL = os.environ.get("LLM_COUNCIL_CHAIR", "claude-opus-4-8")

_OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
_ANTHROPIC = "https://api.anthropic.com/v1/messages"
_OLLAMA = os.environ.get("OLLAMA_BASE", "http://localhost:11434") + "/v1/chat/completions"
_TIMEOUT = 60.0
_OLLAMA_TIMEOUT = 120.0  # CPU inference is slow


@dataclass(frozen=True)
class Vote:
    member: str
    rating: Rating | None
    rationale: str


@dataclass
class CouncilResult:
    final_rating: Rating
    confidence: int  # 0-100
    chair_summary: str
    votes: list[Vote] = field(default_factory=list)


def _parse_rating(text: str) -> Rating | None:
    m = re.search(r"RATING\s*:?\s*(Buy|Overweight|Hold|Underweight|Sell)", text, re.I)
    if not m:
        return None
    return m.group(1).capitalize()  # type: ignore[return-value]


def _parse_confidence(text: str) -> int:
    m = re.search(r"CONFIDENCE\s*:?\s*(\d{1,3})", text, re.I)
    if not m:
        return 50
    return max(0, min(100, int(m.group(1))))


def _call_openrouter(model: str, system: str, user: str) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.post(
            _OPENROUTER,
            headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 600,
                "temperature": 0.2,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _call_ollama(model: str, system: str, user: str) -> str:
    """Local ollama via its OpenAI-compatible endpoint. No auth; slow on CPU."""
    with httpx.Client(timeout=_OLLAMA_TIMEOUT) as c:
        r = c.post(
            _OLLAMA,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 500,
                "temperature": 0.2,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def _call_anthropic(model: str, system: str, user: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    with httpx.Client(timeout=_TIMEOUT) as c:
        r = c.post(
            _ANTHROPIC,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 700,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))


_VOTER_SYSTEM = (
    "You are an independent equity analyst on a review council. You are shown a "
    "research digest and the house view for a US stock. State YOUR OWN rating — "
    "be willing to disagree with the house if the evidence warrants. Keep it to "
    "2-3 sentences of reasoning, then end with exactly:\nRATING: "
    "<Buy|Overweight|Hold|Underweight|Sell>"
)

_CHAIR_SYSTEM = (
    "You are the chair of an investment council and the most capable model "
    "present. You receive a research digest, the house view, and each council "
    "member's independent vote. Weigh agreement and dissent — a unanimous "
    "council strengthens conviction; genuine dissent warrants caution (prefer a "
    "less aggressive rating when the council is split). Give the FINAL decision "
    "in 2-4 sentences, then end with exactly two lines:\n"
    "RATING: <Buy|Overweight|Hold|Underweight|Sell>\nCONFIDENCE: <0-100>"
)


def _collect_votes(digest: str, house_block: str) -> list[Vote]:
    votes: list[Vote] = []
    user = f"{digest}\n\n{house_block}\n\nGive your independent rating."
    for v in VOTERS:
        try:
            if v["provider"] == "ollama":
                text = _call_ollama(v["model"], _VOTER_SYSTEM, user)
            else:
                text = _call_openrouter(v["model"], _VOTER_SYSTEM, user)
            votes.append(Vote(v["name"], _parse_rating(text), text.strip()[:1200]))
        except Exception as exc:  # noqa: BLE001 — drop a failed voter, keep going
            log.warning("council voter %s failed: %s", v["name"], exc)
    return votes


def council_review(
    ticker: str,
    digest: str,
    house_rating: str,
    house_rationale: str,
) -> CouncilResult | None:
    """Run the council. Returns None (keep house decision) on total failure."""
    house_block = (
        f"HOUSE VIEW for {ticker}: rating={house_rating}\n"
        f"House rationale: {house_rationale[:1500]}"
    )
    votes = _collect_votes(digest, house_block)

    vote_lines = "\n".join(
        f"- {v.member}: {v.rating or 'no-rating'} — {v.rationale[:300]}" for v in votes
    ) or "- (no council votes available)"
    chair_user = (
        f"{digest}\n\n{house_block}\n\nCOUNCIL VOTES:\n{vote_lines}\n\n"
        f"Issue the final decision for {ticker}."
    )
    try:
        chair_text = _call_anthropic(CHAIR_MODEL, _CHAIR_SYSTEM, chair_user)
    except Exception as exc:  # noqa: BLE001 — chair down -> keep house decision
        log.warning("council chair failed for %s: %s", ticker, exc)
        return None

    final = _parse_rating(chair_text)
    if final is None:
        log.warning("council chair gave no parseable rating for %s", ticker)
        return None
    return CouncilResult(
        final_rating=final,
        confidence=_parse_confidence(chair_text),
        chair_summary=chair_text.strip()[:2000],
        votes=votes,
    )
