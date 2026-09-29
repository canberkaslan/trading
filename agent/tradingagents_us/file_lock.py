"""Cross-process exclusive file locks.

The daily run now councils several tickers at once, each in its own process
(scripts/daily_run.sh). A handful of things those processes share were written
on the assumption that only one of them ever ran at a time: the vendor's
markdown memory log, the per-run source breakers, and the account read that
sizes an order. `flock` is what they coordinate through, because it is what
both the Linux box and a macOS dev checkout have, and because the kernel drops
the lock when a process dies, so a killed ticker cannot wedge the rest.

A second `flock` on a fresh descriptor for the same file blocks even inside the
process that already holds it, so a thread that re-enters a lock it holds would
deadlock itself. `exclusive` tracks what the current thread holds and passes a
re-entry straight through.
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_held = threading.local()

#: How often a timed acquisition re-tries a lock someone else holds.
_POLL_S = 0.05


class LockTimeoutError(TimeoutError):
    """The lock stayed held by someone else for longer than the caller allowed."""


def _held_paths() -> set[str]:
    paths = getattr(_held, "paths", None)
    if paths is None:
        paths = set()
        _held.paths = paths
    return paths


@contextmanager
def exclusive(path: str | os.PathLike[str], timeout_s: float | None = None) -> Iterator[None]:
    """Hold an exclusive lock on ``path`` (created if missing) for the block.

    ``timeout_s=None`` waits for as long as it takes. A number gives up with
    `LockTimeoutError` once that long has passed without the lock.
    """
    lock_path = Path(path)
    key = str(lock_path.resolve())
    held = _held_paths()
    if key in held:
        yield
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _acquire(fd, lock_path, timeout_s)
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _acquire(fd: int, lock_path: Path, timeout_s: float | None) -> None:
    if timeout_s is None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeoutError(
                    f"could not lock {lock_path} within {timeout_s:g}s: another "
                    f"process holds it; check for a stuck run before raising the limit"
                ) from None
            time.sleep(_POLL_S)
