"""An in-memory stand-in for the GitHub REST endpoints the ops channel uses."""

from __future__ import annotations

import json

import httpx

#: Never a real credential: tests must not be able to file a live issue.
FAKE_GITHUB_TOKEN = "gh-test-token-not-real"


class FakeGitHub:
    """The GitHub REST endpoints the ops channel uses, recorded in memory."""

    def __init__(self) -> None:
        self.open_issues: list[dict[str, object]] = []
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []
        self.auth_headers: list[str] = []
        self.error: Exception | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.error is not None:
            raise self.error
        payload = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, payload))
        self.auth_headers.append(request.headers.get("Authorization", ""))
        path = request.url.path
        if request.method == "GET" and path.endswith("/issues"):
            return httpx.Response(200, json=self.open_issues)
        if request.method == "POST" and path.endswith("/issues"):
            return httpx.Response(201, json={"number": 41})
        if request.method == "POST" and path.endswith("/comments"):
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404, json={"message": "Not Found"})

    @property
    def opened(self) -> list[dict[str, object]]:
        return [p or {} for m, path, p in self.requests if m == "POST" and path.endswith("/issues")]

    @property
    def comments(self) -> list[tuple[str, dict[str, object]]]:
        return [
            (path, p or {})
            for m, path, p in self.requests
            if m == "POST" and path.endswith("/comments")
        ]

    @property
    def posted_text(self) -> str:
        """Everything that would have been published, for leak assertions."""
        return "\n".join(
            json.dumps(p, ensure_ascii=False) for m, _, p in self.requests if m == "POST"
        )
