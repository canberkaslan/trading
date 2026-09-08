"""The Learn lesson set: served correctly, and identical in both apps.

The drift test is the load-bearing one. The lessons are authored once and
copied into the mobile bundle, so nothing but a test stops the two from
diverging — and a divergence is invisible until someone reads the same lesson
on both screens and finds different content.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app

_AGENT_COPY = Path(__file__).resolve().parents[1] / "api" / "static" / "lessons.json"
_MOBILE_COPY = (
    Path(__file__).resolve().parents[2] / "mobile" / "app" / "src" / "content" / "lessons.json"
)

client = TestClient(app)


def _lessons() -> list[dict]:
    return json.loads(_AGENT_COPY.read_text(encoding="utf-8"))


def test_endpoint_serves_the_lessons() -> None:
    r = client.get("/v1/learn")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and body
    assert {lesson["id"] for lesson in body} == {lesson["id"] for lesson in _lessons()}


def test_endpoint_needs_no_token() -> None:
    """It is general education — no position, no equity, nothing account-specific
    — so the dashboard must not have to be signed in to explain its own words."""
    r = client.get("/v1/learn")
    assert r.status_code == 200


def test_mobile_copy_has_not_drifted() -> None:
    assert _MOBILE_COPY.exists(), "mobile lesson bundle is missing"
    assert _MOBILE_COPY.read_text(encoding="utf-8") == _AGENT_COPY.read_text(encoding="utf-8"), (
        "mobile/app/src/content/lessons.json differs from agent/api/static/lessons.json — "
        "re-copy so both apps teach the same thing"
    )


def test_every_lesson_is_complete_in_both_languages() -> None:
    for lesson in _lessons():
        for lang in ("tr", "en"):
            text = lesson[lang]
            assert text["title"] and text["summary"] and text["takeaway"], lesson["id"]
            assert text["body"], lesson["id"]
            assert len(text["quizOptions"]) == 3, lesson["id"]
            assert 0 <= text["quizAnswer"] < 3, lesson["id"]
            assert text["quizExplain"], lesson["id"]


def test_quiz_options_are_distinct() -> None:
    """Two identical options would make the question unanswerable."""
    for lesson in _lessons():
        for lang in ("tr", "en"):
            opts = lesson[lang]["quizOptions"]
            assert len(set(opts)) == len(opts), f"{lesson['id']}/{lang} repeats an option"


def test_lessons_do_not_claim_dead_controls_are_live() -> None:
    """The sector cap and the circuit breaker's daily-drawdown halt cannot fire
    as currently wired (every order is passed sector="Unknown"; the breaker gets
    current equity as its session-open equity). A lesson that presents either as
    active protection teaches the operator to rely on a safety net that is not
    there — the one failure mode in this content that could actually cost money.
    """
    blob = _AGENT_COPY.read_text(encoding="utf-8")
    for claim in ("shrink the tail in advance", "kuyruğu sonradan ölçmek yerine baştan kısıyor"):
        assert claim not in blob, f"lesson still presents inert controls as protection: {claim!r}"
