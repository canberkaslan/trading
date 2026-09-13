"""Keep credentials out of the logs.

`httpx` logs every request at INFO as `HTTP Request: GET <full url>`, and some
vendors carry the key in the query string rather than a header — Polygon uses
`?apiKey=...`. So a routine, correctly-configured run writes a live credential
into its own log file in plain text, once per call.

Logs are the wrong trust boundary for this. They are read over someone's
shoulder during a debug session, pasted into chat when something breaks,
shipped to whatever aggregator gets wired up next, and kept far longer than a
key rotation cycle. The backup here happens to cover only local.db, snapshots
and memory — but "the leak was contained because of what today's backup script
copies" is luck, not a control.

The fix is a filter rather than silencing httpx: the request lines are useful,
it is the secret inside them that is not. Anything matching a known
credential-bearing parameter is replaced with a marker that still shows the
parameter was present, so a redacted URL reads as redacted rather than as a
request that never carried a key.

Applied at the root logger so it covers every handler, including ones added
later by a library.
"""

from __future__ import annotations

import logging
import re

# Query parameters that carry secrets. Matched case-insensitively, and the
# value runs to the next separator so a trailing `&other=...` survives intact.
_SECRET_PARAMS = (
    "apikey",
    "api_key",
    "token",
    "access_token",
    "key",
    "secret",
    "password",
    "signature",
)

_QUERY_SECRET = re.compile(
    r"(?i)\b(" + "|".join(_SECRET_PARAMS) + r")=([^&\s\"']+)"
)

# Bearer tokens can appear in logged headers or exception text.
_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-]{8,})")

_MARK = "<redacted>"


def redact(text: str) -> str:
    """Replace credential values in `text`, keeping the surrounding structure."""
    out = _QUERY_SECRET.sub(lambda m: f"{m.group(1)}={_MARK}", text)
    return _BEARER.sub(lambda m: f"{m.group(1)}{_MARK}", out)


class RedactingFilter(logging.Filter):
    """Strip credentials from a record before any handler formats it.

    Rewrites `record.msg` and `record.args` rather than the formatted output,
    because a Filter runs before formatting and that is the only point where
    every handler is covered by one change.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: redact(v) if isinstance(v, str) else v
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        redact(a) if isinstance(a, str) else a for a in record.args
                    )
        except Exception:  # noqa: BLE001 — a redaction bug must not drop the log line
            pass
        # Always emit: this filter censors, it never suppresses. A filter that
        # could return False would silently lose lines on a regex edge case.
        return True


def install() -> None:
    """Attach the filter to the root logger and to existing handlers. Idempotent.

    Handlers get it too: a handler attached before this runs formats records
    that never pass through a root-logger filter.
    """
    root = logging.getLogger()
    if not any(isinstance(f, RedactingFilter) for f in root.filters):
        root.addFilter(RedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
