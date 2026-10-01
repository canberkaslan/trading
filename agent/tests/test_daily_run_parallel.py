"""daily_run.sh councils tickers side by side, within a bound, in order.

Runs the real script under the recording stubs of test_daily_run_alerting. A
stand-in for `scripts.trade` (FAKE_TRADE_HOOK) sleeps, records how many
tickers were in flight when it started, and fails where a test says so.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

from tests.test_daily_run_alerting import RUN_DATE, Run, run_daily

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

UNIVERSE = "AAPL MSFT NVDA GOOGL AMZN"

# argv: --ticker T --date D [--submit]. Registers itself as running, notes how
# many were running, prints a marker, sleeps, and exits with FAIL_<T> or 0.
HOOK = r"""#!/usr/bin/env bash
ticker="$2"
mkdir -p "$HOOK_DIR/running" "$HOOK_DIR/peaks" "$HOOK_DIR/state" "$HOOK_DIR/pids"
echo $$ > "$HOOK_DIR/pids/$ticker"
: > "$HOOK_DIR/running/$ticker"
ls "$HOOK_DIR/running" | wc -l | tr -d ' ' > "$HOOK_DIR/peaks/$ticker"
printf '%s\n' "$TRADINGAGENTS_RUN_STATE_DIR" > "$HOOK_DIR/state/$ticker"
echo "council output for $ticker"
delay="SLEEP_${ticker}"
sleep "${!delay:-1}"
rm -f "$HOOK_DIR/running/$ticker"
rc="FAIL_${ticker}"
exit "${!rc:-0}"
"""


def _run(tmp_path: Path, **env: str) -> tuple[Run, Path]:
    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    hook = tmp_path / "trade_hook.sh"
    hook.write_text(HOOK, encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    base = {
        "UNIVERSE": UNIVERSE,
        "FAKE_TRADE_HOOK": str(hook),
        "HOOK_DIR": str(hook_dir),
        "COUNCIL_PARALLELISM": "2",
    }
    return run_daily(tmp_path, **{**base, **env}), hook_dir


def _peaks(hook_dir: Path) -> dict[str, int]:
    return {p.name: int(p.read_text()) for p in (hook_dir / "peaks").iterdir()}


def test_every_ticker_runs_and_the_bound_holds(tmp_path: Path) -> None:
    run, hook_dir = _run(tmp_path)
    assert run.rc == 0, run.output
    peaks = _peaks(hook_dir)
    assert sorted(peaks) == sorted(UNIVERSE.split())
    assert max(peaks.values()) <= 2
    assert max(peaks.values()) == 2, "no two councils ever overlapped"


def test_a_bound_of_one_is_the_old_sequential_run(tmp_path: Path) -> None:
    run, hook_dir = _run(tmp_path, COUNCIL_PARALLELISM="1")
    assert run.rc == 0, run.output
    assert set(_peaks(hook_dir).values()) == {1}


def test_the_default_is_the_sequential_run(tmp_path: Path) -> None:
    # A box that pulls this without touching its config must trade as before.
    run, hook_dir = _run(tmp_path, COUNCIL_PARALLELISM="")
    assert run.rc == 0, run.output
    assert "parallelism=1" in run.output
    assert set(_peaks(hook_dir).values()) == {1}


def test_the_universe_reaches_each_ticker(tmp_path: Path) -> None:
    hook = tmp_path / "env_hook.sh"
    hook.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$UNIVERSE" > "$HOOK_DIR/universe-$2"\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)
    run, hook_dir = _run(tmp_path, FAKE_TRADE_HOOK=str(hook), UNIVERSE="AAPL MSFT")
    assert run.rc == 0, run.output
    assert (hook_dir / "universe-AAPL").read_text().split() == ["AAPL", "MSFT"]


@pytest.mark.parametrize(("value", "expected"), [("zero", "1"), ("0", "1"), ("50", "6")])
def test_a_bad_bound_is_corrected_loudly(tmp_path: Path, value: str, expected: str) -> None:
    run, _ = _run(tmp_path, COUNCIL_PARALLELISM=value, UNIVERSE="AAPL")
    assert run.rc == 0, run.output
    assert f"parallelism={expected}" in run.output
    assert "WARNING: COUNCIL_PARALLELISM" in run.output


def test_output_is_in_universe_order_whatever_finishes_first(tmp_path: Path) -> None:
    # The first ticker is the slowest, so it finishes last.
    run, _ = _run(tmp_path, COUNCIL_PARALLELISM="5", SLEEP_AAPL="2", SLEEP_MSFT="0")
    assert run.rc == 0, run.output
    headers = re.findall(r"^--- (\w+) @ ", run.output, flags=re.M)
    assert headers == UNIVERSE.split()
    for ticker in UNIVERSE.split():
        block = run.output.split(f"--- {ticker} @ {RUN_DATE} ---", 1)[1]
        # Each ticker's own output sits under its own header.
        assert block.lstrip().startswith(f"council output for {ticker}")


def test_one_tickers_failure_does_not_touch_the_others(tmp_path: Path) -> None:
    run, hook_dir = _run(tmp_path, FAIL_MSFT="1", FAIL_NVDA="124")
    assert run.rc == 1
    assert sorted(_peaks(hook_dir)) == sorted(UNIVERSE.split())
    assert "-> MSFT FAILED (rc=1)" in run.output
    assert "-> NVDA TIMED OUT" in run.output
    for ok in ("AAPL", "GOOGL", "AMZN"):
        assert f"-> {ok} done" in run.output
    assert "2 ticker(s) errored" in run.output
    [alert] = run.alerts("daily_run")
    assert alert["--body"].startswith("Failed: MSFT NVDA")


def _run_log(tmp_path: Path) -> str:
    return (tmp_path / "logs" / f"daily_{RUN_DATE}.log").read_text(encoding="utf-8")


def test_ticker_logs_are_kept_beside_the_run_log(tmp_path: Path) -> None:
    run, _ = _run(tmp_path)
    assert run.rc == 0, run.output
    kept = {
        p.name.rsplit("-", 1)[-1].removesuffix(".log"): p
        for p in (tmp_path / "logs" / f"daily_{RUN_DATE}.d").glob("*.log")
    }
    assert sorted(kept) == sorted(UNIVERSE.split())
    assert "council output for NVDA" in kept["NVDA"].read_text(encoding="utf-8")


def test_progress_lines_reach_the_run_log(tmp_path: Path) -> None:
    run, _ = _run(tmp_path)
    assert run.rc == 0, run.output
    log = _run_log(tmp_path)
    for ticker in UNIVERSE.split():
        assert f"[council] {ticker} started" in log
        assert f"[council] {ticker} finished rc=0" in log


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_sigterm_stops_the_councils_and_pages(tmp_path: Path) -> None:
    hook_dir = tmp_path / "hook"

    def terminate_mid_council(proc: subprocess.Popen[str]) -> None:
        deadline = time.monotonic() + 30
        pids = hook_dir / "pids"
        while time.monotonic() < deadline:
            if pids.exists() and len(list(pids.iterdir())) >= 2:
                break
            time.sleep(0.1)
        time.sleep(0.3)  # let the stand-ins print their first line
        proc.send_signal(signal.SIGTERM)

    run, _ = _run(
        tmp_path, during=terminate_mid_council, SLEEP_AAPL="60", SLEEP_MSFT="60"
    )
    assert run.rc == 143, run.output
    # Nothing outlives the run: the stand-ins were killed, not orphaned.
    time.sleep(0.5)
    for pid_file in (hook_dir / "pids").iterdir():
        assert not _alive(int(pid_file.read_text())), f"{pid_file.name} still running"
    # Not the rest of the universe either.
    assert sorted(p.name for p in (hook_dir / "pids").iterdir()) == ["AAPL", "MSFT"]
    # The dead-man's switch hears a failure.
    assert run.ping_urls and run.ping_urls[-1].endswith("/fail")
    # What the councils had said is kept, in the ticker logs and the run log.
    log = _run_log(tmp_path)
    assert "SIGNAL received" in log
    assert "council output for AAPL" in log
    assert list((tmp_path / "logs" / f"daily_{RUN_DATE}.d").glob("*-AAPL.log"))


def test_the_run_shares_one_state_dir_and_cleans_it_up(tmp_path: Path) -> None:
    run, hook_dir = _run(tmp_path)
    assert run.rc == 0, run.output
    dirs = {p.read_text().strip() for p in (hook_dir / "state").iterdir()}
    assert len(dirs) == 1
    [state_dir] = dirs
    assert state_dir.startswith(str(tmp_path))
    assert not Path(state_dir).exists(), "the run's breaker state outlived the run"
