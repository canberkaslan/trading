"""How old is the incident, and how old is the reading — said out loud.

An incident issue is the watchdog's only memory and its only voice. On
2026-08-25 the watchdog opened issue #1 and then, correctly, stayed quiet: the
state never changed, and a dark host that comments on every run posts 48 times a
day about one outage. But "quiet" was implemented as "write nothing at all", so
four days later the issue body still said `_First seen: 2026-08-25 07:24 UTC_`
and nothing else about time. `updated_at` equalled `created_at`.

That leaves a reader unable to answer the question they actually have. A body
that has not moved in four days is produced by two very different worlds:

  * the watchdog is running fine and has simply had no new news, or
  * the watchdog stopped running on 2026-08-25 too, and the last thing it ever
    said is still sitting there looking current.

Nothing on the page distinguishes them, so the default reading is the optimistic
one, which is the reading that loses outages. And it is not hypothetical here:
the 2026-08-28 measurement found the `*/30` cron actually delivering a **55
minute median** gap with a third of intervals over an hour, so even a healthy
watchdog's verdict is routinely much staler than its schedule advertises.

The fix is not more alerting. It is to write the two timestamps that make
staleness visible — when the incident started, and when it was last confirmed —
and to refresh the second on *every* check, including the quiet ones. Editing an
issue body sends no notification, so this costs the reader nothing and turns a
frozen page into a signal: if the "last checked" stamp stops advancing, the
watchdog itself is what died.

Pure and stdlib-only, like `liveness`: every instant is passed in. This module
never reads a clock, so its output is a function of its arguments and the tests
can pin exact strings.
"""

from __future__ import annotations

from datetime import datetime

# What `.github/workflows/watchdog.yml` asks the scheduler for. Used only to
# describe the gap in words — never to decide anything. GitHub's cron is
# best-effort and demonstrably runs late, which is the fact this text exists to
# expose rather than to enforce.
NOMINAL_CHECK_INTERVAL_MIN = 30.0

# How far past nominal a gap has to be before the text stops treating it as
# ordinary jitter and warns the reader that the facts may be older than the
# schedule implies. Two intervals: one missed run is unremarkable, two means the
# reading in front of you could be over an hour old.
LATE_CHECK_FACTOR = 2.0

_STAMP_FMT = "%Y-%m-%d %H:%M UTC"


def format_stamp(when: datetime) -> str:
    """An absolute UTC timestamp — the only thing that stays true after it is written.

    Everything else here is a duration computed against `now`, which is correct
    at the moment of writing and wrong every moment after. The absolute stamp is
    what a reader three days later can actually work from, so it always appears
    alongside the friendlier relative phrasing rather than being replaced by it.
    """
    return when.strftime(_STAMP_FMT)


def humanize_duration(seconds: float) -> str:
    """A short, honest span: `48s`, `67 min`, `4.5h`, `4d 5h`.

    Negative spans are clamped to zero rather than rendered. A negative age can
    only come from clock skew between the runner and GitHub, and "checked -3
    minutes ago" reads as a bug in the alerter — which costs it the reader's
    trust at precisely the moment it is trying to report an outage.
    """
    seconds = max(0.0, seconds)
    minutes = seconds / 60.0
    if minutes < 1.0:
        return f"{int(seconds)}s"
    if minutes < 90.0:
        return f"{round(minutes)} min"
    hours = minutes / 60.0
    if hours < 48.0:
        return f"{hours:.1f}h"
    days, rem_hours = divmod(round(hours), 24)
    return f"{int(days)}d {int(rem_hours)}h"


def _first_seen_line(now: datetime, first_seen: datetime | None) -> str:
    if first_seen is None:
        # An issue filed by hand, or one opened before this marker existed. Say
        # so instead of substituting `now`, which would silently reset the age of
        # an outage every time it was read.
        return "_First seen: not recorded on this issue._"
    age = humanize_duration((now - first_seen).total_seconds())
    return f"_First seen {format_stamp(first_seen)} — open {age}._"


def _last_checked_line(
    now: datetime, previous_check: datetime | None, nominal_interval_min: float
) -> str:
    head = f"_Last checked {format_stamp(now)}"
    if previous_check is None:
        return (
            f"{head}. No previous check is recorded on this issue, so there is no "
            "measured cadence yet._"
        )

    gap_min = max(0.0, (now - previous_check).total_seconds()) / 60.0
    gap = humanize_duration(gap_min * 60.0)
    if gap_min > nominal_interval_min * LATE_CHECK_FACTOR:
        return (
            f"{head}; the previous check was {gap} earlier, against a schedule of every "
            f"{nominal_interval_min:.0f} min. GitHub's cron runs late, so the facts above "
            "can be considerably older than the schedule suggests._"
        )
    return (
        f"{head}; previous check {gap} earlier "
        f"(schedule: every {nominal_interval_min:.0f} min)._"
    )


def render_timeline(
    now: datetime,
    first_seen: datetime | None,
    previous_check: datetime | None,
    nominal_interval_min: float = NOMINAL_CHECK_INTERVAL_MIN,
) -> str:
    """The time footer for an incident body: when it started, when it was last confirmed.

    The third line is the one that does the work. It tells the reader that the
    second line advances on every check even when nothing is said — which makes
    a stopped watchdog visible as a stopped clock, from the same page they were
    already looking at. Without it, "last checked" is just another number that
    could mean anything, and the reader has no reason to keep watching it.
    """
    return "\n".join(
        [
            "---",
            _first_seen_line(now, first_seen),
            _last_checked_line(now, previous_check, nominal_interval_min),
            "_The line above is rewritten on every check, comment or not. If it stops "
            "advancing, the watchdog has stopped and nothing on this page is current._",
        ]
    )


def render_recovery(now: datetime, first_seen: datetime | None) -> str:
    """Opening line of the recovery comment, including how long the outage ran.

    The duration is the number anybody asks first afterwards, and it is only
    knowable while the issue is still open — once it is closed the start time
    stops being in front of anyone. Writing it into the closing comment is the
    cheapest possible incident record.
    """
    if first_seen is None:
        return f"✅ Recovered at {format_stamp(now)}."
    lasted = humanize_duration((now - first_seen).total_seconds())
    return f"✅ Recovered at {format_stamp(now)}, after {lasted} down."
