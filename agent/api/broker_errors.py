"""Name a failing broker instead of surfacing a bare 5xx.

Every read-only route that asks Alpaca something used to answer 502 with the
raw exception text. Two problems, both seen during the Sept 2026 revoked-key
window: Cloudflare replaces an origin 502 body with its own "error code: 502"
page, so the app never saw *why*; and the raw text carried the broker URL.

`broker_http_exception` maps the httpx errors we can name to 503 (nothing about
the request is wrong and a retry may succeed) with a stable machine reason.
Anything else keeps the old 502 contract — an unknown failure is still not a
clean answer — but without echoing the exception.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import HTTPException

log = logging.getLogger(__name__)

_AUTH_REFUSED = (401, 403)


def broker_http_exception(exc: Exception) -> HTTPException:
    """Translate an exception raised while talking to the broker."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        reason = "broker_auth_refused" if status in _AUTH_REFUSED else "broker_error"
        return HTTPException(503, f"{reason}: alpaca answered {status}")
    if isinstance(exc, httpx.TransportError):
        return HTTPException(503, "broker_unreachable")
    log.warning("broker call failed: %s", type(exc).__name__, exc_info=exc)
    return HTTPException(502, f"alpaca_error: {type(exc).__name__}")
