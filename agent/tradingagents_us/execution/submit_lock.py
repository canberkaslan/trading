"""One writer at a time between "read the book" and "order at the broker".

Every path that sends orders for the account takes this lock: the daily run's
tickers (scripts/trade.py, now several at once), the position pass
(scripts/manage_positions.py) and a mobile approval (api/routes/orders.py).
A BUY is sized against the cash the account has left, so two BUYs sized from
the same read overspend it; the lock makes each one read the book after the
previous one's order is in.

An exit spends no cash, and dropping one is worse than sending it unordered:
it is the system's only discretionary way out of a position. So for an exit
the lock is best effort. It is tried for `EXIT_TIMEOUT_S`, and when it cannot
be had (still held, or the lock file unusable) the exit goes ahead without it,
loudly. A BUY that cannot have the lock is not sent.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tradingagents_us.file_lock import LockTimeoutError, acquire, release

log = logging.getLogger(__name__)

#: Overrides where the lock file lives.
SUBMIT_LOCK_ENV = "TRADE_SUBMIT_LOCK_PATH"
#: Next to the trade log DB on the box (/opt/ai-trader/agent/run/), not /tmp:
#: systemd's PrivateTmp and tmp cleaners must not split or delete it, and a
#: world-writable directory is the wrong place for a file others could replace.
_DEFAULT_LOCK = Path(__file__).resolve().parents[2] / "run" / "trade-submit.lock"

#: Longest a BUY waits. A ticker's whole critical section is one account read,
#: a sizing and one order, so a wait this long means something is stuck.
BUY_TIMEOUT_S = 600.0
#: Longest an exit waits before going ahead without the lock.
EXIT_TIMEOUT_S = 60.0


class SubmitLockUnavailableError(RuntimeError):
    """A cash-spending order could not take the lock, so it must not be sent."""


def lock_path() -> Path:
    configured = os.environ.get(SUBMIT_LOCK_ENV)
    return Path(configured) if configured else _DEFAULT_LOCK


@contextmanager
def submit_section(*, exit_only: bool, timeout_s: float | None = None) -> Iterator[bool]:
    """Hold the submit lock for the block; yields whether it is actually held.

    ``exit_only``: every order the block may send reduces exposure. Then a lock
    that cannot be had yields False instead of raising, and the caller sends
    anyway. Otherwise it raises `SubmitLockUnavailableError`.
    """
    wait = timeout_s if timeout_s is not None else (
        EXIT_TIMEOUT_S if exit_only else BUY_TIMEOUT_S
    )
    path = lock_path()
    try:
        fd = acquire(path, wait)
    except (LockTimeoutError, OSError) as exc:
        if not exit_only:
            raise SubmitLockUnavailableError(
                f"submit lock {path} unavailable ({exc}); no cash-spending order is "
                f"sent without it"
            ) from exc
        log.warning(
            "submit lock %s unavailable (%s); sending the exit without it", path, exc
        )
        yield False
        return
    try:
        yield True
    finally:
        release(fd)
