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
YOUTUBE_RETENTION = timedelta(days=30)

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


def youtube_api_key() -> str | None:
    return _secret("YOUTUBE_API_KEY")


def x_bearer_token() -> str | None:
    return _secret("X_BEARER_TOKEN")
