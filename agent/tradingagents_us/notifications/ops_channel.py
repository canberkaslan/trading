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
    incidents on (`scripts/watchdog.py`). There is one open issue per alert
    *kind*, and a recurrence is a comment on it, so a failure that repeats
    nightly becomes one thread rather than a pile of issues.

The box does not write that issue itself. It dispatches the `box-alert`
workflow (`.github/workflows/box-alert.yml`), which writes it with the
workflow's own token, as github-actions[bot] (`scripts/box_alert_issue.py`).
The author is the point: the box's token is a fine-grained PAT, which on a
personally owned repo only the owner can mint, and GitHub does not notify you
about your own activity. An issue filed with it would be the owner's own issue,
and nobody would be emailed. The watchdog's issues notify because the bot
writes them; box alerts now take the same path.

The GitHub half needs a credential the box does not ship with:
`OPS_ALERT_GITHUB_TOKEN`, a fine-grained PAT with Actions read/write on the
alert repo only (dispatching is an Actions write; the issue permission belongs
to the workflow, not to the box). Actions write covers every dispatchable
workflow in that repo, so a dedicated private alert repo is the narrow choice.
Without the token that half reports "not configured". Preflight records that
gap, and `check_github_channel` also catches a token that is present but
refused or about to expire; /readyz serves the record, and the off-box watchdog
opens an incident on it (see `monitoring.alerting_state`).

The default repo is public, as the watchdog's incidents are. Text is scrubbed
before it leaves the box: credential-bearing URL parameters, bearer tokens, URL
userinfo and the literal value of every secret-looking environment variable.
Point `OPS_ALERT_GITHUB_REPO` at a private repo that carries the same workflow
and script to keep book details off a public page.

Nothing here raises. An alerter that can crash its caller turns one failure
into two, and the caller is usually already handling the first.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

import httpx

from tradingagents_us.log_redaction import redact

GITHUB_TOKEN_ENV = "OPS_ALERT_GITHUB_TOKEN"
GITHUB_REPO_ENV = "OPS_ALERT_GITHUB_REPO"
#: Where the off-box watchdog files its incidents, so both alert sources land
#: in the one place the owner already watches.
DEFAULT_GITHUB_REPO = "fusapp/trading"
GITHUB_API = "https://api.github.com"

#: The workflow that writes the issue as github-actions[bot], and the branch
#: it is dispatched on (a dispatched workflow must exist on that ref).
ALERT_WORKFLOW = "box-alert.yml"
ALERT_WORKFLOW_REF = "main"

#: An alert is on the failure path of whatever called it. Waiting longer than
#: this for GitHub delays the caller's own exit for no gain.
GITHUB_TIMEOUT_S = 10.0

#: Expo truncates on the device anyway; keep the push readable on a lock screen.
PUSH_BODY_LIMIT = 200

#: Dispatch inputs share a 65,535-character budget; the issue text is capped
#: well inside it so the title and the rest always fit.
GITHUB_TITLE_LIMIT = 200
GITHUB_BODY_LIMIT = 20_000

#: A token this close to its expiry is reported now, while it still works.
TOKEN_EXPIRY_WARN_DAYS = 7

#: Kinds become part of an HTML-comment marker in the issue body. Anything that
#: is not a plain token is sent as "ops" (the workflow applies the same rule).
_KIND_PATTERN = re.compile(r"^[a-z0-9_]{1,40}$")

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
        from tradingagents_us.notifications.sender import PushMessage, send_expo_push
        from tradingagents_us.storage import TradeLogRepository, make_engine
        from tradingagents_us.storage.device_tokens import list_all_tokens

        url = os.environ.get("TRADE_LOG_DB_URL", "sqlite:///./local.db")
        repo = TradeLogRepository(engine=make_engine(url))
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


def _alert_repo() -> str:
    return os.environ.get(GITHUB_REPO_ENV, "").strip() or DEFAULT_GITHUB_REPO


def _send_github(title: str, body: str, kind: str) -> ChannelResult:
    """Dispatch the box-alert workflow; it writes the issue as github-actions[bot]."""
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    if not token:
        return ChannelResult("github", False, f"not configured ({GITHUB_TOKEN_ENV} unset)")
    repo = _alert_repo()
    safe_kind = kind if _KIND_PATTERN.match(kind) else "ops"
    inputs = {
        "kind": safe_kind,
        "title": scrub(title)[:GITHUB_TITLE_LIMIT],
        "body": scrub(body)[:GITHUB_BODY_LIMIT],
        "raised_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
    try:
        with _github_client(token) as gh:
            r = gh.post(
                f"/repos/{repo}/actions/workflows/{ALERT_WORKFLOW}/dispatches",
                json={"ref": ALERT_WORKFLOW_REF, "inputs": inputs},
            )
            r.raise_for_status()
        return ChannelResult("github", True, f"dispatched {ALERT_WORKFLOW} on {repo} ({safe_kind})")
    except Exception as exc:  # noqa: BLE001 — never fail the caller over the alert
        return ChannelResult("github", False, f"failed: {scrub(str(exc))}")


@dataclass(frozen=True)
class ChannelCheck:
    """Whether a channel looks usable, without sending anything through it.

    `ok is None` means the check itself could not run (GitHub unreachable, a
    5xx): not evidence either way, and never reported as a broken channel.
    """

    ok: bool | None
    detail: str


def check_github_channel(now: datetime | None = None) -> ChannelCheck:
    """Exercise the GitHub half without sending anything. Never raises.

    Presence of the token proves nothing: a fine-grained PAT expires after 30
    days by default, and a revoked or expired one fails every dispatch with a
    401 that lands only in the journal. This reads the alert workflow with the
    token, which fails the way the dispatch would for an expired or revoked
    token, a token without access to the repo, or a repo that lacks the
    workflow. It cannot prove the Actions *write* permission: GitHub offers no
    way to read a fine-grained token's permissions without using them.
    """
    token = os.environ.get(GITHUB_TOKEN_ENV, "").strip()
    if not token:
        return ChannelCheck(False, f"{GITHUB_TOKEN_ENV} unset: box alerts reach only the app")
    repo = _alert_repo()
    try:
        with _github_client(token) as gh:
            r = gh.get(f"/repos/{repo}/actions/workflows/{ALERT_WORKFLOW}")
    except Exception as exc:  # noqa: BLE001
        return ChannelCheck(None, f"GitHub unreachable, token not checked: {scrub(str(exc))}")
    expiry = _token_expiry(r.headers.get("github-authentication-token-expiration", ""))
    expires = f", token expires {expiry.isoformat()}" if expiry else ""
    refusals = {401: "rejected (expired or revoked?)", 403: "lacks permission", 404: "cannot see"}
    if r.status_code in refusals:
        return ChannelCheck(
            False,
            f"{GITHUB_TOKEN_ENV} {refusals[r.status_code]}: HTTP {r.status_code} reading "
            f"{repo} {ALERT_WORKFLOW}{expires}",
        )
    if r.status_code >= 400:
        return ChannelCheck(None, f"GitHub answered HTTP {r.status_code}, token not checked")
    today = (now or datetime.now(UTC)).date()
    if expiry is not None and (expiry - today).days <= TOKEN_EXPIRY_WARN_DAYS:
        return ChannelCheck(False, f"{GITHUB_TOKEN_ENV} expires {expiry.isoformat()}: rotate it")
    return ChannelCheck(True, f"token accepted for {repo}{expires}")


def _token_expiry(raw: str) -> date | None:
    """The date part of GitHub's token-expiration header, if it sent one."""
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None
