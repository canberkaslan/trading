"""Is the box still there — decided from outside the box.

Every alerting path this agent has runs *on* the Hetzner host: the daily run's
non-zero exit, `OnFailure=` on the systemd units, `scripts/inert_alert`, the
healthchecks.io dead-man ping. All of them share one failure mode, and on
2026-08-24 that failure mode happened: the host stopped answering SSH, ICMP and
the Cloudflare tunnel, and not one alert fired, because the thing that would
have sent the alert died with everything else. A watchdog that lives on the
machine it watches is a watchdog that reports "healthy" right up until it can't
report anything, and then reports nothing at all.

So this module is pure and stdlib-only on purpose: it is meant to be evaluated
somewhere the host has no vote — a GitHub Actions runner (see
`.github/workflows/watchdog.yml`). It takes what an outside observer can see and
decides what to say about it. No DB, no clock, no network.

Three independent signals, because "unreachable" has more than one cause:

  * **The public health endpoint** — an HTTPS GET that travels the user's path:
    Cloudflare edge -> tunnel -> cloudflared on the box -> uvicorn.
  * **The backup repo's newest commit** — the 02:15 UTC backup timer pushes to
    GitHub over plain outbound HTTPS, which shares *nothing* with the tunnel
    path but the host itself.
  * **A direct TCP connect to the origin** — no Cloudflare, no GitHub, no
    userspace on the box beyond its kernel accepting (or refusing) a socket.

Read together they separate the outages that look identical from a browser.
Tunnel down but backups landing = the host is alive and the edge is broken (a
`systemctl restart cloudflared` fixes it). Everything silent = the host is gone
(only a console or a power cycle fixes it). Telling those apart before waking
somebody is most of the value; being paged for "the site is down" at 3am and
finding a dead tunnel is how people learn to mute the pager.

The third signal exists because of what the first real incident did: from
2026-08-25 07:24 the backup repo could not be read (it is private, and the
workflow's built-in token cannot see across repositories), so the watchdog sat
at "cannot tell a dead tunnel from a dead host" for over a day while the host
was in fact dark. One unreadable signal should not blind the whole verdict. A
TCP probe needs no credential of any kind and can *positively* prove the host is
alive — the one thing the other two cannot do once either goes quiet.

What it deliberately cannot do is prove the opposite. A firewall that drops
rather than rejects is indistinguishable from an unplugged machine, so silence
on this probe only ever corroborates; it never promotes a verdict to `dark` on
its own. The asymmetry is the design, not a shortcut.

Cloudflare's own error pages (530, 1033, 502…) are explicitly *not* the origin
answering. They are the edge telling us it could not reach the tunnel, so they
count as "not reached" — reading a 5xx as "the API replied, just unhappily"
would turn the loudest possible symptom into a shrug.
"""

from __future__ import annotations

from dataclasses import dataclass

# The backup timer fires once a day at 02:15 UTC. Anything under a day plus a
# little is a normal gap, not news: systemd timers jitter, a slow sqlite
# snapshot and git push can add minutes, and a watchdog that pages because a
# timer ran at 02:41 instead of 02:15 is a watchdog that gets turned off.
BACKUP_INTERVAL_HOURS = 24.0
BACKUP_GRACE_HOURS = 2.0
BACKUP_STALE_AFTER_HOURS = BACKUP_INTERVAL_HOURS + BACKUP_GRACE_HOURS

# States, worst-first. Ordering matters: the runner uses it to decide whether a
# new observation is an escalation of an open incident or just more of the same.
STATE_DARK = "dark"
STATE_WEDGED = "wedged"
STATE_EDGE_DOWN = "edge_down"
STATE_DEGRADED = "degraded"
STATE_UP = "up"

# `wedged` sits just under `dark`: the kernel answers but nothing it is supposed
# to run does. The book is equally unattended either way, so it is nearly as bad
# — but the first move differs (ssh may still work), and sending somebody to a
# datacentre console when a shell would have done is how a remedy stops being
# read. Numbers are spaced so a future state can land between two of these
# without renumbering the rest.
_SEVERITY = {STATE_DARK: 40, STATE_WEDGED: 30, STATE_EDGE_DOWN: 20, STATE_DEGRADED: 10, STATE_UP: 0}


def severity(state: str) -> int:
    """How bad `state` is, for comparing two observations. Unknown states sort worst.

    An unrecognised state means this module grew a case the caller has not been
    taught yet; treating that as "probably fine" is the one reading that can
    lose an outage, so it sorts above `dark` instead.
    """
    return _SEVERITY.get(state, 99)


@dataclass(frozen=True)
class HealthProbe:
    """The result of asking the public endpoint whether it is alive.

    `reached_origin` is deliberately separate from `status`: a 530 and a DNS
    failure are both "the app did not answer", and collapsing them into a status
    code loses the distinction between "no answer" and "an answer we didn't
    like". `error` carries the transport-level reason when there is no status at
    all (timeout, TLS, DNS), so the incident text can say which.
    """

    reached_origin: bool
    status: int | None = None
    error: str | None = None

    def describe(self) -> str:
        if self.reached_origin:
            return f"HTTP {self.status}"
        if self.status is not None:
            return f"HTTP {self.status} (Cloudflare edge — tunnel not reached)"
        return f"no response ({self.error or 'unknown error'})"


@dataclass(frozen=True)
class BackupSignal:
    """How long ago the box last pushed a backup, as seen from GitHub.

    `age_hours is None` means we could not ask (GitHub API down, rate limit, a
    bad token) — which is emphatically not the same as "no backup for a long
    time". Conflating the two would let a GitHub hiccup declare the host dead,
    and an alerting system that invents outages is worse than one that misses
    them, because the next real one gets ignored.
    """

    age_hours: float | None
    newest_commit_utc: str | None = None
    error: str | None = None

    @property
    def known(self) -> bool:
        return self.age_hours is not None

    @property
    def stale(self) -> bool:
        return self.age_hours is not None and self.age_hours > BACKUP_STALE_AFTER_HOURS

    def describe(self) -> str:
        if not self.known:
            return f"unknown ({self.error or 'not checked'})"
        assert self.age_hours is not None
        when = f" (newest: {self.newest_commit_utc})" if self.newest_commit_utc else ""
        return f"{self.age_hours:.1f}h ago{when}"


@dataclass(frozen=True)
class HostProbe:
    """Did the origin host's own IP stack answer a TCP connect, bypassing everything.

    Three states, and the difference between the last two is the whole point:

    * `configured=False` — no origin address was supplied, so this signal was
      never taken. Absent, not negative.
    * `answered=True` — the connect was accepted *or* actively refused. Both mean
      a kernel formed a reply, which is proof the machine is up and networked.
      A refusal is as good as an accept here; we are asking about the host, not
      about whatever was or was not listening on the port.
    * `answered=False` — timeout or unreachable. Suggestive, never conclusive: a
      firewall configured to drop looks exactly like an unplugged machine from
      the outside, so this must not be read as "the host is dead".

    `detail` is a fixed category ("connection accepted", "no answer"), never an
    exception string and never an address. The origin address is deliberately
    absent from this object: it is supplied to the watchdog as a secret because
    publishing the IP behind Cloudflare would let anyone skip the edge entirely,
    and a verdict that gets pasted into a public incident issue is the last place
    it should be able to surface. Keeping it out of the dataclass means no future
    edit to the incident text can leak it by accident.
    """

    configured: bool
    answered: bool = False
    detail: str | None = None

    def describe(self) -> str:
        """Reads as the tail of "Direct TCP probe to the origin: …", so it does not
        repeat the words the caller already wrote."""
        if not self.configured:
            return "not checked (no origin address configured)"
        if self.answered:
            return f"answered ({self.detail or 'reachable'})"
        return f"no answer ({self.detail or 'unreachable'})"


@dataclass(frozen=True)
class Verdict:
    """What to say, and how loudly."""

    state: str
    headline: str
    reasons: tuple[str, ...]
    remedy: str

    @property
    def is_incident(self) -> bool:
        return self.state != STATE_UP

    def body(self) -> str:
        """Incident text for a GitHub issue.

        The repo is public, so this carries liveness facts only — never equity,
        positions, account ids or anything read out of the broker. A watchdog
        that leaks the book in order to report that the book is unreachable has
        made the outage worse than it was.
        """
        lines = [self.headline, ""]
        lines += [f"- {reason}" for reason in self.reasons]
        lines += ["", f"**Next step:** {self.remedy}"]
        return "\n".join(lines)


def _unreadable_backup(
    health_line: str, backup_line: str, host_line: str, host: HostProbe
) -> Verdict:
    """The health probe is down and the backup signal could not be read at all.

    This is the branch the live watchdog spent 2026-08-25 through 2026-08-26 in,
    repeating "I cannot tell" every thirty minutes while the host was dark. The
    TCP probe exists to give this case an answer; when it has none to give, the
    text at least says which piece of setup would have provided one.
    """
    if host.answered:
        # A credential-free probe settles what the missing credential could not.
        return Verdict(
            state=STATE_EDGE_DOWN,
            headline="Tunnel or API down, but the host is alive",
            reasons=(
                health_line,
                backup_line,
                host_line,
                "The backup signal could not be read, but the host answered a direct TCP "
                "probe that never touches Cloudflare — so the machine is up and the break "
                "is in cloudflared or the API process.",
            ),
            remedy=(
                "ssh agentmesh, then `systemctl status cloudflared ai-trader-api.service` "
                "and restart whichever is down."
            ),
        )

    # One signal down, the others unavailable or inconclusive. Say so plainly
    # rather than guessing: "I could not tell" is a fact the on-call can act on,
    # while a confident wrong state costs them the trip.
    if host.configured:
        leaning = (
            f"{host_line} — so two paths that share nothing are both quiet. That leans "
            "toward a dead host, but a dropped packet and an unplugged machine look "
            "identical from here, so it is not called."
        )
        remedy = (
            "Treat as probably dark: check the Hetzner console first, and keep "
            "`curl -sS https://trader.fusapp.com/healthz` as the recovery check."
        )
    else:
        leaning = (
            "No third signal is configured, so this cannot be settled from outside at "
            "all. Setting either WATCHDOG_BACKUP_TOKEN or WATCHDOG_HOST would decide "
            "this case instead of leaving it open every 30 minutes."
        )
        remedy = (
            "Retry `curl -sS https://trader.fusapp.com/healthz`; if it stays down, "
            "`ssh agentmesh` and check `cloudflared` before assuming the host is gone."
        )

    return Verdict(
        state=STATE_EDGE_DOWN,
        headline="Box unreachable over the tunnel (backup signal unavailable)",
        reasons=(
            health_line,
            backup_line,
            "Only one of the two primary signals could be read, so this cannot yet be "
            "told apart from a dead host — treat it as unreachable until the next run.",
            leaning,
        ),
        remedy=remedy,
    )


def classify(
    health: HealthProbe,
    backup: BackupSignal,
    host: HostProbe | None = None,
) -> Verdict:
    """Turn the outside signals into one verdict.

    The truth table is small enough to read in full, which is the point — the
    on-call reading the alert at 3am should be able to reconstruct why it fired
    from the alert alone.

    `host` defaults to "not configured" so that a deployment with no origin
    address behaves exactly as this module did before the probe existed. It only
    ever moves a verdict in two directions: it can prove the machine is up (which
    turns a guess into an instruction), and its silence can add weight to a case
    already made by the other two. It cannot declare an outage by itself.
    """
    host = host or HostProbe(configured=False)
    health_line = f"Health endpoint: {health.describe()}"
    backup_line = f"Last off-box backup: {backup.describe()}"
    host_line = f"Direct TCP probe to the origin: {host.describe()}"

    if health.reached_origin:
        if backup.stale:
            # The API answering proves the host is up and networked, so a missed
            # backup is not an outage — it is a broken timer, a full disk, or an
            # expired deploy key. Quiet, but it is exactly the failure that makes
            # the *next* real outage unrecoverable, so it still gets said.
            return Verdict(
                state=STATE_DEGRADED,
                headline="Box reachable, but the off-box backup has stopped",
                reasons=(
                    health_line,
                    backup_line,
                    "The host is alive, so this is the backup path failing on its own: "
                    "check `ai-trader-backup.timer`, disk space, and the deploy key.",
                ),
                remedy=(
                    "ssh agentmesh, then "
                    "`systemctl status ai-trader-backup.timer ai-trader-backup.service` "
                    "and `journalctl -u ai-trader-backup -n 50`."
                ),
            )
        # No host line in this branch or the one above: the API answering is
        # already proof the machine is up, and a second line saying so is noise
        # in the only alert somebody reads half-awake.
        return Verdict(
            state=STATE_UP,
            headline="Box reachable",
            reasons=(health_line, backup_line),
            remedy="Nothing to do.",
        )

    if backup.stale:
        if host.answered:
            # Kernel up, both userspace paths dead. Not a dead machine and not a
            # dead tunnel — a wedged host: full disk, OOM, a failed boot that got
            # as far as the network. Worth nearly as much alarm as `dark`, but
            # the first move is a shell, not a datacentre console.
            return Verdict(
                state=STATE_WEDGED,
                headline="Host answers, but nothing it runs does",
                reasons=(
                    health_line,
                    backup_line,
                    host_line,
                    "The tunnel and the backup push share no infrastructure except the host, "
                    "and both are silent while the host itself still answers TCP — so the "
                    "machine is up and its services are not.",
                    "Nothing rebalances and no stop is re-armed while this holds — the open "
                    "book is unattended, exactly as if the box were gone.",
                ),
                remedy=(
                    "ssh agentmesh (it may still work — the host is up). Check `df -h` and "
                    "`journalctl -b -e` first: a full disk or an OOM kill takes the timer, the "
                    "API and cloudflared down together. Console only if ssh also refuses."
                ),
            )

        # Every path silent. This is the one that earns a page: nothing on the
        # machine can fix a machine that is gone.
        reasons = [
            health_line,
            backup_line,
            "The health probe and the backup push share no infrastructure except the "
            "host itself, so both going quiet points at the host, not the tunnel.",
        ]
        if host.configured:
            reasons.append(
                f"{host_line} — a third path, sharing nothing with the other two, is also "
                "silent. Consistent with a dead host, though a firewall that drops would "
                "look the same."
            )
        reasons.append(
            "While the box is dark nothing rebalances, no stop is re-armed and no "
            "alert can be raised from on-box — the open book is unattended."
        )
        return Verdict(
            state=STATE_DARK,
            headline="Box is dark — the trader is not running",
            reasons=tuple(reasons),
            remedy=(
                "Check the Hetzner console for the host; if it is up, `ssh agentmesh` and "
                "read `journalctl -b -1 -e`. If it is not, reboot from the console and "
                "verify `ai-trader.timer`, `ai-trader-api.service` and `cloudflared` come back."
            ),
        )

    if not backup.known:
        return _unreadable_backup(health_line, backup_line, host_line, host)

    return Verdict(
        state=STATE_EDGE_DOWN,
        headline="Tunnel or API down, but the host is alive",
        reasons=(
            health_line,
            backup_line,
            "A backup landed inside the expected window, so the host has outbound network "
            "and a working timer — the break is in cloudflared or the API process.",
        ),
        remedy=(
            "ssh agentmesh, then `systemctl status cloudflared ai-trader-api.service` and "
            "restart whichever is down."
        ),
    )
