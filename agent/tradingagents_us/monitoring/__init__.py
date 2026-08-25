"""Watching the agent from somewhere the agent cannot influence.

Everything else under `tradingagents_us` assumes it is running on the box. This
package assumes the opposite: its code has to work on a CI runner with no
database, no secrets and no assumption that the host is even powered on. Keep it
stdlib-only and pure so it can be dropped into any off-box executor.
"""

from .liveness import (
    BackupSignal,
    HealthProbe,
    Verdict,
    classify,
    severity,
)

__all__ = [
    "BackupSignal",
    "HealthProbe",
    "Verdict",
    "classify",
    "severity",
]
