"""What the box knows about its own alerting, in a form the off-box watchdog can read.

Every alert raised on the box travels a channel that may be missing or dead:
the Expo push depends on an app, the GitHub half on a token, the dead-man's
switch on HEALTHCHECK_URL. Reporting "the alerting is broken" through that same
alerting is the loop that kept the 2026-09-14 broker outage quiet for nearly two
weeks. So the facts are published where no box secret is needed to see them:

  * preflight writes its last result to a small state file (`write_preflight`);
  * the API's /readyz serves that record plus the alerting config it can see
    in its own environment (`alerting_config`), which is the same secrets.env;
  * the watchdog, on a GitHub runner, reads /readyz and files an incident as
    github-actions[bot] when a check failed or a channel is missing.

Only fixed check names and booleans leave the box this way. /readyz is public
and so are the watchdog's issues: no message text, no URL, no token.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from tradingagents_us.notifications.ops_channel import GITHUB_TOKEN_ENV

#: Anchored to the agent root like inert_alert's state, never the CWD: preflight
#: and the API must read and write the same file wherever they were started.
_AGENT_ROOT = Path(__file__).resolve().parents[2]
STATE_ENV = "PREFLIGHT_STATE_PATH"
DEFAULT_STATE_FILE = "preflight.state.json"

#: Check names are code-defined tokens. Anything else read back from the file is
#: dropped rather than published.
_NAME = re.compile(r"^[a-z_]{1,32}$")

HEALTHCHECK_ENV = "HEALTHCHECK_URL"


def state_path() -> Path:
    env = os.environ.get(STATE_ENV, "").strip()
    return Path(env) if env else _AGENT_ROOT / DEFAULT_STATE_FILE


def healthcheck_configured(env: Mapping[str, str]) -> bool:
    url = env.get(HEALTHCHECK_ENV, "").strip()
    return url.startswith(("https://", "http://"))


def alerting_config(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    """Which off-phone channels this process's environment has. Presence only."""
    env = os.environ if env is None else env
    return {
        "healthcheck": healthcheck_configured(env),
        "github": bool(env.get(GITHUB_TOKEN_ENV, "").strip()),
    }


#: Stands in for an entry that is not a clean check name, so a failure recorded
#: under an odd name is still a failure rather than silently an empty list.
UNNAMED = "unnamed"


def _names(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list | tuple):
        return ()
    clean = {n if isinstance(n, str) and _NAME.match(n) else UNNAMED for n in raw}
    return tuple(sorted(clean))


@dataclass(frozen=True)
class PreflightRecord:
    """One preflight run: when, which dependency checks failed, which alert paths are missing."""

    at: str
    failed: tuple[str, ...]
    alerting_gaps: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failed

    def public(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "at": self.at,
            "failed": list(self.failed),
            "alerting_gaps": list(self.alerting_gaps),
        }

    @classmethod
    def from_names(
        cls, at: str, failed: list[str] | tuple[str, ...], gaps: list[str] | tuple[str, ...]
    ) -> PreflightRecord:
        return cls(at=at, failed=_names(list(failed)), alerting_gaps=_names(list(gaps)))


def write_preflight(record: PreflightRecord, path: Path | None = None) -> None:
    """Atomic, so a reader never sees half a record. Raises on I/O failure."""
    path = path or state_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record.public(), indent=2), encoding="utf-8")
    tmp.replace(path)


def read_preflight(path: Path | None = None) -> PreflightRecord | None:
    """The last recorded run, or None when there is none or it cannot be read.

    None is "unknown", never "failed": a box that has not run preflight since
    this shipped must not be reported as broken for it.
    """
    path = path or state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("at"), str):
        return None
    return PreflightRecord(
        at=raw["at"][:40],
        failed=_names(raw.get("failed")),
        alerting_gaps=_names(raw.get("alerting_gaps")),
    )
