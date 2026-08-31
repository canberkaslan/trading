"""Cadence for a watchdog that cannot trust the scheduler it runs on.

`.github/workflows/watchdog.yml` asks GitHub for 48 runs a day. Measured over
the outage that started 2026-08-24, it got:

    08-25  22/48      08-28   2/48      08-30   6/48
    08-26  18/48      08-29   6/48      08-31   1/48 (by 06:00 UTC)
    08-27   3/48

The largest hole was 13.9 hours, in the middle of a dark box. Moving the cron
off the `:00/:30` boundary to `7,37` on 08-28 changed nothing measurable — the
two days after it are 6/48 and 6/48 — so congestion at the top of the hour was
not the story. Scheduled runs are best-effort on GitHub's side, and at this
repo's weight "best effort" is delivering about an eighth of what is asked.

That is fatal to the one property the watchdog exists for. It runs off the box
precisely so a dead host cannot silence it; a cadence that can go half a day
without firing silences it anyway, just for a different reason.

The repair cannot be another cron, because the thing that is unreliable is cron.
The one trigger GitHub does honour on demand is `workflow_dispatch` — and it is
one of the two events explicitly exempted from the "GITHUB_TOKEN cannot trigger
a workflow" rule, so a run can dispatch its own successor with the built-in
token and no PAT. So the watchdog stops asking to be woken and instead stays
awake: a job probes on a fixed cadence for a window just under the six-hour
runner ceiling, then hands off to a fresh run of itself. The chain is its own
clock. Cron survives only as the way a *broken* chain gets restarted, which is
a rare event rather than the load-bearing path.

This module is the pure half: when the next probe is due, and whether a chain
is already alive. It reads no clock and makes no request — every instant and
every run is passed in — so the tests pin exact numbers instead of sleeping.

The cost is honest and worth naming: the relay occupies a runner continuously.
This repository is public, where Actions minutes are not metered, and the job
is a sleep loop rather than compute. On a private repo this design would be the
wrong trade and an external pinger would be the right one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

# One probe every half hour, matching what the cron was always meant to deliver.
DEFAULT_INTERVAL_S = 1800.0

# How long one link of the chain stays awake. GitHub kills a job on a hosted
# runner at six hours, and a job killed mid-flight never reaches its hand-off
# step — so the window has to end with enough room left to dispatch a successor
# and for that successor to start. Five hours ten minutes leaves ~50 minutes.
DEFAULT_BUDGET_S = 18600.0

# Refuse to be configured past the point where the hand-off is at risk, however
# large `WATCHDOG_RELAY_BUDGET_S` says. A window that outlives the runner turns
# the chain into a single link that dies quietly.
MAX_BUDGET_S = 19800.0

# A relay run is expected to finish within its budget plus the time GitHub takes
# to schedule and tear it down. Past that it is not "running", it is stuck — and
# a stuck run matters more than an absent one, because it holds the concurrency
# slot its replacement needs.
CHAIN_GRACE_S = 1200.0

# What the Actions API calls a run that has not finished. `waiting` is the
# environment-approval state; this workflow uses no environments, but a run
# parked there is still occupying the chain and must not be read as absent.
ACTIVE_RUN_STATUSES = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})


@dataclass(frozen=True)
class Tick:
    """A scheduled probe: which one it is, when it is due, how long until then."""

    index: int
    due_at: datetime
    sleep_s: float


@dataclass(frozen=True)
class RunSummary:
    """The three fields of an Actions run this module has an opinion about."""

    run_id: int
    status: str
    started_at: datetime | None

    def age_s(self, now: datetime) -> float | None:
        """Seconds since the run started, or None when GitHub gave no timestamp.

        None rather than 0.0 on purpose: "I do not know how old this is" and
        "it started this instant" lead to opposite conclusions about whether a
        run is stuck, and collapsing them would make the optimistic one the
        default — the same mistake the frozen incident body made.
        """
        if self.started_at is None:
            return None
        return (now - self.started_at).total_seconds()


@dataclass(frozen=True)
class ChainState:
    """Whether a relay chain is currently carrying the cadence, and how well."""

    alive: bool
    stalled: bool
    detail: str

    @property
    def needs_start(self) -> bool:
        """Should a fresh link be dispatched?

        Yes when nothing is running, and *also* yes when something is running
        but has outlived its budget. The second case looks like over-reaction
        until you notice the concurrency group: the replacement queues behind
        the stuck run and takes over the moment GitHub's timeout kills it,
        instead of the chain waiting for a cron that arrives an eighth of the
        time.
        """
        return (not self.alive) or self.stalled


def next_tick(
    started_at: datetime,
    now: datetime,
    interval_s: float = DEFAULT_INTERVAL_S,
    budget_s: float = DEFAULT_BUDGET_S,
) -> Tick | None:
    """The next probe due after `now`, or None when the window is spent.

    Ticks are anchored to `started_at + k * interval`, never to "when the last
    probe happened to finish". The distinction is the whole point: a probe that
    takes 40 seconds, repeated across a five-hour window, walks a sleep-based
    cadence minutes off the schedule it claims to keep — and this is a module
    whose entire job is to make a claimed cadence true. An overrun that swallows
    a tick loses that probe rather than shifting every later one.

    Index 0 is the probe the caller makes at launch, before it ever waits, so
    the answer here is always index 1 or later.
    """
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")

    elapsed = (now - started_at).total_seconds()
    index = int(elapsed // interval_s) + 1 if elapsed >= 0 else 1
    offset = index * interval_s
    if offset > budget_s:
        return None

    due_at = started_at + timedelta(seconds=offset)
    return Tick(index=index, due_at=due_at, sleep_s=max(0.0, (due_at - now).total_seconds()))


def planned_probes(
    interval_s: float = DEFAULT_INTERVAL_S, budget_s: float = DEFAULT_BUDGET_S
) -> int:
    """How many probes one link makes, launch probe included. For the log line."""
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    return int(budget_s // interval_s) + 1


def chain_state(
    runs: Iterable[RunSummary],
    now: datetime,
    budget_s: float = DEFAULT_BUDGET_S,
    grace_s: float = CHAIN_GRACE_S,
    exclude_ids: Sequence[int] = (),
) -> ChainState:
    """Read the relay workflow's runs as one answer: is the cadence being carried?

    `exclude_ids` exists for the hand-off. A run asking this question about
    itself would always find itself alive and conclude the chain is healthy —
    which is true, right up until it exits a second later.

    A run with no start timestamp is counted as alive but never as stalled: the
    only way to call something stuck is to know how long it has been running,
    and guessing in the direction of "kill it" would let a missing field cancel
    a perfectly healthy chain.
    """
    active = [r for r in runs if r.status in ACTIVE_RUN_STATUSES and r.run_id not in exclude_ids]
    if not active:
        return ChainState(alive=False, stalled=False, detail="no relay run is queued or running")

    limit = budget_s + grace_s
    overdue = [r for r in active if (age := r.age_s(now)) is not None and age > limit]
    if overdue:
        worst = max(overdue, key=lambda r: r.age_s(now) or 0.0)
        age_h = (worst.age_s(now) or 0.0) / 3600.0
        return ChainState(
            alive=True,
            stalled=True,
            detail=(
                f"relay run {worst.run_id} has been {worst.status} for {age_h:.1f}h, "
                f"past its {limit / 3600.0:.1f}h limit — queueing a replacement behind it"
            ),
        )

    newest = max(active, key=lambda r: r.age_s(now) if r.age_s(now) is not None else -1.0)
    age = newest.age_s(now)
    when = "age unknown" if age is None else f"{age / 60.0:.0f} min in"
    return ChainState(
        alive=True,
        stalled=False,
        detail=(
            f"relay run {newest.run_id} is {newest.status} ({when}); "
            "chain is carrying the cadence"
        ),
    )


def parse_seconds(raw: str | None, default: float, maximum: float | None = None) -> float:
    """An env-supplied duration, or the default — never an exception.

    This is the boundary between configuration and the one alerter that has to
    keep working when everything else is broken. A typo in
    `WATCHDOG_RELAY_INTERVAL_S` must cost the default cadence, not the watchdog.
    """
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if value <= 0:
        return default
    return min(value, maximum) if maximum is not None else value
