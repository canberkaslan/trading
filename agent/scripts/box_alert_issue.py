#!/usr/bin/env python3
"""File one box alert as a GitHub issue, from a GitHub Actions runner.

The trading box does not write issues itself. It dispatches
`.github/workflows/box-alert.yml` with the alert as inputs, and that workflow
runs this script with its own `GITHUB_TOKEN`, so the issue and every repeat
comment are authored by github-actions[bot].

Who the author is decides whether anyone hears about it. GitHub sends no
notification for your own activity unless you opt in, and a fine-grained PAT
on a personally owned repo can only be minted by the owner, so an issue the box
filed with that token would be the owner's own issue: a thread nobody is
emailed about. The watchdog's incidents do notify, because the workflow token
files them as the bot. This puts box alerts on that same path.

One open issue per alert kind; a recurrence is a comment on it. The thread is
found by a marker in the body, and only on an issue the bot itself opened: in a
public repo anyone can open an issue carrying the marker, and a stranger's
issue must not become the place alerts go.

Stdlib only, like the watchdog, so a broken lockfile cannot take this path down.
Exits 1 when the issue could not be written: a failed run is emailed to whoever
dispatched it, which is the last way this alert can still reach the owner.

    ALERT_KIND=daily_run ALERT_TITLE=... ALERT_BODY=... python agent/scripts/box_alert_issue.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable, Mapping

API = "https://api.github.com"
DEFAULT_REPO = "canberkaslan/trading"
ISSUE_LABEL = "box-alert"
BOT_LOGIN = "github-actions[bot]"
TIMEOUT_S = 15

#: Kinds are machine tokens that go inside an HTML comment in the issue body.
#: Anything else is filed under "ops" rather than trusted into the marker.
KIND_PATTERN = re.compile(r"^[a-z0-9_]{1,40}$")
FALLBACK_KIND = "ops"
TITLE_LIMIT = 200
#: GitHub rejects issue bodies over 65,536 characters.
BODY_LIMIT = 60_000

MARKER = "<!-- box-alert-kind:{kind} -->"

Request = Callable[[str, str, dict[str, object] | None], object]


def safe_kind(raw: str) -> str:
    kind = raw.strip()
    return kind if KIND_PATTERN.match(kind) else FALLBACK_KIND


def _http_request(token: str) -> Request:
    def request(method: str, url: str, payload: dict[str, object] | None = None) -> object:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "ai-trader-box-alert/2",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = json.dumps(payload).encode() if payload is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = resp.read()
        return json.loads(body) if body else {}

    return request


def find_thread(request: Request, repo: str, kind: str) -> int | None:
    """The open issue the bot opened for this kind, if there is one."""
    issues = request("GET", f"{API}/repos/{repo}/issues?state=open&per_page=100", None)
    marker = MARKER.format(kind=kind)
    for issue in issues if isinstance(issues, list) else []:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        author = issue.get("user")
        if not isinstance(author, dict) or author.get("login") != BOT_LOGIN:
            continue
        if marker in str(issue.get("body") or ""):
            return int(issue["number"])
    return None


def file_alert(
    request: Request, repo: str, kind: str, title: str, body: str, raised_at: str
) -> str:
    """Open the kind's issue or comment on it. Returns what was done."""
    kind = safe_kind(kind)
    title = (title.strip() or f"{kind} alert")[:TITLE_LIMIT]
    body = body[:BODY_LIMIT]
    when = raised_at.strip() or "an unrecorded time"
    number = find_thread(request, repo, kind)
    if number is None:
        created = request(
            "POST",
            f"{API}/repos/{repo}/issues",
            {
                "title": f"Box alert: {title}",
                "body": (
                    f"{MARKER.format(kind=kind)}\n\n{body}\n\n"
                    f"_Raised on the trading box at {when} (kind `{kind}`). "
                    "Repeats of this kind are added below as comments; "
                    "close the issue once it is dealt with._"
                ),
                "labels": [ISSUE_LABEL],
            },
        )
        return f"opened {repo}#{created.get('number', '?') if isinstance(created, dict) else '?'}"
    request(
        "POST",
        f"{API}/repos/{repo}/issues/{number}/comments",
        {"body": f"**{title}**\n\n{body}\n\n_{when}_"},
    )
    return f"commented on {repo}#{number}"


def main(env: Mapping[str, str] | None = None, request: Request | None = None) -> int:
    env = os.environ if env is None else env
    token = env.get("GITHUB_TOKEN", "")
    if request is None:
        if not token:
            print("box_alert_issue: no GITHUB_TOKEN", file=sys.stderr)
            return 1
        request = _http_request(token)
    repo = env.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    try:
        print(
            file_alert(
                request,
                repo,
                env.get("ALERT_KIND", ""),
                env.get("ALERT_TITLE", ""),
                env.get("ALERT_BODY", ""),
                env.get("ALERT_RAISED_AT", ""),
            )
        )
    except Exception as exc:  # noqa: BLE001 — a red run is the last signal left
        print(f"box_alert_issue: could not write the issue ({exc})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
