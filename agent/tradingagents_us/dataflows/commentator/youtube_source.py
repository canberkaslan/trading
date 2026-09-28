"""YouTube Data API v3: the commentator's uploads, as title + description + chapters.

The chain is channels.list (the channel's uploads playlist) -> playlistItems.list
(newest uploads) -> videos.list (snippet, and live timing). Each call costs one
quota unit; a routine run is three units against a free 10,000 a day.

What this can see is what the uploader typed: the title, the description and
its chapter lines. The spoken content is not available through any route this
system is allowed to use — `captions.download` needs the channel owner's
authorisation, and transcript scrapers or audio download break the YouTube
Terms — so no attempt is made. Most videos therefore yield topics and an
`unstated` stance, and the prompt says so.

Descriptions on this channel end in the same block of links and membership
promotion on every video. Lines that repeat across at least five descriptions
in the fetched batch are dropped before extraction: they cost tokens, carry no
view on the market, and would otherwise mark every video as a promo.

The API key travels in the `X-Goog-Api-Key` header, not the query string, so
httpx's INFO request log never carries it.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from .items import YOUTUBE, RawItem

log = logging.getLogger(__name__)

_BASE = "https://www.googleapis.com/youtube/v3"
_TIMEOUT = 10.0
_PAGE_SIZE = 50  # the API maximum for both playlistItems and videos

#: A line repeated in this many descriptions of one batch is channel boilerplate.
BOILERPLATE_MIN_REPEATS = 5

#: Enough for the longest descriptions seen on the channel (~1,500 characters of
#: unique text); a runaway description must not become a runaway extraction bill.
_MAX_DESCRIPTION_CHARS = 4000

# "00:00 Giriş", "1:02:03 - Fed kararı", "12:30 – NVDA"
_CHAPTER = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s*[-–—:]?\s*(\S.*)$")


class YouTubeClient:
    """Thin REST client. Methods raise; the ingest step decides what a failure costs."""

    def __init__(
        self, api_key: str, *, http: httpx.Client | None = None, timeout_s: float = _TIMEOUT
    ) -> None:
        self._http = http or httpx.Client(timeout=timeout_s)
        self._headers = {"X-Goog-Api-Key": api_key}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> YouTubeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        r = self._http.get(f"{_BASE}/{path}", params=params, headers=self._headers)
        r.raise_for_status()
        body = r.json()
        return body if isinstance(body, dict) else {}

    def uploads_playlist_id(self, channel_id: str) -> str:
        body = self._get("channels", {"part": "contentDetails", "id": channel_id})
        items = body.get("items") or []
        if not items:
            raise LookupError(f"YouTube channel {channel_id} not found")
        uploads = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads:
            raise LookupError(f"YouTube channel {channel_id} has no uploads playlist")
        return str(uploads)

    def playlist_page(
        self, playlist_id: str, page_token: str | None = None
    ) -> tuple[list[str], str | None]:
        params: dict[str, Any] = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": _PAGE_SIZE,
        }
        if page_token:
            params["pageToken"] = page_token
        body = self._get("playlistItems", params)
        ids = [
            str(it["contentDetails"]["videoId"])
            for it in body.get("items") or []
            if it.get("contentDetails", {}).get("videoId")
        ]
        return ids, body.get("nextPageToken") or None

    def videos(self, video_ids: Sequence[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i in range(0, len(video_ids), _PAGE_SIZE):
            chunk = video_ids[i : i + _PAGE_SIZE]
            body = self._get(
                "videos", {"part": "snippet,liveStreamingDetails", "id": ",".join(chunk)}
            )
            out.extend(v for v in body.get("items") or [] if isinstance(v, dict))
        return out


# ------------------------------------------------------------------ parsing


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:  # an unzoned stamp is not a UTC stamp
        return None
    return dt.astimezone(UTC)  # stored as UTC; SQLite keeps no offset


def published_at(video: dict[str, Any]) -> datetime | None:
    """The LATEST timestamp the API gives for a video, or None if it gives none.

    A live stream's `publishedAt` can predate the broadcast. The latest known
    stamp is used because a later date can only keep an item out of a
    backtest, never let one in early.
    """
    snippet = video.get("snippet") or {}
    live = video.get("liveStreamingDetails") or {}
    stamps = [
        _parse_ts(snippet.get("publishedAt")),
        _parse_ts(live.get("actualStartTime")),
        _parse_ts(live.get("actualEndTime")),
    ]
    known = [s for s in stamps if s is not None]
    return max(known) if known else None


def _norm(line: str) -> str:
    return " ".join(line.split())


def boilerplate_lines(
    descriptions: Iterable[str], min_repeats: int = BOILERPLATE_MIN_REPEATS
) -> frozenset[str]:
    """Normalised lines that occur in at least `min_repeats` distinct descriptions."""
    counts: Counter[str] = Counter()
    for d in descriptions:
        counts.update({_norm(line) for line in d.splitlines() if _norm(line)})
    return frozenset(line for line, n in counts.items() if n >= min_repeats)


def split_description(
    description: str, boilerplate: frozenset[str]
) -> tuple[list[tuple[str, str]], str]:
    """(chapters, body) with boilerplate lines removed from both."""
    chapters: list[tuple[str, str]] = []
    body: list[str] = []
    for raw in description.splitlines():
        line = _norm(raw)
        if not line:
            if body and body[-1]:
                body.append("")
            continue
        if line in boilerplate:
            continue
        m = _CHAPTER.match(line)
        if m:
            chapters.append((m.group(1), m.group(2).strip()))
            continue
        body.append(line)
    return chapters, "\n".join(body).strip()


def extraction_text(title: str, chapters: list[tuple[str, str]], body: str) -> str:
    parts = [f"Title: {title.strip()}"]
    if chapters:
        parts.append("Chapters:\n" + "\n".join(f"- {ts} {name}" for ts, name in chapters))
    if body:
        parts.append("Description:\n" + body[:_MAX_DESCRIPTION_CHARS])
    return "\n\n".join(parts)


def to_raw_item(
    video: dict[str, Any], channel_id: str, boilerplate: frozenset[str]
) -> RawItem | None:
    """A RawItem, or None for a video that must not be ingested.

    Refused: a video with no id, from another channel, not yet finished (an
    upcoming or in-progress live stream has no settled content), or undated.
    """
    vid = video.get("id")
    snippet = video.get("snippet") or {}
    if not vid:
        return None
    if snippet.get("channelId") and snippet.get("channelId") != channel_id:
        log.warning("video %s belongs to channel %s, not %s; skipped",
                    vid, snippet.get("channelId"), channel_id)
        return None
    if snippet.get("liveBroadcastContent") in ("upcoming", "live"):
        return None
    when = published_at(video)
    if when is None:
        log.warning("video %s carries no usable timestamp; skipped rather than guessed", vid)
        return None
    chapters, body = split_description(str(snippet.get("description") or ""), boilerplate)
    return RawItem(
        source=YOUTUBE,
        source_id=str(vid),
        channel_id=channel_id,
        url=f"https://www.youtube.com/watch?v={vid}",
        published_at=when,
        text=extraction_text(str(snippet.get("title") or ""), chapters, body),
    )


def fetch_recent(
    client: YouTubeClient, channel_id: str, *, since: datetime, max_pages: int = 1
) -> list[RawItem]:
    """Uploads published at or after `since`, newest first.

    Always reads at least one full page (fifty uploads) even when only a few
    are new: the boilerplate filter needs a batch to see what repeats, and the
    page costs the same one unit whether one video on it is new or fifty are.
    """
    playlist = client.uploads_playlist_id(channel_id)
    videos: list[dict[str, Any]] = []
    token: str | None = None
    for _ in range(max(1, max_pages)):
        ids, token = client.playlist_page(playlist, page_token=token)
        if not ids:
            break
        page = client.videos(ids)
        videos.extend(page)
        dates = [d for d in (published_at(v) for v in page) if d is not None]
        if token is None or (dates and min(dates) < since):
            break

    boilerplate = boilerplate_lines(
        str((v.get("snippet") or {}).get("description") or "") for v in videos
    )
    items = [to_raw_item(v, channel_id, boilerplate) for v in videos]
    fresh = [i for i in items if i is not None and i.published_at >= since]
    return sorted(fresh, key=lambda i: i.published_at, reverse=True)
