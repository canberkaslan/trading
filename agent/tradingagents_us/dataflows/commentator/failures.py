"""What a log line may say about a feed failure: never a post or video id.

An httpx status error's message is its request URL, and this feed's URLs
carry the ids of stored content in the query string: the X deletion check
(`/2/tweets?ids=...`) and `videos.list` (`?id=...`). A traceback repeats that
message. The daily run appends everything to a log file that nothing rotates,
and the retention timer writes to journald, so an id logged there outlives
the purge: a deleted X post past the 24-hour rule, a video past YouTube's
thirty days. The type and the HTTP status say what went wrong without it.
"""

from __future__ import annotations

import httpx


def describe(exc: BaseException) -> str:
    """The exception type, plus the status of an HTTP error. Never the message."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{type(exc).__name__} {exc.response.status_code}"
    return type(exc).__name__


def traceback_of(exc: BaseException) -> BaseException | None:
    """`exc` to pass as `exc_info`, or None when its chain holds an HTTP status error."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        link = pending.pop()
        if id(link) in seen:
            continue
        seen.add(id(link))
        if isinstance(link, httpx.HTTPStatusError):
            return None
        pending.extend(e for e in (link.__cause__, link.__context__) if e is not None)
    return exc
