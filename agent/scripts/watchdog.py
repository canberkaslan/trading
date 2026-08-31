#!/usr/bin/env python3
"""Off-box watchdog — probe the trader from outside and file an incident if it is gone.

Runs on a GitHub Actions runner (`.github/workflows/watchdog.yml`), never on the
box. That is the entire point: on 2026-08-24 the Hetzner host stopped answering
SSH, ICMP and the Cloudflare tunnel, and every alerting path we had — the daily
run's exit code, `OnFailure=`, `scripts/inert_alert`, the healthchecks.io ping —
was a process on that same host, so nobody was told. The outage was found the
next morning by hand.

Stdlib only, no repo dependencies: it needs `GITHUB_TOKEN` (which Actions
injects) and an HTTPS GET. Anything more would be another thing that can be
misconfigured into silence, and this is the one alerter that has to work when
everything else is already broken.

Two optional secrets sharpen the verdict, and it degrades to the previous
behaviour without either: `WATCHDOG_BACKUP_TOKEN` (read access to the private
backups repo) and `WATCHDOG_HOST` (`host[:port]`, comma-separated, for a direct
TCP probe of the origin). Either one is enough to tell a dead tunnel from a dead
host; with neither, the watchdog can only report that it cannot tell — which is
exactly what it did for a day and a half in the 2026-08-25 incident.

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
import socket
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

from tradingagents_us.monitoring.incident_clock import (  # noqa: E402
    format_stamp,
    render_recovery,
    render_timeline,
)
from tradingagents_us.monitoring.liveness import (  # noqa: E402
    BackupSignal,
    HealthProbe,
    HostProbe,
    Verdict,
    classify,
    severity,
)

DEFAULT_HEALTH_URL = "https://trader.fusapp.com/healthz"
DEFAULT_BACKUP_REPO = "canberkaslan/trading-backups"
DEFAULT_ISSUE_REPO = "canberkaslan/trading"
ISSUE_LABEL = "watchdog"
PROBE_TIMEOUT_S = 15

# Shorter than the HTTP probe: a TCP handshake either happens in a moment or is
# being dropped, and there is no third outcome worth waiting fifteen seconds for.
TCP_TIMEOUT_S = 5
DEFAULT_TCP_PORT = 22

# Written into the issue body so a later run can read back what it already
# reported. Issue state is the only memory this thing has — there is no box to
# keep a state file on, which is rather the situation it exists for.
_STATE_MARKER = "<!-- watchdog-state:"

# The two instants that make the page's own freshness legible: when this outage
# started (preserved across escalations, so a retitle cannot reset the clock)
# and when it was last confirmed (rewritten every run, including the quiet ones).
_FIRST_SEEN_MARKER = "<!-- watchdog-first-seen:"
_CHECKED_AT_MARKER = "<!-- watchdog-checked-at:"


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

    The backup repo is private and the workflow's built-in `GITHUB_TOKEN` is
    scoped to this repository alone, so it reads as 404 (GitHub hides private
    repos rather than admitting they exist). That is a configuration gap, not a
    GitHub problem, and the two want completely different responses from whoever
    reads the alert — so name it, with the fix in the text.
    """
    url = f"https://api.github.com/repos/{repo}/commits?per_page=1"
    try:
        raw = _github_request("GET", url, token=token)
        newest = raw[0]["commit"]["committer"]["date"]
        when = datetime.fromisoformat(newest.replace("Z", "+00:00"))
        age = (now - when).total_seconds() / 3600.0
        return BackupSignal(age_hours=age, newest_commit_utc=newest)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            return BackupSignal(
                age_hours=None,
                error=(
                    f"no read access to {repo} (HTTP {exc.code}) — set a WATCHDOG_BACKUP_TOKEN "
                    "secret with read access to that private repo"
                ),
            )
        return BackupSignal(age_hours=None, error=f"HTTP {exc.code}")
    except Exception as exc:
        return BackupSignal(age_hours=None, error=f"{type(exc).__name__}: {exc}")


def _parse_targets(spec: str) -> list[tuple[str, int]]:
    """Read `host[:port][,host[:port]...]` into connect targets.

    Bad entries are skipped rather than raised on: this runs in the one alerter
    that has to work when everything else is broken, and a typo in an optional
    signal must not be able to take the whole watchdog down with it.
    """
    targets: list[tuple[str, int]] = []
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        host, _, port = entry.rpartition(":")
        if not host:  # no colon at all — the whole entry is the address
            targets.append((entry, DEFAULT_TCP_PORT))
        elif port.isdigit():
            targets.append((host, int(port)))
        else:
            targets.append((entry, DEFAULT_TCP_PORT))
    return targets


def probe_host(spec: str | None) -> HostProbe:
    """Ask the origin's own IP stack whether it is there, bypassing every proxy.

    A refused connection counts as an answer and is arguably the better one: an
    RST is the host's kernel talking, with nothing else on the machine involved.
    A timeout is not evidence of death — a firewall told to drop produces exactly
    the same silence — so it is reported as "no answer" and the policy in
    `liveness` refuses to promote it to an outage on its own.

    The address comes from `WATCHDOG_HOST` (a secret) and never leaves this
    function: the repo and the incident issues it files are public, and the
    origin IP behind Cloudflare is the one fact that must not be published there.
    Only fixed category strings are handed back.
    """
    if not spec:
        return HostProbe(configured=False)

    targets = _parse_targets(spec)
    if not targets:
        return HostProbe(configured=False)

    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=TCP_TIMEOUT_S):
                return HostProbe(configured=True, answered=True, detail="connection accepted")
        except ConnectionRefusedError:
            # Nothing listening, but something is home. That is the question asked.
            return HostProbe(configured=True, answered=True, detail="connection refused")
        except (TimeoutError, OSError):
            continue  # try the next target before concluding silence

    return HostProbe(configured=True, answered=False, detail="timed out or filtered")


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


def _marker(name: str, value: str) -> str:
    return f"{name} {value} -->"


def _state_marker(state: str) -> str:
    return _marker(_STATE_MARKER, state)


def _read_marker(body: str, name: str) -> str | None:
    """Read back a value a previous run hid in an HTML comment in the issue body."""
    start = body.find(name)
    if start == -1:
        return None
    end = body.find("-->", start)
    if end == -1:
        return None
    return body[start + len(name) : end].strip() or None


def _recorded_state(body: str) -> str | None:
    """Read back the state a previous run wrote into the issue body."""
    return _read_marker(body, _STATE_MARKER)


def _parse_stamp(raw: str | None) -> datetime | None:
    """An ISO instant from a marker, or None if it is missing or malformed.

    Never raises. This is the one alerter that has to work when everything else
    is broken, and a hand-edited issue body is not a reason for it to stop
    reporting an outage — a lost timestamp costs a line of prose, an exception
    costs the alert.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _incident_start(existing: dict[str, object], fallback: datetime) -> datetime | None:
    """When this outage actually began, in preference order.

    The marker first, then GitHub's own `created_at`. That second source matters
    right now: issue #1 was opened before the marker existed, and without the
    fallback its four-day-old outage would read as having started on whichever
    run first wrote the new format.

    A `fallback` of `now` is only used when both are unavailable — a body a human
    replaced entirely on an issue the API did not date.
    """
    from_marker = _parse_stamp(_read_marker(str(existing.get("body") or ""), _FIRST_SEEN_MARKER))
    if from_marker is not None:
        return from_marker
    created = existing.get("created_at")
    return _parse_stamp(created if isinstance(created, str) else None) or fallback


def _build_body(
    verdict: Verdict,
    now: datetime,
    first_seen: datetime | None,
    previous_check: datetime | None,
) -> str:
    """The full issue body: machine-readable markers, the verdict, then the clock."""
    markers = "\n".join(
        [
            _state_marker(verdict.state),
            _marker(_FIRST_SEEN_MARKER, (first_seen or now).isoformat()),
            _marker(_CHECKED_AT_MARKER, now.isoformat()),
        ]
    )
    timeline = render_timeline(now, first_seen, previous_check)
    return f"{markers}\n\n{verdict.body()}\n\n{timeline}"


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

    One open issue at a time, and a *comment* only when the news changes. The
    workflow runs every 30 minutes; a dark host that commented on every run would
    produce 48 notifications a day for a single outage, and the second day of
    that is the day the alert stops being read.

    The body, though, is rewritten on every run — including the quiet ones.
    Editing an issue body notifies nobody, so it costs the reader nothing, and it
    is the only way the page can say when it was last confirmed. Without it,
    "quiet" and "the watchdog died too" produce an identical, frozen page: that
    is exactly what issue #1 looked like for four days.
    """
    existing = find_open_incident(repo, token)
    stamp = format_stamp(now)

    if not verdict.is_incident:
        if existing is None:
            return "up: no open incident, nothing to do"
        number = existing["number"]
        _github_request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues/{number}/comments",
            token=token,
            payload={"body": f"{render_recovery(now, _incident_start(existing, now))}\n\n"
                             f"{verdict.body()}"},
        )
        _github_request(
            "PATCH",
            f"https://api.github.com/repos/{repo}/issues/{number}",
            token=token,
            payload={"state": "closed"},
        )
        return f"up: closed #{number}"

    if existing is None:
        created = _github_request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues",
            token=token,
            payload={
                "title": f"Watchdog: {verdict.headline}",
                "body": _build_body(verdict, now, first_seen=now, previous_check=None),
                "labels": [ISSUE_LABEL],
            },
        )
        number = created["number"] if isinstance(created, dict) else "?"
        return f"{verdict.state}: opened #{number}"

    number = existing["number"]
    old_body = str(existing.get("body") or "")
    previous = _recorded_state(old_body)
    # Carried over rather than restamped: an escalation retitles the incident, it
    # does not start a new one, and the old code's `_First seen: {now}` quietly
    # reset the age of every outage that got worse.
    first_seen = _incident_start(existing, now)
    previous_check = _parse_stamp(_read_marker(old_body, _CHECKED_AT_MARKER))
    body = _build_body(verdict, now, first_seen, previous_check)

    if previous is not None and severity(verdict.state) <= severity(previous):
        # Same news. Refresh the body so the clock advances, and say nothing.
        _github_request(
            "PATCH",
            f"https://api.github.com/repos/{repo}/issues/{number}",
            token=token,
            payload={"body": body},
        )
        return (
            f"{verdict.state}: #{number} already open at '{previous}', "
            "refreshed the clock and staying quiet"
        )

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


def check_once(now: datetime, dry_run: bool = False) -> Verdict:
    """One probe → classify → print → report cycle. Returns the verdict.

    Split out of `main` so the relay driver can call it on a loop without
    re-implementing any of it. Everything below reads the environment on each
    call rather than closing over it at import: a five-hour relay link that
    froze its configuration at launch would keep probing a URL the operator had
    already corrected.
    """
    token = os.environ.get("GITHUB_TOKEN")
    health_url = os.environ.get("WATCHDOG_HEALTH_URL", DEFAULT_HEALTH_URL)
    backup_repo = os.environ.get("WATCHDOG_BACKUP_REPO", DEFAULT_BACKUP_REPO)
    issue_repo = os.environ.get("GITHUB_REPOSITORY", DEFAULT_ISSUE_REPO)

    # The backup repo is private and lives outside this repository, so the
    # built-in token cannot see it. Prefer a PAT when one is configured; fall
    # back to GITHUB_TOKEN so the health half still works with no setup at all.
    backup_token = os.environ.get("WATCHDOG_BACKUP_TOKEN") or token

    # Optional and secret. Without it the watchdog behaves exactly as it did
    # before the probe existed; with it, "the host is up" becomes provable
    # without any credential at all. Never defaulted to a literal address —
    # the origin IP behind Cloudflare does not belong in a public repo.
    host_spec = os.environ.get("WATCHDOG_HOST")

    health = probe_health(health_url)
    backup = probe_backup(backup_repo, backup_token, now)
    host = probe_host(host_spec)
    verdict = classify(health, backup, host)

    print(json.dumps({
        "checked_at": now.isoformat(),
        "state": verdict.state,
        "headline": verdict.headline,
        "health": health.describe(),
        "backup": backup.describe(),
        "host": host.describe(),
        "reasons": list(verdict.reasons),
    }, indent=2))

    if dry_run:
        return verdict
    if not token:
        print("watchdog: no GITHUB_TOKEN — verdict printed, no incident filed", file=sys.stderr)
        return verdict

    try:
        print(report(verdict, issue_repo, token, now))
    except Exception as exc:  # never fail the run over the reporting side
        print(f"watchdog: could not file incident ({exc})", file=sys.stderr)
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the verdict, write nothing")
    args = parser.parse_args()

    check_once(datetime.now(UTC), dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
