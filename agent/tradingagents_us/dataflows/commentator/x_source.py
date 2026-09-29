"""X API v2: the commentator's own posts, by pinned numeric user id.

Phase 2 of ADR-009. Complete, but inert until both an X_BEARER_TOKEN and a
pinned numeric user id exist (see `config.X_USER_ID`). The account is never
looked up by handle at runtime; `resolve_user_id` exists for the one-off
pinning step and nothing on the run path calls it.

Reads `GET /2/users/{id}/tweets` with `exclude=replies,retweets`, so what comes
back is his own top-level posts, and `since_id` so each run buys only what is
new. Pay-per-use bills per post returned, which is why the first run with
nothing stored is capped (`config.X_FIRST_RUN_MAX`) and later runs page at most
a few times.

Deletions: the Developer Agreement requires removing a post that was deleted or
made non-public. `lookup_alive` re-reads the stored ids each run; any id that
does not come back is purged by the ingest step. That check is also where a
stored post whose extraction failed gets its text back, so a retry costs
nothing extra.

The post text is read by the extractor and dropped; only derived fields are
stored, and no report quotes a post.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from .items import RawItem, X

log = logging.getLogger(__name__)

_BASE = "https://api.x.com/2"
_TIMEOUT = 10.0
_MAX_RESULTS = 100  # API maximum per page for both endpoints used here
_MIN_RESULTS = 5  # API minimum for /users/{id}/tweets
#: `note_tweet` carries the full text of a long post (over 280 characters);
#: without it `text` is only the truncated head.
_TWEET_FIELDS = "created_at,entities,lang,note_tweet"

#: With a since_id, how many pages one run may buy. Three pages is 300 posts,
#: far beyond this account's two or three a day; the cap is there so a bad
#: since_id cannot turn into the full history at $0.005 a post.
_MAX_PAGES = 3


class XClient:
    """Thin REST client. Methods raise; the ingest step decides what a failure costs."""

    def __init__(
        self, bearer_token: str, *, http: httpx.Client | None = None, timeout_s: float = _TIMEOUT
    ) -> None:
        self._http = http or httpx.Client(timeout=timeout_s)
        self._headers = {"Authorization": f"Bearer {bearer_token}"}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> XClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        r = self._http.get(f"{_BASE}{path}", params=params, headers=self._headers)
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, dict) else {}

    def user_posts_page(
        self,
        user_id: str,
        *,
        since_id: str | None,
        max_results: int,
        pagination_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "max_results": max(_MIN_RESULTS, min(_MAX_RESULTS, max_results)),
            "exclude": "replies,retweets",
            "tweet.fields": _TWEET_FIELDS,
            "expansions": "author_id",
            "user.fields": "username",
        }
        if since_id:
            params["since_id"] = since_id
        if pagination_token:
            params["pagination_token"] = pagination_token
        return self._get(f"/users/{user_id}/tweets", params)

    def lookup_alive(self, post_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
        """The posts among `post_ids` that X still serves, keyed by id.

        An id missing from the answer — deleted, protected, suspended, withheld
        — is absent here, and absence is what the caller acts on.
        """
        alive: dict[str, dict[str, Any]] = {}
        ids = [i for i in post_ids if i]
        for i in range(0, len(ids), _MAX_RESULTS):
            chunk = ids[i : i + _MAX_RESULTS]
            body = self._get("/tweets", {"ids": ",".join(chunk), "tweet.fields": _TWEET_FIELDS})
            for post in body.get("data") or []:
                if isinstance(post, dict) and post.get("id"):
                    alive[str(post["id"])] = post
        return alive

    def resolve_user_id(self, username: str) -> str:
        """One-off: handle -> numeric id, for a person to check and pin. Never on the run path."""
        body = self._get(f"/users/by/username/{username.lstrip('@')}", {})
        data = body.get("data") or {}
        uid = str(data.get("id") or "")
        if not uid.isdigit():
            raise LookupError(f"X user {username!r} not found")
        return uid


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)


def post_text(post: dict[str, Any]) -> str:
    """The post's full text: `note_tweet.text` for a long post, else `text`.

    X returns a long post's `text` truncated to its first 280 characters and
    puts the whole of it in `note_tweet.text` (when `note_tweet` is requested
    in `tweet.fields`). Extracting the truncated head would read half a claim.
    """
    note = post.get("note_tweet")
    if isinstance(note, dict):
        full = str(note.get("text") or "").strip()
        if full:
            return full
    return str(post.get("text") or "").strip()


def to_raw_item(post: dict[str, Any], user_id: str) -> RawItem | None:
    """A RawItem, or None for a post that must not be ingested (no id, no date, no text)."""
    pid = str(post.get("id") or "")
    when = _parse_ts(post.get("created_at"))
    text = post_text(post)
    if not pid or not text:
        return None
    if when is None:
        # No id in the log: it would outlive a deletion (see `failures`).
        log.warning("an X post carries no usable created_at; skipped rather than guessed")
        return None
    if post.get("author_id") and str(post["author_id"]) != user_id:
        return None
    return RawItem(
        source=X,
        source_id=pid,
        channel_id=user_id,
        # The handle-free permalink: it resolves by post id, whoever holds the handle.
        url=f"https://x.com/i/web/status/{pid}",
        published_at=when,
        text=text,
    )


def _check_username(body: dict[str, Any], user_id: str, expected: str) -> None:
    for user in (body.get("includes") or {}).get("users") or []:
        if str(user.get("id")) == user_id and user.get("username"):
            name = str(user["username"])
            if name.lower() != expected.lower():
                # Logged, never followed: the id is the account, the name is a label.
                log.warning(
                    "pinned X user %s now carries @%s (expected @%s); still tracking the id",
                    user_id, name, expected,
                )


def fetch_new(
    client: XClient,
    user_id: str,
    *,
    since_id: str | None,
    first_run_max: int,
    expected_username: str,
) -> list[RawItem]:
    """Posts newer than `since_id` (or the latest `first_run_max` with none), newest first."""
    posts: list[dict[str, Any]] = []
    token: str | None = None
    pages = _MAX_PAGES if since_id else 1
    for _ in range(pages):
        body = client.user_posts_page(
            user_id,
            since_id=since_id,
            max_results=_MAX_RESULTS if since_id else first_run_max,
            pagination_token=token,
        )
        _check_username(body, user_id, expected_username)
        posts.extend(p for p in body.get("data") or [] if isinstance(p, dict))
        token = (body.get("meta") or {}).get("next_token")
        if not token:
            break
    items = [to_raw_item(p, user_id) for p in posts]
    return sorted((i for i in items if i is not None), key=lambda i: i.published_at, reverse=True)
