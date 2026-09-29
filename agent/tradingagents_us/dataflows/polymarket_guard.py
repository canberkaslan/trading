"""Fail-fast Polymarket for the news analyst.

The vendored Polymarket client (`tradingagents.dataflows.polymarket`) calls the
Gamma API with a 30 s timeout and no retry policy, and the news analyst may
call it several times per ticker. When Gamma is slow, that is minutes of a
council spent waiting on an optional signal.

`install` swaps the client's `_request` for one on the policy in `fail_fast`:
5 s timeouts, one bounded retry on a transient failure, and a breaker shared by
the run that stops calling Gamma once it has failed a few times in a row.

Every failure still leaves as a `requests.RequestException`, which the vendor's
`get_prediction_markets` already turns into "Polymarket data is currently
unavailable ... Proceed without prediction-market signal". So the analyst reads
an absent source as absent, including when the breaker skipped the call, and no
vendor file is edited.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from tradingagents_us.dataflows.fail_fast import (
    HTTP_CONNECT_TIMEOUT_S,
    HTTP_READ_TIMEOUT_S,
    RunBreaker,
    retry_delay,
)

log = logging.getLogger(__name__)

#: Consecutive failed calls before Polymarket is skipped for the run.
FAILURE_LIMIT = 2

#: Statuses worth one retry: rate limited, or the server's own trouble.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})

BREAKER = RunBreaker("polymarket", limit=FAILURE_LIMIT)

#: Indirection so tests can skip the real pause.
_sleep: Callable[[float], None] = time.sleep


class PolymarketSkippedError(requests.ConnectionError):
    """Raised instead of calling Gamma once the run's breaker is open."""


def _get(base: str, path: str, params: dict[str, Any]) -> requests.Response:
    return requests.get(
        f"{base}/{path}",
        params=params,
        timeout=(HTTP_CONNECT_TIMEOUT_S, HTTP_READ_TIMEOUT_S),
    )


def fail_fast_request(base: str, path: str, params: dict[str, Any]) -> Any:
    """GET ``base/path`` under the fail-fast policy; returns the parsed JSON."""
    if BREAKER.is_open():
        raise PolymarketSkippedError(
            f"skipped: Polymarket failed {FAILURE_LIMIT} times in a row earlier in "
            f"this run, so it is not being called again (this is not an absence "
            f"of markets)"
        )
    for attempt in (1, 2):
        try:
            response = _get(base, path, params)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == 1:
                log.info("Polymarket %s: %s; retrying once", path, exc)
                _sleep(retry_delay(None))
                continue
            BREAKER.record(success=False)
            raise
        if response.status_code in _TRANSIENT_STATUS and attempt == 1:
            wait = retry_delay(response.headers.get("Retry-After"))
            log.info(
                "Polymarket %s: HTTP %d; retrying once in %.1fs",
                path, response.status_code, wait,
            )
            _sleep(wait)
            continue
        if 400 <= response.status_code < 500 and response.status_code != 429:
            # Our request was refused, not the source down: a bad query from
            # one ticker must not switch Polymarket off for the others.
            response.raise_for_status()
        try:
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError):
            BREAKER.record(success=False)
            raise
        BREAKER.record(success=True)
        return data
    # Unreachable: the second attempt always returns or raises.
    raise requests.ConnectionError(f"Polymarket {path}: no attempt completed")


def install() -> bool:
    """Route the vendored client through `fail_fast_request`. Idempotent."""
    try:
        from tradingagents.dataflows import polymarket as mod
    except ImportError:
        return False
    current = getattr(mod, "_request", None)
    if current is None:
        return False
    if getattr(current, "_fail_fast", False):
        return True

    def guarded(path: str, params: dict[str, Any]) -> Any:
        return fail_fast_request(mod.GAMMA_BASE, path, params)

    guarded._fail_fast = True  # type: ignore[attr-defined]
    guarded._wraps = current  # type: ignore[attr-defined]
    mod._request = guarded
    return True
