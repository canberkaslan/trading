"""FileKillSwitchReader semantics + scripts.kill_check exit codes.

The reader is the enforcement half of the mobile kill switch: the API
writes KILL_SWITCH_PATH, trade.py's circuit breaker and daily_run.sh's
pre-check read it. Failure direction matters — when in doubt, PAUSE_NEW.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tradingagents_us.risk.kill_switch import FileKillSwitchReader


class TestFileKillSwitchReader:
    def test_missing_file_is_run(self, tmp_path: Path) -> None:
        r = FileKillSwitchReader(path=str(tmp_path / "nope.state"))
        assert r.read() == "RUN"

    @pytest.mark.parametrize("state", ["RUN", "PAUSE_NEW", "FLATTEN_ALL"])
    def test_valid_states_pass_through(self, tmp_path: Path, state: str) -> None:
        f = tmp_path / "kill.state"
        f.write_text(state)
        assert FileKillSwitchReader(path=str(f)).read() == state

    def test_whitespace_tolerated(self, tmp_path: Path) -> None:
        f = tmp_path / "kill.state"
        f.write_text("  PAUSE_NEW\n")
        assert FileKillSwitchReader(path=str(f)).read() == "PAUSE_NEW"

    def test_empty_file_fails_to_pause(self, tmp_path: Path) -> None:
        # An armed-then-truncated file (crashed write, ENOSPC) must never
        # fail open — "never armed" is the missing-file case, not this one.
        f = tmp_path / "kill.state"
        f.write_text("")
        assert FileKillSwitchReader(path=str(f)).read() == "PAUSE_NEW"

    def test_default_path_is_absolute_not_cwd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Writer (API, hand-startable from anywhere) and readers must agree
        # on one file — the default anchors to the agent root, never CWD.
        monkeypatch.delenv("KILL_SWITCH_PATH", raising=False)
        from tradingagents_us.risk.kill_switch import default_kill_switch_path

        path = default_kill_switch_path()
        assert path.startswith("/")
        assert path.endswith("agent/kill_switch.state")

    def test_garbage_fails_to_pause(self, tmp_path: Path) -> None:
        f = tmp_path / "kill.state"
        f.write_text("BANANA")
        assert FileKillSwitchReader(path=str(f)).read() == "PAUSE_NEW"

    def test_unreadable_fails_to_pause(self, tmp_path: Path) -> None:
        # A directory at the path raises IsADirectoryError (an OSError)
        d = tmp_path / "kill.state"
        d.mkdir()
        assert FileKillSwitchReader(path=str(d)).read() == "PAUSE_NEW"

    def test_env_var_default_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "from_env.state"
        f.write_text("FLATTEN_ALL")
        monkeypatch.setenv("KILL_SWITCH_PATH", str(f))
        assert FileKillSwitchReader().read() == "FLATTEN_ALL"


class TestKillCheckExitCodes:
    """scripts.kill_check drives daily_run.sh: 0=RUN, 75=PAUSE, 76=FLATTENED."""

    def _run_main(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str | None):
        from scripts import kill_check

        f = tmp_path / "kill.state"
        if state is not None:
            f.write_text(state)
        monkeypatch.setenv("KILL_SWITCH_PATH", str(f))
        return kill_check

    def test_run_exits_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        kc = self._run_main(tmp_path, monkeypatch, "RUN")
        assert kc.main() == 0

    def test_missing_file_exits_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        kc = self._run_main(tmp_path, monkeypatch, None)
        assert kc.main() == 0

    def test_pause_exits_75_and_audits(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        kc = self._run_main(tmp_path, monkeypatch, "PAUSE_NEW")
        with patch.object(kc, "_audit") as audit:
            assert kc.main() == 75
        audit.assert_called_once()
        assert audit.call_args.args[0] == "PAUSE_NEW"

    def _flatten_cli(self, positions, close_results):
        cli = MagicMock()
        cli.list_positions.return_value = positions
        cli.close_all_positions.return_value = close_results
        cli.close = MagicMock()
        return cli

    def _pos(self, symbol: str):
        pos = MagicMock()
        pos.symbol = symbol
        pos.qty = 10.0
        return pos

    def test_flatten_submits_and_exits_76(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kc = self._run_main(tmp_path, monkeypatch, "FLATTEN_ALL")
        cli = self._flatten_cli([self._pos("AAPL")], [{"symbol": "AAPL", "status": 200}])
        with (
            patch("tradingagents_us.execution.flatten.AlpacaClient", return_value=cli),
            patch.object(kc, "_audit") as audit,
            patch.object(kc, "_notify") as notify,
        ):
            assert kc.main() == 76
        cli.close_all_positions.assert_called_once_with(cancel_orders=True)
        audit.assert_called_once()
        assert "executed" in audit.call_args.args[1]
        notify.assert_called_once()

    def test_partial_flatten_fails_loudly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # One halted symbol inside Alpaca's 207 body: its stop leg is already
        # cancelled, so reporting success would hide an UNPROTECTED position.
        kc = self._run_main(tmp_path, monkeypatch, "FLATTEN_ALL")
        cli = self._flatten_cli(
            [self._pos("AAPL"), self._pos("NVDA")],
            [{"symbol": "AAPL", "status": 200}, {"symbol": "NVDA", "status": 403}],
        )
        with (
            patch("tradingagents_us.execution.flatten.AlpacaClient", return_value=cli),
            patch.object(kc, "_audit") as audit,
            patch.object(kc, "_notify") as notify,
        ):
            assert kc.main() == 1  # fail-safe: daily_run skips + alerts
        assert "partial" in audit.call_args.args[1]
        assert "NVDA" in notify.call_args.args[1]

    def test_position_missing_from_response_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kc = self._run_main(tmp_path, monkeypatch, "FLATTEN_ALL")
        cli = self._flatten_cli(
            [self._pos("AAPL"), self._pos("MSFT")],
            [{"symbol": "AAPL", "status": 200}],  # MSFT never mentioned
        )
        with (
            patch("tradingagents_us.execution.flatten.AlpacaClient", return_value=cli),
            patch.object(kc, "_audit"),
            patch.object(kc, "_notify") as notify,
        ):
            assert kc.main() == 1
        assert "MSFT" in notify.call_args.args[1]

    def test_already_flat_is_quiet_noop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A still-armed switch on a flat book must not page every day.
        kc = self._run_main(tmp_path, monkeypatch, "FLATTEN_ALL")
        cli = self._flatten_cli([], [])
        with (
            patch("tradingagents_us.execution.flatten.AlpacaClient", return_value=cli),
            patch.object(kc, "_audit") as audit,
            patch.object(kc, "_notify") as notify,
        ):
            assert kc.main() == 76
        cli.cancel_all_orders.assert_called_once()
        assert "noop" in audit.call_args.args[1]
        notify.assert_not_called()

    def test_flatten_broker_error_exits_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kc = self._run_main(tmp_path, monkeypatch, "FLATTEN_ALL")
        cli = MagicMock()
        cli.list_positions.side_effect = RuntimeError("alpaca down")
        with (
            patch("tradingagents_us.execution.flatten.AlpacaClient", return_value=cli),
            patch.object(kc, "_audit") as audit,
            patch.object(kc, "_notify"),
        ):
            assert kc.main() == 1
        assert "FAILED" in audit.call_args.args[1]
