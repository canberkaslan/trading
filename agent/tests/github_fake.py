"""An in-memory GitHub for the ops channel: the box's dispatch, and the workflow it starts.

The box never writes an issue. It dispatches `.github/workflows/box-alert.yml`,
and that workflow runs `scripts/box_alert_issue.py` with its own token. This
fake plays both halves, and the workflow half is read out of the real YAML: the
dispatch is checked against the inputs the workflow declares, and the script is
run with exactly the environment the workflow's step maps those inputs into. A
renamed input or env var on either side therefore fails here, not on the box.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

#: Never a real credential: tests must not be able to file a live issue.
FAKE_GITHUB_TOKEN = "gh-test-token-not-real"
#: What the workflow's `secrets.GITHUB_TOKEN` resolves to in the fake runner.
FAKE_WORKFLOW_TOKEN = "gh-workflow-token-not-real"
BOT_LOGIN = "github-actions[bot]"

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "box-alert.yml"
_DISPATCH = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/actions/workflows/(?P<wf>[^/]+)/dispatches$")
_WORKFLOW = re.compile(r"^/repos/(?P<repo>[^/]+/[^/]+)/actions/workflows/(?P<wf>[^/]+)$")
_EXPR = re.compile(r"^\$\{\{\s*(inputs|secrets)\.([A-Za-z_]+)\s*\}\}$")


def load_workflow() -> dict:
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as the boolean True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def workflow_step() -> dict:
    """The step that runs the issue script."""
    (job,) = load_workflow()["jobs"].values()
    return next(s for s in job["steps"] if "box_alert_issue.py" in str(s.get("run", "")))


class FakeGitHub:
    """GitHub REST, recorded in memory, with the box-alert workflow runnable."""

    def __init__(self) -> None:
        self.open_issues: list[dict[str, object]] = []
        #: Every call on either side: (method, path, payload).
        self.requests: list[tuple[str, str, dict[str, object] | None]] = []
        #: The Authorization header each request carried, in the same order.
        self.auth_headers: list[str] = []
        self.dispatches: list[dict[str, object]] = []
        #: What the fake runner printed or raised for each workflow run.
        self.runs: list[int] = []
        self.error: Exception | None = None
        #: What reading the workflow with the box's token answers.
        self.workflow_status = 200
        self.workflow_headers: dict[str, str] = {}
        self._next_number = 41

    # --- the box's side: httpx, through ops_channel's client ---------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.error is not None:
            raise self.error
        payload = json.loads(request.content) if request.content else None
        path = request.url.path
        self.requests.append((request.method, path, payload))
        self.auth_headers.append(request.headers.get("Authorization", ""))
        m = _DISPATCH.match(path)
        if request.method == "POST" and m:
            return self._dispatch(m["repo"], m["wf"], payload or {})
        m = _WORKFLOW.match(path)
        if request.method == "GET" and m:
            if self.workflow_status != 200:
                return httpx.Response(
                    self.workflow_status, json={"message": "no"}, headers=self.workflow_headers
                )
            return httpx.Response(
                200, json={"path": f".github/workflows/{m['wf']}"}, headers=self.workflow_headers
            )
        return httpx.Response(404, json={"message": "Not Found"})

    def _dispatch(self, repo: str, wf: str, payload: dict[str, object]) -> httpx.Response:
        if wf != WORKFLOW_PATH.name:
            return httpx.Response(404, json={"message": "workflow not found"})
        declared = load_workflow()["on"]["workflow_dispatch"]["inputs"]
        inputs = payload.get("inputs") or {}
        assert isinstance(inputs, dict)
        unknown = set(inputs) - set(declared)
        missing = {k for k, v in declared.items() if v.get("required") and k not in inputs}
        if unknown or missing or not payload.get("ref"):
            # What GitHub answers for an input the workflow does not declare.
            return httpx.Response(
                422, json={"message": f"Unexpected inputs {unknown}, missing {missing}"}
            )
        self.dispatches.append(payload)
        resolved = {k: str(inputs.get(k, v.get("default", ""))) for k, v in declared.items()}
        self.runs.append(self.run_workflow(repo, resolved))
        return httpx.Response(204)

    # --- the workflow's side: box_alert_issue on the runner ----------------

    def run_workflow(self, repo: str, inputs: dict[str, str]) -> int:
        """Run the step the way Actions would: its env, its script, its token."""
        from scripts import box_alert_issue

        step = workflow_step()
        assert step["run"].strip() == "python agent/scripts/box_alert_issue.py"
        env = {"GITHUB_REPOSITORY": repo}
        for name, expr in step["env"].items():
            m = _EXPR.match(str(expr))
            assert m, f"{name}: {expr!r} is not a plain inputs/secrets expression"
            scope, key = m.groups()
            if scope == "secrets":
                assert key == "GITHUB_TOKEN", f"{name} must come from the workflow token"
                env[name] = FAKE_WORKFLOW_TOKEN
            else:
                env[name] = inputs[key]
        return box_alert_issue.main(env, request=self._as_bot(env["GITHUB_TOKEN"]))

    def _as_bot(self, token: str):
        def request(method: str, url: str, payload: dict[str, object] | None = None) -> object:
            path = urlparse(url).path
            self.requests.append((method, path, payload))
            self.auth_headers.append(f"Bearer {token}")
            if method == "GET" and path.endswith("/issues"):
                return list(self.open_issues)
            if method == "POST" and path.endswith("/issues"):
                number = self._next_number
                self._next_number += 1
                self.open_issues.append(
                    {"number": number, "body": (payload or {}).get("body"),
                     "user": {"login": BOT_LOGIN}}
                )
                return {"number": number}
            if method == "POST" and path.endswith("/comments"):
                return {"id": 1}
            raise AssertionError(f"unexpected {method} {path}")

        return request

    # --- what tests read ----------------------------------------------------

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
