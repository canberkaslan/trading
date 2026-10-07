"""Serialise the vendor's memory log across councils running side by side.

`TradingMemoryLog` is one markdown file every council shares
(`TRADINGAGENTS_MEMORY_LOG_PATH`, default ~/.tradingagents/memory/), written for
one council at a time. With daily_run.sh councilling tickers in parallel
(COUNCIL_PARALLELISM), two of its paths collide:

- resolving outcomes reads the whole file, rewrites it through a fixed
  ``<log>.tmp`` and renames it over the log, so an entry another ticker appended
  in between is silently dropped, and two rewrites share the one temp file;
- appending a decision checks for a duplicate and then appends, so the check
  and the write can straddle another process's rewrite.

One log per ticker would end the collisions and the point of the log too: each
council reads the other tickers' recent lessons from it. So the file stays
shared and its reads and writes hold an exclusive `flock` on ``<log>.lock``.
Each is a short file operation, never an LLM call, so the lock is held for
milliseconds, and the kernel drops it when a process dies, so a killed council
cannot wedge the others.
"""

from __future__ import annotations

import fcntl
import functools
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: The methods that touch the file. None calls another; a re-entry on the same
#: thread passes straight through anyway, since a second flock would deadlock.
_LOCKED_METHODS = (
    "store_decision",
    "update_with_outcome",
    "batch_update_with_outcomes",
    "load_entries",
)

_held = threading.local()


def lock_path_for(log_path: Path) -> Path:
    return log_path.with_name(log_path.name + ".lock")


@contextmanager
def exclusive(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive flock on ``lock_path`` (created if missing) for the block."""
    key = str(lock_path.absolute())
    held: set[str] = getattr(_held, "paths", set())
    _held.paths = held
    if key in held:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # O_NOFOLLOW: a lock file swapped for a symlink must not make this process
    # create or truncate a file somewhere else.
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def _locked(method: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        log_path = getattr(self, "_log_path", None)
        if not log_path:
            return method(self, *args, **kwargs)
        with exclusive(lock_path_for(Path(log_path))):
            return method(self, *args, **kwargs)

    wrapper._memory_locked = True  # type: ignore[attr-defined]
    return wrapper


def install() -> bool:
    """Wrap the memory log's file operations in the lock. Idempotent.

    False when the vendor class or one of its methods has moved, so the caller
    can say the log is unprotected rather than assume it is.
    """
    try:
        from tradingagents.agents.utils.memory import TradingMemoryLog
    except ImportError:
        return False
    wrapped = 0
    for name in _LOCKED_METHODS:
        method = getattr(TradingMemoryLog, name, None)
        if method is None:
            continue
        if not getattr(method, "_memory_locked", False):
            setattr(TradingMemoryLog, name, _locked(method))
        wrapped += 1
    return wrapped == len(_LOCKED_METHODS)
