"""Serialise the vendor's memory log across concurrently running councils.

`TradingMemoryLog` is one markdown file every council shares
(`TRADINGAGENTS_MEMORY_LOG_PATH`, default ~/.tradingagents/memory/). It was
written for one council at a time, and with the daily run's tickers now running
side by side two of its paths collide:

- resolving outcomes reads the whole file, rewrites it through a fixed
  ``<log>.tmp`` and renames it over the log. An entry another ticker appended
  in between is silently dropped, and two rewrites share the one temp file;
- appending a decision checks for a duplicate and then appends, so the check
  and the write can straddle another process's rewrite.

Giving every ticker its own log would end the collisions and also the thing the
log exists for: each council reads the other tickers' recent lessons from it.
So the log stays shared and its reads and writes take an exclusive lock on
``<log>.lock``. Each is a short file operation, never an LLM call, so the lock
is held for milliseconds.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tradingagents_us.file_lock import exclusive

#: The methods that touch the file. None of them calls another, so no path
#: re-enters the lock (and `exclusive` passes a re-entry through anyway).
_LOCKED_METHODS = (
    "store_decision",
    "update_with_outcome",
    "batch_update_with_outcomes",
    "load_entries",
)


def lock_path_for(log_path: Path) -> Path:
    return log_path.with_name(log_path.name + ".lock")


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
    """Wrap the memory log's file operations in the lock. Idempotent."""
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
