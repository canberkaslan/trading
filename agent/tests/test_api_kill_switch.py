"""Kill-switch API semantics: immediate FLATTEN execution at flip time,
atomic state writes, and the approve path honoring the armed switch."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

from tradingagents_us.execution.flatten import FlattenResult


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # These exercise kill-switch BEHAVIOUR, not authorisation. The open
    # posture used to be implied by the absence of every auth env var;
    # it is now asked for, so the test says which posture it wants
    # instead of inheriting one.
    monkeypatch.setenv("ALLOW_ANONYMOUS_ADMIN", "1")
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.setenv("KILL_SWITCH_PATH", str(tmp_path / "kill.state"))

    from api.deps import get_repo
    from api.main import app

    repo = MagicMock()
    # A failed flatten is paged; never through the live channels from here.
    monkeypatch.setattr("api.routes.orders.send_ops_alert", MagicMock())
    app.dependency_overrides[get_repo] = lambda: repo
    yield TestClient(app)
    app.dependency_overrides.pop(get_repo, None)


class TestSetKillSwitch:
    def test_run_writes_state_without_flatten(self, client: TestClient, tmp_path: Path) -> None:
        with patch("api.routes.orders.flatten_all") as fl:
            r = client.post("/v1/orders/kill-switch", json={"state": "RUN"})
        assert r.status_code == 200
        assert (tmp_path / "kill.state").read_text() == "RUN"
        fl.assert_not_called()

    def test_flatten_all_executes_immediately(self, client: TestClient, tmp_path: Path) -> None:
        # The panic button must liquidate at flip time, not at 22:30 UTC.
        with patch("api.routes.orders.flatten_all") as fl:
            fl.return_value = FlattenResult(
                ok=True, summary="close orders submitted for 2/2", submitted=["AAPL", "NVDA"]
            )
            r = client.post("/v1/orders/kill-switch", json={"state": "FLATTEN_ALL"})
        assert r.status_code == 200
        fl.assert_called_once()
        assert "close orders submitted" in r.json()["flatten"]
        assert (tmp_path / "kill.state").read_text() == "FLATTEN_ALL"

    def test_partial_flatten_surfaces_as_error(self, client: TestClient, tmp_path: Path) -> None:
        with patch("api.routes.orders.flatten_all") as fl:
            fl.return_value = FlattenResult(
                ok=False, summary="PARTIAL: NVDA failed", failed=["NVDA: status=403"]
            )
            r = client.post("/v1/orders/kill-switch", json={"state": "FLATTEN_ALL"})
        assert r.status_code == 502
        # State stays armed so the daily-run backstop retries
        assert (tmp_path / "kill.state").read_text() == "FLATTEN_ALL"

    def test_flatten_exception_keeps_switch_armed(self, client: TestClient, tmp_path: Path) -> None:
        with patch("api.routes.orders.flatten_all", side_effect=RuntimeError("alpaca down")):
            r = client.post("/v1/orders/kill-switch", json={"state": "FLATTEN_ALL"})
        assert r.status_code == 502
        assert (tmp_path / "kill.state").read_text() == "FLATTEN_ALL"

    def test_no_tmp_file_left_behind(self, client: TestClient, tmp_path: Path) -> None:
        client.post("/v1/orders/kill-switch", json={"state": "PAUSE_NEW"})
        assert not list(tmp_path.glob("kill.state*.tmp"))


class TestTwoTapsAtOnce:
    """The handler runs in the threadpool, so a panicked double tap runs side by side."""

    def test_both_land_and_the_last_one_is_acted_on(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Both taps have written their state before either renames it over
        # the flag: PAUSE_NEW first, then FLATTEN_ALL.
        from api.routes import orders
        from tradingagents_us.schemas import KillSwitchState

        flag = str(tmp_path / "kill.state")
        at_rename = {"tap-1": threading.Event(), "tap-2": threading.Event()}
        tap1_renamed = threading.Event()
        real_replace = os.replace

        def replace(src: Any, dst: Any) -> None:
            who = threading.current_thread().name
            if str(dst) != flag or who not in at_rename:
                return real_replace(src, dst)
            at_rename[who].set()
            if who == "tap-1":
                at_rename["tap-2"].wait(2)
                try:
                    return real_replace(src, dst)
                finally:
                    tap1_renamed.set()
            tap1_renamed.wait(2)
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", replace)
        answers: dict[str, Any] = {}

        def tap(state: KillSwitchState) -> None:
            try:
                answers[threading.current_thread().name] = orders.set_kill_switch(
                    orders.KillSwitchUpdate(state=state), Response(), user="op",
                    repo=MagicMock(),
                )
            except Exception as exc:  # noqa: BLE001 — the answer under test
                answers[threading.current_thread().name] = exc

        with patch("api.routes.orders.flatten_all") as fl:
            fl.return_value = FlattenResult(ok=True, noop=True, summary="book already flat")
            pause = threading.Thread(target=tap, args=("PAUSE_NEW",), name="tap-1")
            pause.start()
            assert at_rename["tap-1"].wait(5)
            flat = threading.Thread(target=tap, args=("FLATTEN_ALL",), name="tap-2")
            flat.start()
            pause.join(10)
            flat.join(10)

        assert answers["tap-1"]["state"] == "PAUSE_NEW", answers["tap-1"]
        assert answers["tap-2"]["state"] == "FLATTEN_ALL", answers["tap-2"]
        assert (tmp_path / "kill.state").read_text() == "FLATTEN_ALL"
        # The switch reads FLATTEN_ALL, so the book was flattened.
        fl.assert_called_once()
        assert not list(tmp_path.glob("kill.state*.tmp"))


class TestGetKillSwitch:
    def test_missing_file_is_run(self, client: TestClient) -> None:
        assert client.get("/v1/orders/kill-switch").json()["state"] == "RUN"

    def test_empty_file_fails_to_pause(self, client: TestClient, tmp_path: Path) -> None:
        (tmp_path / "kill.state").write_text("")
        assert client.get("/v1/orders/kill-switch").json()["state"] == "PAUSE_NEW"


class TestApproveHonorsKillSwitch:
    @pytest.mark.parametrize("state", ["PAUSE_NEW", "FLATTEN_ALL"])
    def test_armed_switch_blocks_approve(
        self, client: TestClient, tmp_path: Path, state: str
    ) -> None:
        # A stale Approve tap must not open a position while the switch is
        # armed — this path used to bypass the kill switch entirely.
        (tmp_path / "kill.state").write_text(state)
        r = client.post("/v1/orders/some-order-id/approve")
        assert r.status_code == 409
        assert state in r.json()["detail"]
