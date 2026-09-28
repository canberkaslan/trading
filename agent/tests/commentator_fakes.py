"""In-process stand-ins for the YouTube Data API and the X API (no network in tests).

Both are httpx MockTransport handlers behind the real clients, so what is
tested is the real request building and response parsing, and every request
is recorded for assertions about what was — and was not — sent.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from tradingagents_us.dataflows.commentator.x_source import XClient
from tradingagents_us.dataflows.commentator.youtube_source import YouTubeClient

CHANNEL = "UCrXj09uA0Nqv65st774NEKw"
UPLOADS = "UUrXj09uA0Nqv65st774NEKw"
YT_KEY = "yt-key-not-real-0001"
X_TOKEN = "x-token-not-real-0001"
X_USER = "1234567890"

#: The channel's repeated footer, shaped like the real one (links, membership,
#: disclaimer, hashtags) — five or more descriptions carry it.
BOILERPLATE = [
    "🔔 Abone ol: https://youtube.com/@boraozkentlenasdaq",
    "Üyelik ve Terminal: https://boraozkent.net/uyelik",
    "Burada paylaşılanlar yatırım tavsiyesi değildir.",
    "#borsa #nasdaq #hisse",
]


def video(
    vid: str,
    published: str | None,
    title: str,
    lines: Sequence[str] = (),
    *,
    chapters: Sequence[str] = (),
    channel: str = CHANNEL,
    broadcast: str = "none",
    live: dict[str, str] | None = None,
    footer: bool = True,
) -> dict[str, Any]:
    description = "\n".join([*lines, "", *chapters, "", *(BOILERPLATE if footer else [])])
    snippet: dict[str, Any] = {
        "channelId": channel,
        "title": title,
        "description": description,
        "liveBroadcastContent": broadcast,
    }
    if published is not None:
        snippet["publishedAt"] = published
    out: dict[str, Any] = {"id": vid, "snippet": snippet}
    if live:
        out["liveStreamingDetails"] = live
    return out


def _json(body: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )


class FakeYouTubeAPI:
    def __init__(self, videos: list[dict[str, Any]], page_size: int = 50) -> None:
        self.videos = videos
        self.page_size = page_size
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with:
            return _json({"error": {"code": self.fail_with}}, self.fail_with)
        path = request.url.path
        params = request.url.params
        if path.endswith("/channels"):
            assert params["id"] == CHANNEL
            uploads = {"relatedPlaylists": {"uploads": UPLOADS}}
            return _json({"items": [{"contentDetails": uploads}]})
        if path.endswith("/playlistItems"):
            assert params["playlistId"] == UPLOADS
            start = int(params.get("pageToken") or 0)
            chunk = self.videos[start : start + self.page_size]
            body: dict[str, Any] = {
                "items": [{"contentDetails": {"videoId": v["id"]}} for v in chunk]
            }
            if start + self.page_size < len(self.videos):
                body["nextPageToken"] = str(start + self.page_size)
            return _json(body)
        if path.endswith("/videos"):
            wanted = params["id"].split(",")
            return _json({"items": [v for v in self.videos if v["id"] in wanted]})
        return _json({"error": "unexpected path"}, 404)

    def client(self) -> YouTubeClient:
        return YouTubeClient(YT_KEY, http=httpx.Client(transport=httpx.MockTransport(self.handle)))


def post(pid: str, created_at: str | None, text: str, author: str = X_USER) -> dict[str, Any]:
    out: dict[str, Any] = {"id": pid, "text": text, "author_id": author, "lang": "tr"}
    if created_at is not None:
        out["created_at"] = created_at
    return out


class FakeXAPI:
    def __init__(self, posts: list[dict[str, Any]], username: str = "BoraOzkentNSDQ") -> None:
        self.posts = {p["id"]: p for p in posts}
        self.username = username
        self.requests: list[httpx.Request] = []

    def delete(self, pid: str) -> None:
        self.posts.pop(pid, None)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = request.url.params
        if path == f"/2/users/{X_USER}/tweets":
            since = int(params.get("since_id") or 0)
            newest = sorted(self.posts.values(), key=lambda p: int(p["id"]), reverse=True)
            data = [p for p in newest if int(p["id"]) > since][: int(params["max_results"])]
            return _json({
                "data": data,
                "includes": {"users": [{"id": X_USER, "username": self.username}]},
                "meta": {"result_count": len(data)},
            })
        if path == "/2/tweets":
            ids = params["ids"].split(",")
            data = [self.posts[i] for i in ids if i in self.posts]
            errors = [
                {"value": i, "title": "Not Found Error", "resource_type": "tweet"}
                for i in ids
                if i not in self.posts
            ]
            return _json({"data": data, "errors": errors})
        if path.startswith("/2/users/by/username/"):
            return _json({"data": {"id": X_USER, "username": path.rsplit("/", 1)[-1]}})
        return _json({"error": "unexpected path"}, 404)

    def client(self) -> XClient:
        return XClient(X_TOKEN, http=httpx.Client(transport=httpx.MockTransport(self.handle)))
