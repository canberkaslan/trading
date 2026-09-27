"""Box-side ops alerts, delivered somewhere other than the phone.

Every alert this box raised used to be an Expo push to the device tokens in
local.db, which is one channel, and it runs through the mobile app. From
2026-09-14 a revoked broker key aborted every ticker of every nightly run;
preflight saw the 401 each evening and the run exited non-zero each night, and
both said so only to an app that was being rebuilt the whole time. Nearly two
weeks passed before anyone looked.

So an alert now goes to two independent places:

  * push: every registered device, as before.
  * a GitHub issue, on the channel the off-box watchdog already files its
    incidents on (`scripts/watchdog.py`). It needs no app, no registered device
    and no new service, and GitHub mails the repo owner. There is one open issue
    per alert *kind*, and a recurrence is a comment on it, so a failure that
    repeats nightly becomes one thread rather than a pile of issues.

The GitHub half needs a credential the box does not ship with:
`OPS_ALERT_GITHUB_TOKEN`, a fine-grained PAT with Issues read/write on the
target repo only. Without it that half reports "not configured", and preflight
names the gap as a failure on every run. Quietly degrading to push-only is the
failure this module exists to end, so it must not happen silently.

The default repo is public, as the watchdog's incidents are. Text is scrubbed
before it leaves the box: credential-bearing URL parameters, bearer tokens, URL
userinfo and the literal value of every secret-looking environment variable.
Point `OPS_ALERT_GITHUB_REPO` at a private repo to keep book details off a
public page.

Nothing here raises. An alerter that can crash its caller turns one failure
into two, and the caller is usually already handling the first.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from tradingagents_us.log_redaction import redact

GITHUB_TOKEN_ENV = "OPS_ALERT_GITHUB_TOKEN"
GITHUB_REPO_ENV = "OPS_ALERT_GITHUB_REPO"
#: Where the off-box watchdog files its incidents, so both alert sources land
#: in the one place the owner already watches.
DEFAULT_GITHUB_REPO = "canberkaslan/trading"
GITHUB_API = "https://api.github.com"

#: Deliberately not the watchdog's `watchdog` label. The watchdog closes the
#: open issue carrying its label once the box answers again, and a box alert
#: must never be closed by a probe that cannot see what the alert was about.
ISSUE_LABEL = "box-alert"

#: An alert is on the failure path of whatever called it. Waiting longer than
#: this for GitHub delays the caller's own exit for no gain.
GITHUB_TIMEOUT_S = 10.0

#: Expo truncates on the device anyway; keep the push readable on a lock screen.
PUSH_BODY_LIMIT = 200

#: GitHub rejects issue bodies over 65,536 characters.
GITHUB_BODY_LIMIT = 60_000

_KIND_MARKER = "<!-- box-alert-kind:{kind} -->"

# Environment variables whose values must never be published. HEALTHCHECK_URL
# is a capability: anyone holding it can report a run that never happened.
_SECRET_ENV_NAME = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|DSN|HEALTHCHECK_URL)$")
# Shorter values are too likely to occur by accident in ordinary text.
_MIN_SECRET_LEN = 8
_URL_USERINFO = re.compile(r"(://[^/\s:@]+:)[^@\s/]+@")
_MARK = "<redacted>"


@dataclass(frozen=True)
class ChannelResult:
    channel: str
    delivered: bool
    detail: str


@dataclass(frozen=True)
class Delivery:
    results: tuple[ChannelResult, ...]

    @property
    def delivered(self) -> bool:
        """True when at least one human-facing channel accepted the alert."""
        return any(r.delivered for r in self.results)

    def describe(self) -> str:
        return "; ".join(f"{r.channel}: {r.detail}" for r in self.results)


def send_ops_alert(title: str, body: str, kind: str = "ops") -> Delivery:
    """Deliver one alert to every configured channel. Never raises.

    `kind` groups repeats: every alert of one kind lands on the same open issue.
    """
    return Delivery((_send_push(title, body, kind), _send_github(title, body, kind)))


def scrub(text: str) -> str:
    """Strip anything credential-shaped before text leaves the box."""
    out = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{_MARK}@", redact(text))
    for name, raw in os.environ.items():
        value = raw.strip()
        if len(value) >= _MIN_SECRET_LEN and _SECRET_ENV_NAME.search(name.upper()):
            out = out.replace(value, _MARK)
    return out


def _send_push(title: str, body: str, kind: str) -> ChannelResult:
    try:
        from sqlalchemy import create_engine

        from tradingagents_us.notifications.sender import PushMessage, send_expo_push
        from tradingagents_us.storage import TradeLogRepository
        from tradingagents_us.storage.device_tokens import list_all_tokens

        url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
        repo = TradeLogRepository(engine=create_engine(url, future=True))
        with repo.session() as s:
            tokens = list_all_tokens(s)
        if not tokens:
            return ChannelResult("push", False, "no registered devices")
        resp = send_expo_push(
            [
                PushMessage(
                    to=t,
                    title=title,
                    body=body[:PUSH_BODY_LIMIT],
                    data={"type": "ops_alert", "kind": kind},
                )
                for t in tokens
            ]
        )
        if resp.get("disabled"):
            return ChannelResult("push", False, "PUSH_DISABLED=1")
        if resp.get("error"):
            return ChannelResult("push", False, f"expo error: {resp['error']}")
        return ChannelResult("push", True, f"sent to {len(tokens)} device(s)")
    except Exception as exc:  # noqa: BLE001 — the other channel must still get its turn
        return ChannelResult("push", False, f"failed: {exc}")


def _github_client(token: str, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """One place that builds the client, so a test can swap its transport."""
    return httpx.Client(
        base_url=GITHUB_API,
        timeout=GITHUB_TIMEOUT_S,
        transport=transport,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-trader-box-alert/1",
        },
    )


def _send_github(title: str, body: str, kind: str) -> ChannelResult:
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    if not token:
        return ChannelResult("github", False, f"not configured ({GITHUB_TOKEN_ENV} unset)")
    repo = os.environ.get(GITHUB_REPO_ENV, "").strip() or DEFAULT_GITHUB_REPO
    safe_title = scrub(title)
    safe_body = scrub(body)[:GITHUB_BODY_LIMIT]
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    try:
        with _github_client(token) as gh:
            number = _find_open_issue(gh, repo, kind)
            if number is None:
                r = gh.post(
                    f"/repos/{repo}/issues",
                    json={
                        "title": f"Box alert: {safe_title}",
                        "body": (
                            f"{_KIND_MARKER.format(kind=kind)}\n\n{safe_body}\n\n"
                            f"_Raised on the trading box at {stamp} (kind `{kind}`). "
                            "Repeats of this kind are added below as comments; "
                            "close the issue once it is dealt with._"
                        ),
                        "labels": [ISSUE_LABEL],
                    },
                )
                r.raise_for_status()
                return ChannelResult("github", True, f"opened {repo}#{r.json()['number']}")
            r = gh.post(
                f"/repos/{repo}/issues/{number}/comments",
                json={"body": f"**{safe_title}**\n\n{safe_body}\n\n_{stamp}_"},
            )
            r.raise_for_status()
            return ChannelResult("github", True, f"commented on {repo}#{number}")
    except Exception as exc:  # noqa: BLE001 — never fail the caller over the alert
        return ChannelResult("github", False, f"failed: {scrub(str(exc))}")


def _find_open_issue(gh: httpx.Client, repo: str, kind: str) -> int | None:
    """The open issue already carrying this kind, if any.

    Matched on the marker in the body rather than filtered by label: GitHub
    drops labels silently when the token cannot set them, and dedupe that
    depended on the label would then open a fresh issue every night.
    """
    r = gh.get(f"/repos/{repo}/issues", params={"state": "open", "per_page": 100})
    r.raise_for_status()
    marker = _KIND_MARKER.format(kind=kind)
    for issue in r.json():
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        if marker in str(issue.get("body") or ""):
            return int(issue["number"])
    return None
