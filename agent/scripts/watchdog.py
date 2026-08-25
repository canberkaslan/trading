#!/usr/bin/env python3
"""Off-box watchdog — probe the trader from outside and file an incident if it is gone.

Runs on a GitHub Actions runner (`.github/workflows/watchdog.yml`), never on the
box. That is the entire point: on 2026-08-24 the Hetzner host stopped answering
SSH, ICMP and the Cloudflare tunnel, and every alerting path we had — the daily
run's exit code, `OnFailure=`, `scripts/inert_alert`, the healthchecks.io ping —
was a process on that same host, so nobody was told. The outage was found the
next morning by hand.

Stdlib only, no repo dependencies, no secrets: it needs `GITHUB_TOKEN` (which
Actions injects) and an HTTPS GET. Anything more would be another thing that can
be misconfigured into silence, and this is the one alerter that has to work when
everything else is already broken.

    python scripts/watchdog.py --dry-run    # print the verdict, write nothing
    python scripts/watchdog.py              # ...and open/update/close the incident

Exits 0 even when the box is dark. The incident issue is the signal; a red run
on top of it would only add a second notification for the same news, and a
watchdog whose own job fails looks indistinguishable from a watchdog that is
broken.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from tradingagents_us.monitoring.liveness import (  # noqa: E402
    BackupSignal,
    HealthProbe,
    Verdict,
    classify,
    severity,
)

DEFAULT_HEALTH_URL = "https://trader.fusapp.com/healthz"
DEFAULT_BACKUP_REPO = "canberkaslan/trading-backups"
DEFAULT_ISSUE_REPO = "canberkaslan/trading"
ISSUE_LABEL = "watchdog"
PROBE_TIMEOUT_S = 15

# Written into the issue body so a later run can read back what it already
# reported. Issue state is the only memory this thing has — there is no box to
# keep a state file on, which is rather the situation it exists for.
_STATE_MARKER = "<!-- watchdog-state:"


def probe_health(url: str) -> HealthProbe:
    """GET the public health endpoint and decide whether the app itself answered.

    A status below 500 means something at the origin formed a reply — even a 404
    proves uvicorn is up. Cloudflare's tunnel errors (530/1033, 502) are the edge
    reporting that it never reached us, so they are 'no answer', not 'a sad
    answer'.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "ai-trader-watchdog/1"})
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            return HealthProbe(reached_origin=True, status=resp.status)
    except urllib.error.HTTPError as exc:
        return HealthProbe(reached_origin=exc.code < 500, status=exc.code)
    except Exception as exc:  # timeout, DNS, TLS — no status to report at all
        return HealthProbe(reached_origin=False, error=type(exc).__name__)


def probe_backup(repo: str, token: str | None, now: datetime) -> BackupSignal:
    """Age of the newest commit in the backup repo — the box's other heartbeat.

    Failures here return `age_hours=None` rather than a large age: a GitHub
    outage must never be able to declare the host dead.
    """
    url = f"https://api.github.com/repos/{repo}/commits?per_page=1"
    try:
        raw = _github_request("GET", url, token=token)
        newest = raw[0]["commit"]["committer"]["date"]
        when = datetime.fromisoformat(newest.replace("Z", "+00:00"))
        age = (now - when).total_seconds() / 3600.0
        return BackupSignal(age_hours=age, newest_commit_utc=newest)
    except Exception as exc:
        return BackupSignal(age_hours=None, error=f"{type(exc).__name__}: {exc}")


def _github_request(
    method: str, url: str, token: str | None, payload: dict[str, object] | None = None
) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-trader-watchdog/1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def _state_marker(state: str) -> str:
    return f"{_STATE_MARKER} {state} -->"


def _recorded_state(body: str) -> str | None:
    """Read back the state a previous run wrote into the issue body."""
    start = body.find(_STATE_MARKER)
    if start == -1:
        return None
    end = body.find("-->", start)
    if end == -1:
        return None
    return body[start + len(_STATE_MARKER) : end].strip() or None


def find_open_incident(repo: str, token: str | None) -> dict[str, object] | None:
    url = f"https://api.github.com/repos/{repo}/issues?state=open&labels={ISSUE_LABEL}&per_page=1"
    issues = _github_request("GET", url, token=token)
    if isinstance(issues, list) and issues:
        first = issues[0]
        if isinstance(first, dict):
            return first
    return None


def report(verdict: Verdict, repo: str, token: str | None, now: datetime) -> str:
    """Open, escalate, or close the incident. Returns what was done, for the log.

    One open issue at a time, and a comment only when the news changes. The
    workflow runs every 30 minutes; a dark host that commented on every run would
    produce 48 notifications a day for a single outage, and the second day of
    that is the day the alert stops being read.
    """
    existing = find_open_incident(repo, token)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")

    if not verdict.is_incident:
        if existing is None:
            return "up: no open incident, nothing to do"
        number = existing["number"]
        _github_request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues/{number}/comments",
            token=token,
            payload={"body": f"✅ Recovered at {stamp}.\n\n{verdict.body()}"},
        )
        _github_request(
            "PATCH",
            f"https://api.github.com/repos/{repo}/issues/{number}",
            token=token,
            payload={"state": "closed"},
        )
        return f"up: closed #{number}"

    body = f"{_state_marker(verdict.state)}\n\n{verdict.body()}\n\n_First seen: {stamp}_"

    if existing is None:
        created = _github_request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues",
            token=token,
            payload={
                "title": f"Watchdog: {verdict.headline}",
                "body": body,
                "labels": [ISSUE_LABEL],
            },
        )
        number = created["number"] if isinstance(created, dict) else "?"
        return f"{verdict.state}: opened #{number}"

    number = existing["number"]
    previous = _recorded_state(str(existing.get("body") or ""))
    if previous is not None and severity(verdict.state) <= severity(previous):
        return f"{verdict.state}: #{number} already open at '{previous}', staying quiet"

    _github_request(
        "PATCH",
        f"https://api.github.com/repos/{repo}/issues/{number}",
        token=token,
        payload={"title": f"Watchdog: {verdict.headline}", "body": body},
    )
    _github_request(
        "POST",
        f"https://api.github.com/repos/{repo}/issues/{number}/comments",
        token=token,
        payload={"body": f"⚠️ Escalated to `{verdict.state}` at {stamp}.\n\n{verdict.body()}"},
    )
    return f"{verdict.state}: escalated #{number} from '{previous}'"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the verdict, write nothing")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    token = os.environ.get("GITHUB_TOKEN")
    health_url = os.environ.get("WATCHDOG_HEALTH_URL", DEFAULT_HEALTH_URL)
    backup_repo = os.environ.get("WATCHDOG_BACKUP_REPO", DEFAULT_BACKUP_REPO)
    issue_repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_ISSUE_REPO)

    health = probe_health(health_url)
    backup = probe_backup(backup_repo, token, now)
    verdict = classify(health, backup)

    print(json.dumps({
        "checked_at": now.isoformat(),
        "state": verdict.state,
        "headline": verdict.headline,
        "health": health.describe(),
        "backup": backup.describe(),
        "reasons": list(verdict.reasons),
    }, indent=2))

    if args.dry_run:
        return 0
    if not token:
        print("watchdog: no GITHUB_TOKEN — verdict printed, no incident filed", file=sys.stderr)
        return 0

    try:
        print(report(verdict, issue_repo, token, now))
    except Exception as exc:  # never fail the run over the reporting side
        print(f"watchdog: could not file incident ({exc})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
