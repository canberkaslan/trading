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

Two independent signals, because "unreachable" has more than one cause:

  * **The public health endpoint** — an HTTPS GET that travels the user's path:
    Cloudflare edge -> tunnel -> cloudflared on the box -> uvicorn.
  * **The backup repo's newest commit** — the 02:15 UTC backup timer pushes to
    GitHub over plain outbound HTTPS, which shares *nothing* with the tunnel
    path but the host itself.

Read together they separate the two outages that look identical from a browser.
Tunnel down but backups landing = the host is alive and the edge is broken (a
`systemctl restart cloudflared` fixes it). Both silent = the host is gone (only
a console or a power cycle fixes it). Telling those apart before waking somebody
is most of the value; being paged for "the site is down" at 3am and finding a
dead tunnel is how people learn to mute the pager.

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
STATE_EDGE_DOWN = "edge_down"
STATE_DEGRADED = "degraded"
STATE_UP = "up"

_SEVERITY = {STATE_DARK: 3, STATE_EDGE_DOWN: 2, STATE_DEGRADED: 1, STATE_UP: 0}


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


def classify(health: HealthProbe, backup: BackupSignal) -> Verdict:
    """Turn the two outside signals into one verdict.

    The truth table is small enough to read in full, which is the point — the
    on-call reading the alert at 3am should be able to reconstruct why it fired
    from the alert alone.
    """
    health_line = f"Health endpoint: {health.describe()}"
    backup_line = f"Last off-box backup: {backup.describe()}"

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
        return Verdict(
            state=STATE_UP,
            headline="Box reachable",
            reasons=(health_line, backup_line),
            remedy="Nothing to do.",
        )

    if backup.stale:
        # Two paths that share only the host, both silent. This is the one that
        # earns a page: nothing on the machine can fix a machine that is gone.
        return Verdict(
            state=STATE_DARK,
            headline="Box is dark — the trader is not running",
            reasons=(
                health_line,
                backup_line,
                "The health probe and the backup push share no infrastructure except the "
                "host itself, so both going quiet points at the host, not the tunnel.",
                "While the box is dark nothing rebalances, no stop is re-armed and no "
                "alert can be raised from on-box — the open book is unattended.",
            ),
            remedy=(
                "Check the Hetzner console for the host; if it is up, `ssh agentmesh` and "
                "read `journalctl -b -1 -e`. If it is not, reboot from the console and "
                "verify `ai-trader.timer`, `ai-trader-api.service` and `cloudflared` come back."
            ),
        )

    if not backup.known:
        # One signal down, the other unavailable. Say so plainly rather than
        # guessing: "I could not tell" is a fact the on-call can act on, while a
        # confident wrong state costs them the trip.
        return Verdict(
            state=STATE_EDGE_DOWN,
            headline="Box unreachable over the tunnel (backup signal unavailable)",
            reasons=(
                health_line,
                backup_line,
                "Only one of the two signals could be read, so this cannot yet be told "
                "apart from a dead host — treat it as unreachable until the next run.",
            ),
            remedy=(
                "Retry `curl -sS https://trader.fusapp.com/healthz`; if it stays down, "
                "`ssh agentmesh` and check `cloudflared` before assuming the host is gone."
            ),
        )

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
