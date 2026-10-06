"""Fail-fast policy for the council's optional data sources.

Measured: a council took about fourteen and a half minutes, and a good part of
it was spent waiting on two sources whose absence the analysts already know how
to read. Polymarket was called with a 30 s timeout, and Reddit's RSS search
answered a 429 with a back-off of up to a minute, once per ticker, every ticker.
Neither is a source the decision cannot be made without: each already reports
its failure to the analyst as "unavailable" rather than as silence.

So the policy is to give up quickly and say so:

- short timeouts, the repo's 5 s for an HTTP call;
- at most one retry, after a short jittered pause, honouring a server's
  ``Retry-After`` only up to `RETRY_AFTER_CAP_S`;
- a circuit breaker per source that opens after a few consecutive failures
  and stays open for the rest of the run, so the next ten tickers do not each
  rediscover that the source is refusing us.

Reddit keeps its own one-minute 429 back-off: its limiter refuses a sooner
retry and lets that one through (sentiment_supplement.py).

"The run" is every ticker process of one daily run. Each ticker is its own
process, so a breaker held in memory would reset for every ticker and never
save anything; the daily run sets `RUN_STATE_DIR_ENV` to a directory of its
own, and the breaker keeps its count in a file there. Without it (the API, a
one-off CLI run) the count lives in the process. The API process lives for
weeks, so there an open breaker closes again after `MEMORY_OPEN_TTL_S`
rather than switching a source off until the next restart.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from tradingagents_us.file_lock import exclusive

log = logging.getLogger(__name__)

#: Directory one daily run shares between its ticker processes.
RUN_STATE_DIR_ENV = "TRADINGAGENTS_RUN_STATE_DIR"

#: Connect timeout. A little over a multiple of 3 s, the TCP retransmit window.
HTTP_CONNECT_TIMEOUT_S = 3.05
#: Read timeout: the repo standard for an HTTP call.
HTTP_READ_TIMEOUT_S = 5.0
#: Longest ``Retry-After`` honoured before the single retry. A server asking
#: for longer is refusing us for longer than a council should wait.
RETRY_AFTER_CAP_S = 5.0
#: Pause before the retry when the server gave no ``Retry-After``.
RETRY_BASE_S = 1.0
#: +/- fraction of jitter, so concurrent tickers do not retry in lockstep.
JITTER_FRACTION = 0.2
#: How long an in-process breaker stays open. A daily run's breaker needs no
#: expiry: its state directory is deleted with the run.
MEMORY_OPEN_TTL_S = 600.0


def jittered(seconds: float, frac: float = JITTER_FRACTION) -> float:
    """``seconds`` with +/-``frac`` random jitter, never negative."""
    return max(0.0, seconds * (1.0 + random.uniform(-frac, frac)))


def retry_delay(retry_after: str | None, cap_s: float = RETRY_AFTER_CAP_S) -> float:
    """Seconds to wait before the one retry.

    A ``Retry-After`` in seconds is honoured up to ``cap_s``. Beyond it, or when
    it is absent or an HTTP date, the pause is a jittered `RETRY_BASE_S` capped
    at ``cap_s``, so the retry is bounded whatever the server says.
    """
    if retry_after is not None:
        try:
            asked = float(retry_after)
        except ValueError:
            asked = None
        if asked is not None and asked >= 0:
            return min(asked, cap_s)
    return min(jittered(RETRY_BASE_S), cap_s)


@dataclass
class _Count:
    consecutive: int = 0
    open: bool = False
    #: `time.monotonic()` when it opened; used only for the in-process count.
    opened_at: float = 0.0


class RunBreaker:
    """Consecutive-failure breaker for one source, shared by one run's processes.

    Opens after ``limit`` failures in a row and then stays open: the source is
    skipped for the rest of the run. A success before that resets the count.
    It never raises; a state file it cannot use degrades to the in-process
    count, which is today's behaviour.
    """

    _memory: dict[str, _Count] = {}
    _memory_lock = threading.Lock()

    def __init__(self, source: str, limit: int) -> None:
        if limit < 1:
            raise ValueError(f"breaker limit must be at least 1, got {limit}")
        self.source = source
        self.limit = limit

    def is_open(self) -> bool:
        return self._update(None).open

    def record(self, *, success: bool) -> bool:
        """Count one outcome; returns whether the breaker is now open."""
        state = self._update(success)
        return state.open

    def reset(self) -> None:
        """Forget this source's count, in memory and in the run's file."""
        with self._memory_lock:
            self._memory.pop(self.source, None)
        path = self._state_path()
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not clear %s breaker state at %s: %s", self.source, path, exc)

    # -- internals ---------------------------------------------------------

    def _state_path(self) -> Path | None:
        run_dir = os.environ.get(RUN_STATE_DIR_ENV)
        if not run_dir:
            return None
        return Path(run_dir) / f"breaker-{self.source}.json"

    def _apply(self, state: _Count, success: bool | None) -> _Count:
        if success is None or state.open:
            return state
        if success:
            return _Count()
        consecutive = state.consecutive + 1
        opened = consecutive >= self.limit
        if opened:
            log.warning(
                "%s: %d consecutive failures, skipping it for the rest of this run",
                self.source, consecutive,
            )
        return _Count(consecutive=consecutive, open=opened)

    def _update(self, success: bool | None) -> _Count:
        path = self._state_path()
        if path is not None:
            try:
                return self._update_file(path, success)
            except OSError as exc:
                log.warning(
                    "%s breaker state at %s unusable (%s); counting in-process",
                    self.source, path, exc,
                )
        with self._memory_lock:
            state = self._memory.get(self.source, _Count())
            if state.open and time.monotonic() - state.opened_at >= MEMORY_OPEN_TTL_S:
                log.info("%s: breaker open for %.0fs, trying the source again",
                         self.source, MEMORY_OPEN_TTL_S)
                state = _Count()
            new = self._apply(state, success)
            if new.open and not state.open:
                new = _Count(new.consecutive, True, time.monotonic())
            self._memory[self.source] = new
            return new

    def _update_file(self, path: Path, success: bool | None) -> _Count:
        with exclusive(path.with_suffix(".lock")):
            state = _Count()
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    state = _Count(int(raw.get("consecutive", 0)), bool(raw.get("open")))
                except (ValueError, TypeError, AttributeError):
                    state = _Count()
            new = self._apply(state, success)
            if (new.consecutive, new.open) != (state.consecutive, state.open):
                tmp = path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps({"consecutive": new.consecutive, "open": new.open}),
                    encoding="utf-8",
                )
                tmp.replace(path)
            return new
