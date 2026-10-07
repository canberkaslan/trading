"""A FLATTEN_ALL answered 202 must outlive a stop of the API process.

The 202 tells the operator the flatten runs once the submit lock is let go,
and that its outcome goes to the audit trail. The flatten then runs on a
thread of its own. A deploy or `systemctl restart ai-trader-api` in that wait
must not drop it: uvicorn's graceful stop waits only for requests still in
flight, then re-raises SIGTERM, which ends the process without joining any
thread, daemon or not.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import create_engine, select

from tradingagents_us.execution import submit_lock
from tradingagents_us.execution.flatten import FlattenResult
from tradingagents_us.file_lock import exclusive
from tradingagents_us.notifications.ops_channel import ChannelResult, Delivery
from tradingagents_us.storage import TradeLogRepository
from tradingagents_us.storage.models import KillSwitchEventRow

_AGENT_DIR = Path(__file__).resolve().parents[1]

#: The real app under real uvicorn; only the broker and the pager are stand-ins.
_LAUNCHER = """
import sys
from pathlib import Path

import uvicorn

from api.main import app
from api.routes import orders
from tradingagents_us.execution.flatten import FlattenResult
from tradingagents_us.notifications.ops_channel import ChannelResult, Delivery

port, marks = int(sys.argv[1]), Path(sys.argv[2])


def flatten_all(client=None):
    (marks / "flattened").touch()
    return FlattenResult(ok=True, noop=True, summary="book already flat; open orders cancelled")


def page(title, body, kind="ops"):
    (marks / "paged").write_text(f"{title}\\n{body}")
    return Delivery((ChannelResult("push", True, "sent"),))


orders.flatten_all = flatten_all
orders.send_ops_alert = page
orders.KILL_SWITCH_ANSWER_S = 0.3
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_until_serving(proc: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            pytest.fail(f"the API exited before serving: {out}")
        with contextlib.suppress(httpx.HTTPError):
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                return
        time.sleep(0.1)
    pytest.fail("the API never started serving")


def _kill_details(db: Path) -> list[str]:
    repo = TradeLogRepository(engine=create_engine(f"sqlite:///{db}", future=True))
    with repo.session() as s:
        return [d for d in s.execute(select(KillSwitchEventRow.detail)).scalars().all() if d]


@pytest.mark.slow
def test_a_flatten_answered_202_still_runs_when_the_api_is_stopped(tmp_path: Path) -> None:
    marks = tmp_path / "marks"
    marks.mkdir()
    db = tmp_path / "trades.db"
    env = {
        **os.environ,
        "ALLOW_ANONYMOUS_ADMIN": "1",
        "TRADE_LOG_DB_URL": f"sqlite:///{db}",
        "PUSH_DISABLED": "1",
        "PYTHONPATH": os.pathsep.join(
            p for p in (str(_AGENT_DIR), os.environ.get("PYTHONPATH", "")) if p
        ),
    }
    for var in ("DEV_API_TOKEN", "COGNITO_USER_POOL_ID", "FIREBASE_PROJECT_ID"):
        env.pop(var, None)
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-c", _LAUNCHER, str(port), str(marks)],
        cwd=_AGENT_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        _wait_until_serving(proc, port)
        # An order path (the position pass, a ticker) holds the submit lock.
        with exclusive(submit_lock.lock_path()):
            r = httpx.post(
                f"http://127.0.0.1:{port}/v1/orders/kill-switch",
                json={"state": "FLATTEN_ALL"}, timeout=10,
            )
            assert r.status_code == 202, r.text
            assert "audit" in r.json()["pending"]
            # A deploy, or `systemctl restart ai-trader-api`, in the wait.
            proc.send_signal(signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(2)
        proc.wait(30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert (marks / "flattened").exists(), "the flatten answered 202 died with the API"
    assert any(d.startswith("noop: ") for d in _kill_details(db)), (
        "the flatten answered 202 left no audit row"
    )


def test_a_flatten_the_stop_cannot_wait_for_is_paged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The lock stays held past what a stop can wait (systemd SIGKILLs at 90 s):
    # the flatten may never be sent, and the operator was told it would be.
    from api.routes import orders

    flattened: list[str] = []
    pages: list[tuple[str, str, str]] = []

    def page(title: str, body: str, kind: str = "ops") -> Delivery:
        pages.append((title, body, kind))
        return Delivery((ChannelResult("push", True, "sent"),))

    def flatten(client: object = None) -> FlattenResult:
        flattened.append("FLATTEN_ALL")
        return FlattenResult(ok=True, noop=True, summary="book already flat")

    monkeypatch.setattr(orders, "flatten_all", flatten)
    monkeypatch.setattr(orders, "send_ops_alert", page)
    Path(os.environ["KILL_SWITCH_PATH"]).write_text("FLATTEN_ALL")
    with exclusive(submit_lock.lock_path()):
        _, done, _ = orders._behind_the_lock(MagicMock(), "op", flatten=True)
        orders.drain_flattens(timeout_s=0.3)
        assert not flattened
        [(title, body, kind)] = pages
        assert "FLATTEN_ALL" in title
        assert "22:30 UTC" in body
        assert kind == "kill_switch"
    assert done.wait(10)
    assert flattened == ["FLATTEN_ALL"]
