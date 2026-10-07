"""daily_run.sh with COUNCIL_PARALLELISM: councils side by side, one submit pass after.

Runs the real script under the recording stubs of test_daily_run_alerting. A
stand-in for `scripts.trade` sleeps, records how many councils were in flight
when it started, writes the decision record a real `--plan-dir` council would,
and fails where a test says so. A stand-in for `scripts.submit_plans` records
what it was handed and reports each ticker the way the real one does.

What the Python halves do with a record (send nothing in the first pass, size
again and send in universe order in the second) is pinned in
test_submit_plans.py; these pin the orchestration around them.
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

from tests.test_daily_run_alerting import HC, RUN_DATE, Run, run_daily

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

UNIVERSE = "AAPL MSFT NVDA GOOGL AMZN"

# argv: --ticker T --date D [--plan-dir DIR --run-id ID] [--submit]
COUNCIL_HOOK = r"""#!/usr/bin/env bash
ticker="$2" plan_dir=""
while [[ $# -gt 0 ]]; do
  case "$1" in --plan-dir) plan_dir="$2"; shift ;; esac
  shift
done
mkdir -p "$HOOK_DIR/running" "$HOOK_DIR/peaks" "$HOOK_DIR/pids"
echo $$ > "$HOOK_DIR/pids/$ticker"
: > "$HOOK_DIR/running/$ticker"
ls "$HOOK_DIR/running" | wc -l | tr -d ' ' > "$HOOK_DIR/peaks/$ticker"
echo "council output for $ticker"
delay="SLEEP_${ticker}"
sleep "${!delay:-0.2}"
noplan="NOPLAN_${ticker}"
if [[ -n "$plan_dir" && -z "${!noplan:-}" ]]; then
  echo '{}' > "$plan_dir/$ticker.plan.json"
fi
rm -f "$HOOK_DIR/running/$ticker"
rc="FAIL_${ticker}"
exit "${!rc:-0}"
"""

# argv: --plan-dir DIR --run-id ID --date D [--submit] TICKER...
SUBMIT_HOOK = r"""#!/usr/bin/env bash
echo $$ > "$HOOK_DIR/submit.pid"
ls "$HOOK_DIR/running" 2>/dev/null | wc -l | tr -d ' ' > "$HOOK_DIR/submit.in_flight"
ls "$2" > "$HOOK_DIR/submit.records"
shift 6
[[ "${1:-}" == "--submit" ]] && shift
if [[ -n "${SUBMIT_CRASH:-}" ]]; then exit "$SUBMIT_CRASH"; fi
sleep "${SUBMIT_SLEEP:-0}"
failed=0
for t in "$@"; do
  echo "--- $t submit @ x ---"
  fail="SUBMIT_FAIL_${t}"
  if [[ -n "${!fail:-}" ]]; then
    echo "  -> $t FAILED (rc=1)"
    failed=1
  else
    echo "  -> $t done"
  fi
done
exit "$failed"
"""


def _hook(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _run(tmp_path: Path, **env: str) -> tuple[Run, Path]:
    hook_dir = tmp_path / "hook"
    hook_dir.mkdir()
    base = {
        "UNIVERSE": UNIVERSE,
        "COUNCIL_PARALLELISM": "4",
        "FAKE_HOOK_scripts_trade": str(_hook(tmp_path, "council.sh", COUNCIL_HOOK)),
        "FAKE_HOOK_scripts_submit_plans": str(_hook(tmp_path, "submit.sh", SUBMIT_HOOK)),
        "HOOK_DIR": str(hook_dir),
    }
    return run_daily(tmp_path, **{**base, **env}), hook_dir


def _peaks(hook_dir: Path) -> dict[str, int]:
    return {p.name: int(p.read_text()) for p in (hook_dir / "peaks").iterdir()}


def _calls(run: Run, module: str) -> list[list[str]]:
    return [c for c in run.calls if c[0] == module]


def _opt(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _submit_tickers(argv: list[str]) -> list[str]:
    rest = argv[1:]
    for flag in ("--plan-dir", "--run-id", "--date"):
        i = rest.index(flag)
        del rest[i : i + 2]
    return [a for a in rest if a != "--submit"]


def _run_log(tmp_path: Path) -> str:
    return (tmp_path / "logs" / f"daily_{RUN_DATE}.log").read_text(encoding="utf-8")


def _plan_dirs(tmp_path: Path) -> list[Path]:
    return list(tmp_path.glob("daily-plans.*"))


# --- parallelism 1 is today's run ---------------------------------------------


@pytest.mark.parametrize("value", [None, "1"])
@pytest.mark.parametrize("submit", ["0", "1"])
def test_one_is_todays_sequential_run(tmp_path: Path, value: str | None, submit: str) -> None:
    env = {"SUBMIT": submit}
    if value is not None:
        env["COUNCIL_PARALLELISM"] = value
    run = run_daily(tmp_path, UNIVERSE=UNIVERSE, **env)
    assert run.rc == 0, run.output

    flag = ["--submit"] if submit == "1" else []
    assert _calls(run, "scripts.trade") == [
        ["scripts.trade", "--ticker", t, "--date", RUN_DATE, *flag] for t in UNIVERSE.split()
    ]
    assert _calls(run, "scripts.submit_plans") == []
    assert "--- councils" not in run.output
    assert "WARNING: COUNCIL_PARALLELISM" not in run.output
    headers = re.findall(r"^--- (\w+) @ ", run.output, flags=re.M)
    assert headers == UNIVERSE.split()
    assert all(f"-> {t} done" in run.output for t in UNIVERSE.split())


def test_one_runs_one_council_at_a_time(tmp_path: Path) -> None:
    run, hook_dir = _run(tmp_path, COUNCIL_PARALLELISM="1")
    assert run.rc == 0, run.output
    assert set(_peaks(hook_dir).values()) == {1}


@pytest.mark.parametrize(
    ("value", "expected", "warned"),
    [
        ("zero", "1", True),
        ("0", "1", True),
        ("-2", "1", True),
        ("2.5", "1", True),
        ("02", "1", True),
        ("", "1", False),  # unset by another name: the default, silently
        ("3", "3", False),
        ("5", "4", True),
        ("10", "4", True),
        ("99999999999999999999", "4", True),
    ],
)
def test_the_bound_is_checked_and_capped_loudly(
    tmp_path: Path, value: str, expected: str, warned: bool
) -> None:
    run, _ = _run(tmp_path, COUNCIL_PARALLELISM=value, UNIVERSE="AAPL", SLEEP_AAPL="0")
    assert run.rc == 0, run.output
    assert ("WARNING: COUNCIL_PARALLELISM" in run.output) is warned
    if expected == "1":
        assert "--- councils" not in run.output
        assert _calls(run, "scripts.submit_plans") == []
    else:
        assert f"--- councils: parallelism={expected}," in run.output


# --- the first pass -----------------------------------------------------------


@pytest.mark.parametrize("bound", [2, 4])
def test_councils_overlap_within_the_bound(tmp_path: Path, bound: int) -> None:
    slow = {f"SLEEP_{t}": "1" for t in UNIVERSE.split()}
    run, hook_dir = _run(tmp_path, COUNCIL_PARALLELISM=str(bound), **slow)
    assert run.rc == 0, run.output
    peaks = _peaks(hook_dir)
    assert sorted(peaks) == sorted(UNIVERSE.split())
    assert max(peaks.values()) == bound, "the councils never overlapped up to the bound"


@pytest.mark.parametrize("bound", ["2", "4"])
@pytest.mark.parametrize("submit", ["0", "1"])
def test_no_council_can_send_only_the_submit_pass_carries_the_flag(
    tmp_path: Path, bound: str, submit: str
) -> None:
    run, _ = _run(tmp_path, COUNCIL_PARALLELISM=bound, SUBMIT=submit, SLEEP_AAPL="0")
    assert run.rc == 0, run.output
    councils = _calls(run, "scripts.trade")
    assert sorted(_opt(c, "--ticker") for c in councils) == sorted(UNIVERSE.split())
    run_ids = set()
    for argv in councils:
        assert "--submit" not in argv and "--hold" not in argv, argv
        assert _opt(argv, "--date") == RUN_DATE
        run_ids.add((_opt(argv, "--plan-dir"), _opt(argv, "--run-id")))
    [(plan_dir, run_id)] = run_ids
    assert Path(plan_dir).name == run_id
    assert Path(plan_dir).parent == tmp_path

    [submit_call] = _calls(run, "scripts.submit_plans")
    assert _opt(submit_call, "--plan-dir") == plan_dir
    assert _opt(submit_call, "--run-id") == run_id
    assert _opt(submit_call, "--date") == RUN_DATE
    assert ("--submit" in submit_call) is (submit == "1")
    assert _submit_tickers(submit_call) == UNIVERSE.split(), "not in universe order"


def test_council_output_is_merged_in_universe_order(tmp_path: Path) -> None:
    # The first ticker is the slowest, so it finishes last.
    run, _ = _run(tmp_path, COUNCIL_PARALLELISM="4", SLEEP_AAPL="2", SLEEP_MSFT="0")
    assert run.rc == 0, run.output
    headers = re.findall(r"^--- (\w+) @ ", run.output, flags=re.M)
    assert headers == UNIVERSE.split()
    for ticker in UNIVERSE.split():
        block = run.output.split(f"--- {ticker} @ {RUN_DATE} ---", 1)[1]
        assert block.lstrip().startswith(f"council output for {ticker}")
        assert f"-> {ticker} decided" in block.split("\n---", 1)[0]
    log = _run_log(tmp_path)
    for ticker in UNIVERSE.split():
        assert f"[council] {ticker} started" in log
        assert f"[council] {ticker} finished rc=0" in log


# --- the order of the run -------------------------------------------------------


def test_the_passes_sit_where_the_sequential_loop_sat(tmp_path: Path) -> None:
    run, hook_dir = _run(tmp_path, COUNCIL_PARALLELISM="3")
    assert run.rc == 0, run.output
    modules = run.modules()
    councils = [i for i, m in enumerate(modules) if m == "scripts.trade"]
    [submit] = [i for i, m in enumerate(modules) if m == "scripts.submit_plans"]
    assert modules.index("scripts.kill_check") < modules.index("scripts.manage_positions")
    assert modules.index("scripts.manage_positions") < min(councils)
    assert max(councils) < submit < modules.index("scripts.snapshot")
    assert modules.index("scripts.snapshot") < modules.index("scripts.inert_alert")
    assert modules.index("scripts.inert_alert") < modules.index("scripts.naked_alert")
    # The submit pass starts once every council has ended, and finds every record.
    assert (hook_dir / "submit.in_flight").read_text().strip() == "0"
    records = (hook_dir / "submit.records").read_text().split()
    assert {f"{t}.plan.json" for t in UNIVERSE.split()} <= set(records)
    assert run.ping_urls == [HC]


def test_the_records_go_when_the_run_ends(tmp_path: Path) -> None:
    run, _ = _run(tmp_path)
    assert run.rc == 0, run.output
    assert _calls(run, "scripts.submit_plans")
    assert _plan_dirs(tmp_path) == []


@pytest.mark.parametrize(
    ("kill_rc", "via_dotenv"),
    [("76", False), ("1", False), ("76", True), ("0", False)],
    ids=["flatten-all", "kill-check-failed", "flatten-all-from-dotenv", "full-run"],
)
def test_a_plan_dir_the_run_did_not_make_is_never_removed(
    tmp_path: Path, kill_rc: str, via_dotenv: bool
) -> None:
    # A generic name an operator's shell or agent/.env may already export. The
    # run only ever removes the directory it made itself, however it ends.
    keep = tmp_path / "operator-plans"
    keep.mkdir()
    (keep / "keep.txt").write_text("not the run's to delete", encoding="utf-8")
    env = {"UNIVERSE": "AAPL", "FAKE_RC_scripts_kill_check": kill_rc}
    if via_dotenv:
        run = run_daily(tmp_path, dotenv=f"PLAN_DIR={keep}\n", **env)
    else:
        run = run_daily(tmp_path, PLAN_DIR=str(keep), **env)
    assert (keep / "keep.txt").is_file(), run.output


# --- failures -------------------------------------------------------------------


def test_a_failed_or_timed_out_council_is_never_submitted(tmp_path: Path) -> None:
    # Both stand-ins write a record before failing: a council killed by its
    # timeout just after recording must not be sent.
    run, _ = _run(tmp_path, FAIL_MSFT="1", FAIL_NVDA="124")
    assert run.rc == 1
    [submit_call] = _calls(run, "scripts.submit_plans")
    assert _submit_tickers(submit_call) == ["AAPL", "GOOGL", "AMZN"]
    assert "-> MSFT FAILED (rc=1)" in run.output
    assert "-> NVDA TIMED OUT" in run.output
    assert "2 ticker(s) errored" in run.output
    [alert] = run.alerts("daily_run")
    assert alert["--body"].startswith("Failed: MSFT NVDA")
    assert run.ping_urls == [f"{HC}/fail"]


def test_a_council_the_gate_skipped_is_done_and_not_submitted(tmp_path: Path) -> None:
    run, _ = _run(tmp_path, NOPLAN_AMZN="1")
    assert run.rc == 0, run.output
    [submit_call] = _calls(run, "scripts.submit_plans")
    assert _submit_tickers(submit_call) == ["AAPL", "MSFT", "NVDA", "GOOGL"]
    assert "-> AMZN done" in run.output


def test_no_record_at_all_means_no_submit_pass(tmp_path: Path) -> None:
    skip_all = {f"NOPLAN_{t}": "1" for t in UNIVERSE.split()}
    run, _ = _run(tmp_path, **skip_all)
    assert run.rc == 0, run.output
    assert _calls(run, "scripts.submit_plans") == []
    assert "--- submit pass" not in run.output


def test_a_ticker_the_submit_pass_failed_fails_the_run(tmp_path: Path) -> None:
    run, _ = _run(tmp_path, SUBMIT_FAIL_NVDA="1", SUBMIT_FAIL_AMZN="1")
    assert run.rc == 1
    assert "2 ticker(s) errored" in run.output
    [alert] = run.alerts("daily_run")
    assert alert["--body"].startswith("Failed: NVDA AMZN")
    assert run.ping_urls == [f"{HC}/fail"]


@pytest.mark.parametrize("crash_rc", ["1", "3", "124"])
def test_a_submit_pass_that_dies_fails_the_run(tmp_path: Path, crash_rc: str) -> None:
    run, _ = _run(tmp_path, SUBMIT_CRASH=crash_rc)
    assert run.rc == 1
    assert f"submit pass FAILED (rc={crash_rc})" in run.output
    [alert] = run.alerts("daily_run")
    assert "submit-pass" in alert["--body"]
    assert run.ping_urls == [f"{HC}/fail"]


def _timeouts(path: Path) -> dict[str, list[str]]:
    """Module -> the durations `timeout` was given for it, in call order."""
    found: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        duration, *cmd = line.split()
        found.setdefault(cmd[cmd.index("-m") + 1], []).append(duration)
    return found


@pytest.mark.parametrize(
    ("ticker_timeout", "pass_timeout"),
    [("600", "2400"), ("0600", "2400"), ("30m", "30m")],
)
def test_the_submit_pass_has_a_ticker_timeout_for_each_ticker_it_sends(
    tmp_path: Path, ticker_timeout: str, pass_timeout: str
) -> None:
    # The sequential run gives every ticker its own TICKER_TIMEOUT_S. The submit
    # pass sends them all from one process: under a single one, a price feed slow
    # enough to take a few minutes per ticker (each well inside its own cap) cut
    # off every ticker after the first few, exits included.
    log = tmp_path / "timeouts"
    run, _ = _run(
        tmp_path, TICKER_TIMEOUT_S=ticker_timeout, TIMEOUT_LOG=str(log), NOPLAN_AMZN="1"
    )
    assert run.rc == 0, run.output
    [submit_call] = _calls(run, "scripts.submit_plans")
    assert len(_submit_tickers(submit_call)) == 4
    timeouts = _timeouts(log)
    assert timeouts["scripts.trade"] == [ticker_timeout] * 5
    assert timeouts["scripts.submit_plans"] == [pass_timeout]
    assert ("WARNING: TICKER_TIMEOUT_S" in run.output) is (ticker_timeout == "30m")


# --- PAUSE_NEW --------------------------------------------------------------------


@pytest.mark.parametrize("bound", ["2", "4"])
def test_pause_new_runs_neither_pass(tmp_path: Path, bound: str) -> None:
    run, hook_dir = _run(tmp_path, COUNCIL_PARALLELISM=bound, FAKE_RC_scripts_kill_check="75")
    assert run.rc == 0, run.output
    modules = run.modules()
    assert "scripts.trade" not in modules
    assert "scripts.submit_plans" not in modules
    assert not (hook_dir / "peaks").exists(), "a council started under PAUSE_NEW"
    assert "--- councils" not in run.output
    assert "scripts.manage_positions" in modules
    assert "scripts.naked_alert" in modules
    assert "PAUSE_NEW: no decisions, positions managed." in run.output
    assert _plan_dirs(tmp_path) == []
    assert run.ping_urls == [HC]


# --- a stop -----------------------------------------------------------------------


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(path: Path, count: int = 1) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if path.exists() and (path.is_file() or len(list(path.iterdir())) >= count):
            return
        time.sleep(0.1)
    raise AssertionError(f"{path} never appeared")


def test_sigterm_mid_council_stops_the_councils_sends_nothing_and_pages(tmp_path: Path) -> None:
    hook_dir = tmp_path / "hook"

    def terminate_mid_council(proc: subprocess.Popen[str]) -> None:
        _wait_for(hook_dir / "pids", count=2)
        time.sleep(0.3)  # let the stand-ins print their first line
        proc.send_signal(signal.SIGTERM)

    run, _ = _run(
        tmp_path, during=terminate_mid_council, COUNCIL_PARALLELISM="2",
        SLEEP_AAPL="60", SLEEP_MSFT="60",
    )
    assert run.rc == 143, run.output
    time.sleep(0.5)
    for pid_file in (hook_dir / "pids").iterdir():
        assert not _alive(int(pid_file.read_text())), f"{pid_file.name} still running"
    assert sorted(p.name for p in (hook_dir / "pids").iterdir()) == ["AAPL", "MSFT"]
    assert _calls(run, "scripts.submit_plans") == [], "a stopped run sent orders"
    assert "scripts.snapshot" not in run.modules()
    assert run.ping_urls and run.ping_urls[-1].endswith("/fail")
    log = _run_log(tmp_path)
    assert "SIGNAL received" in log
    assert "council output for AAPL" in log
    assert _plan_dirs(tmp_path) == [], "a stopped run's records outlived it"


def test_sigterm_mid_submit_pass_stops_it_and_pages(tmp_path: Path) -> None:
    hook_dir = tmp_path / "hook"

    def terminate_mid_submit(proc: subprocess.Popen[str]) -> None:
        _wait_for(hook_dir / "submit.pid")
        time.sleep(0.3)
        proc.send_signal(signal.SIGTERM)

    run, _ = _run(
        tmp_path, during=terminate_mid_submit, SUBMIT_SLEEP="60", UNIVERSE="AAPL MSFT",
        SLEEP_AAPL="0", SLEEP_MSFT="0",
    )
    assert run.rc == 143, run.output
    time.sleep(0.5)
    submit_pid = int((hook_dir / "submit.pid").read_text())
    assert not _alive(submit_pid), "the submit pass outlived the run"
    assert "scripts.snapshot" not in run.modules()
    assert run.ping_urls and run.ping_urls[-1].endswith("/fail")
    assert _plan_dirs(tmp_path) == []


# --- the handoff to the Python halves ----------------------------------------------
#
# The stand-ins accept any argv. A flag renamed on one side only would make
# every council exit 2 in argparse, or the submit pass refuse every ticker,
# while every test above stayed green. So the argv the script really built goes
# through each entry point's own parser, and the names and lines the script
# reads back are the ones the Python writes.


def test_the_argv_the_script_builds_is_one_each_half_accepts(tmp_path: Path) -> None:
    from scripts import submit_plans, trade

    run, _ = _run(tmp_path, SUBMIT="1", UNIVERSE="AAPL MSFT")
    assert run.rc == 0, run.output
    for argv in _calls(run, "scripts.trade"):
        args = trade.build_parser().parse_args(argv[1:])
        assert args.plan_dir and args.run_id and not args.submit and not args.hold
    [argv] = _calls(run, "scripts.submit_plans")
    opts = submit_plans.build_parser().parse_args(argv[1:])
    assert opts.submit and opts.tickers == ["AAPL", "MSFT"]
    assert opts.date.isoformat() == RUN_DATE


def test_the_record_the_script_looks_for_is_the_one_a_council_writes() -> None:
    from tradingagents_us.execution.plans import plan_path

    # daily_run.sh: [[ -f "$PLAN_DIR/$ticker.plan.json" ]]
    assert plan_path("/plans", "BRK.B") == Path("/plans/BRK.B.plan.json")


def test_the_failures_the_script_counts_are_the_lines_the_submit_pass_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import submit_plans, trade

    monkeypatch.setattr(trade, "_load_env", lambda: None)
    argv = [
        "--plan-dir", str(tmp_path), "--run-id", "x", "--date", RUN_DATE,
        "--db-url", f"sqlite:///{tmp_path / 'local.db'}", "AAPL", "BRK.B",
    ]
    assert submit_plans.main(argv) == 1  # no records: both fail before any broker call
    out = tmp_path / "submit.out"
    out.write_text(capsys.readouterr().out, encoding="utf-8")
    # The extraction daily_run.sh runs on the submit pass's output.
    sed = subprocess.run(
        ["sed", "-n", r"s/^  -> \([^ ]*\) FAILED .*/\1/p", str(out)],
        capture_output=True, text=True, check=True,
    )
    assert sed.stdout.split() == ["AAPL", "BRK.B"]
