"""Who is followed, and for how long anything is kept.

Identities are pinned to ids that cannot be re-registered, never to handles.
The commentator's old X handle, @BoraOzkent, now belongs to someone else, and
two of his former YouTube handles return 404 and could be claimed by anyone.
A handle is a name that can change hands; a channel id or a numeric user id
is the account itself.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

log = logging.getLogger(__name__)

#: Shown in the prompt header. A description, not an endorsement.
COMMENTATOR_NAME = "Bora Özkent"
COMMENTATOR_DESCRIPTION = (
    'Turkish US-equities commentator; self-declared "not investment advice"; opinion'
)

#: YouTube channel id of @boraozkentlenasdaq. Channel ids are permanent.
YOUTUBE_CHANNEL_ID = "UCrXj09uA0Nqv65st774NEKw"

#: X numeric user id of @BoraOzkentNSDQ. It is None until someone pins it.
#:
#: It has to be resolved once, by a person, with
#:     python -m scripts.commentator_fetch --resolve-x-id BoraOzkentNSDQ
#: (one GET /2/users/by/username call, about $0.01), checked against the
#: profile, and then written here or set as COMMENTATOR_X_USER_ID. The code
#: never resolves it at runtime: resolving by handle on every run would follow
#: the handle to whoever holds it next, which is exactly how the old
#: @BoraOzkent would have been followed to a stranger. While it is None the X
#: source does nothing, and says so in the log.
X_USER_ID: str | None = None

#: The handle the pinned id is expected to carry. Used only to notice a
#: rename, which is logged; it is never used to look anything up.
X_EXPECTED_USERNAME = "BoraOzkentNSDQ"

#: YouTube API Developer Policies III.E.4: API data that is not authorised
#: user data may be kept at most thirty days, then refreshed or deleted.
YOUTUBE_MAX_RETENTION = timedelta(days=30)

#: The retention pass runs once a day (ai-trader-commentator-retention.timer,
#: weekends included, whatever COMMENTATOR_FEED says), so an item can sit up
#: to this long past its deadline before the pass deletes it.
RETENTION_PASS_INTERVAL = timedelta(days=1)

#: The deadline stored on a YouTube item: one pass interval inside the policy
#: limit, so the daily pass has deleted it by day thirty. Reads skip an item
#: past its deadline whenever the pass runs.
YOUTUBE_RETENTION = YOUTUBE_MAX_RETENTION - RETENTION_PASS_INTERVAL

#: The X fetch stores new posts only when the daily retention pass ran within
#: this long. That pass is what checks X deletions on weekends and while the
#: kill switch holds the trading run, so without it a post deleted on Friday
#: evening would stay stored until Monday. A day plus slack for a late timer.
RETENTION_PASS_MAX_AGE = timedelta(hours=26)

#: X items are also purged on deletion (checked every run). The window is kept
#: short because the deletion check bills per post still stored: at ~2-3 posts
#: a day, eight days is ~20 lookups a run. The sentiment window is seven days,
#: so nothing the analyst can use is lost.
_X_RETENTION_DAYS_DEFAULT = 8

#: How far back a routine fetch ingests. Shorter than YOUTUBE_RETENTION on
#: purpose: an item that expired is older than this, so the next fetch does
#: not re-ingest (and re-pay to extract) what retention just deleted.
_INGEST_LOOKBACK_DAYS_DEFAULT = 14

#: First X fetch with nothing stored: how many recent posts to buy.
X_FIRST_RUN_MAX = 20

#: ADR-009 "Enabling YouTube — preconditions". The YouTube source turns API
#: Data (title, description, chapters) into derived fields — tickers, a stance
#: per ticker, a paraphrase — and the Developer Policies (III.E.4) prohibit
#: creating derived data from API Data. Nobody has cleared that. So a key is
#: not enough to start it: an operator exporting one for the measurement
#: gate's `--ignore-flag` backfill would create exactly that data. The key is
#: used only beside this separate acknowledgement, which records that the
#: preconditions were met in writing. Only exactly "1".
YOUTUBE_CLEARED_ENV = "COMMENTATOR_YOUTUBE_CLEARED"


def is_enabled() -> bool:
    """The feed's master switch. Off unless exactly "1"."""
    return os.environ.get("COMMENTATOR_FEED") == "1"


def _positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default
    if value <= 0:
        log.warning("%s=%r must be positive; using %d", name, raw, default)
        return default
    return value


def x_retention() -> timedelta:
    return timedelta(
        days=_positive_int_env("COMMENTATOR_X_RETENTION_DAYS", _X_RETENTION_DAYS_DEFAULT)
    )


def ingest_lookback() -> timedelta:
    return timedelta(
        days=_positive_int_env("COMMENTATOR_LOOKBACK_DAYS", _INGEST_LOOKBACK_DAYS_DEFAULT)
    )


def x_user_id() -> str | None:
    """The pinned numeric X user id, or None when none is pinned.

    COMMENTATOR_X_USER_ID overrides the constant so an operator can pin it on
    the box without a deploy. Anything that is not all digits is refused: a
    handle pasted there by mistake must not become a lookup by handle.
    """
    raw = (os.environ.get("COMMENTATOR_X_USER_ID") or X_USER_ID or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        log.warning(
            "commentator X user id %r is not numeric; refusing it — pin the numeric id, "
            "never a handle",
            raw,
        )
        return None
    return raw


def _secret(name: str) -> str | None:
    value = (os.environ.get(name) or "").strip()
    # `.env.example` ships `NAME=...`; a copy of it is a placeholder, not a key.
    return value if value and value != "..." else None


def youtube_cleared() -> bool:
    """Whether the ADR-009 YouTube preconditions are recorded as met."""
    return os.environ.get(YOUTUBE_CLEARED_ENV) == "1"


def youtube_api_key() -> str | None:
    """The YouTube key, or None: unset, a placeholder, or the source not cleared.

    A key without the acknowledgement is refused with a warning, and the
    YouTube source is skipped exactly as if no key were set.
    """
    key = _secret("YOUTUBE_API_KEY")
    if key is not None and not youtube_cleared():
        log.warning(
            "YOUTUBE_API_KEY is set but %s is not 1; the YouTube source stays off until "
            "the ADR-009 preconditions are met (derived data from YouTube API Data)",
            YOUTUBE_CLEARED_ENV,
        )
        return None
    return key


def youtube_skip_reason() -> str:
    """Why `youtube_api_key()` gave nothing, for the run's summary."""
    if _secret("YOUTUBE_API_KEY") is None:
        return "YOUTUBE_API_KEY is not set"
    return f"{YOUTUBE_CLEARED_ENV} is not 1 (ADR-009 YouTube preconditions not met)"


def x_bearer_token() -> str | None:
    return _secret("X_BEARER_TOKEN")


def configured_sources() -> list[str]:
    """The sources a fetch can read: YouTube with a cleared key, X with a token and a pinned id.

    With none, nothing can ever have been read, so the pipeline leaves the
    sentiment prompt alone instead of telling every ticker the feed is
    unavailable. Checked without `youtube_api_key()`'s warning, because the
    pipeline asks once per ticker.
    """
    sources = []
    if _secret("YOUTUBE_API_KEY") is not None and youtube_cleared():
        sources.append("youtube")
    if x_bearer_token() is not None and x_user_id() is not None:
        sources.append("x")
    return sources


def no_source_reason() -> str:
    """Why `configured_sources()` is empty, source by source, for the log."""
    x = "X_BEARER_TOKEN is not set" if x_bearer_token() is None else "no numeric user id pinned"
    return f"YouTube: {youtube_skip_reason()}; X: {x}"
