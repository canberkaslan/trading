"""daily_run.sh's alert wiring, proven by running the real script.

A unit test of naked_alert or notify_ops proves they work when called. It cannot
prove the run calls them, or does anything with what they return; the naked-book
check sat behind `|| true` with a correct checker on the other side of it.

The script is copied into a scratch tree, so its `cd "$(dirname "$0")/.."`
lands somewhere with no .env. Sourcing a real one could swap in a real
interpreter and real keys. Every command it reaches for (python, curl, timeout,
date) is a recording stub first on PATH, so nothing leaves the machine.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parent.parent
SCRIPT = AGENT / "scripts" / "daily_run.sh"
HC = "https://hc-ping.example/uuid-not-real"
RUN_DATE = "2026-09-23"  # a Wednesday: the weekend guard must not end the run

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# Stand-in interpreter. Records `-m <module> <args...>` one arg per line, prints
# $FAKE_OUT_<module> and exits $FAKE_RC_<module> (dots in the module become _).
FAKE_PYTHON = r"""#!/usr/bin/env bash
mod="$2"
n=$(ls "$CALLS_DIR" | wc -l | tr -d ' ')
printf '%s\n' "${@:2}" > "$CALLS_DIR/$(printf '%04d' "$n")"
key="${mod//./_}"
out="FAKE_OUT_${key}"
rc="FAKE_RC_${key}"
if [[ -n "${!out:-}" ]]; then printf '%s\n' "${!out}"; fi
exit "${!rc:-0}"
"""

FAKE_CURL = r"""#!/usr/bin/env bash
n=$(ls "$CURL_DIR" | wc -l | tr -d ' ')
printf '%s\n' "$@" > "$CURL_DIR/$(printf '%04d' "$n")"
exit "${FAKE_CURL_RC:-0}"
"""

# `timeout -k 30 1800 cmd...` -> `cmd...`
FAKE_TIMEOUT = r"""#!/usr/bin/env bash
while [[ "$1" == -* ]]; do
  case "$1" in -k|-s) shift 2 ;; *) shift ;; esac
done
shift
exec "$@"
"""

FAKE_DATE = r"""#!/usr/bin/env bash
case "$*" in
  *%u*) echo 3 ;;
  *"+%F") echo __RUN_DATE__ ;;
  *) exec __REAL_DATE__ "$@" ;;
esac
"""


@dataclass
class Run:
    rc: int
    output: str
    calls: list[list[str]]
    pings: list[list[str]]

    def modules(self) -> list[str]:
        return [c[0] for c in self.calls]

    def alerts(self, kind: str) -> list[dict[str, str]]:
        found = []
        for c in self.calls:
            if c[0] != "scripts.notify_ops":
                continue
            opts = dict(zip(c[1::2], c[2::2], strict=False))
            if opts.get("--kind") == kind:
                found.append(opts)
        return found

    @property
    def ping_urls(self) -> list[str]:
        return [argv[-1] for argv in self.pings]


def _stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run_daily(
    tmp_path: Path,
    healthcheck: str | None = HC,
    precreate_log_dir_as_file: bool = False,
    **env_extra: str,
) -> Run:
    real_date = shutil.which("date")
    assert real_date is not None
    agent = tmp_path / "box" / "agent"
    (agent / "scripts").mkdir(parents=True)
    script = agent / "scripts" / "daily_run.sh"
    shutil.copy(SCRIPT, script)

    stubs = tmp_path / "bin"
    stubs.mkdir()
    _stub(stubs / "python", FAKE_PYTHON)
    _stub(stubs / "curl", FAKE_CURL)
    _stub(stubs / "timeout", FAKE_TIMEOUT)
    _stub(
        stubs / "date",
        FAKE_DATE.replace("__RUN_DATE__", RUN_DATE).replace("__REAL_DATE__", real_date),
    )
    calls_dir = tmp_path / "calls"
    curl_dir = tmp_path / "curls"
    calls_dir.mkdir()
    curl_dir.mkdir()
    logs = tmp_path / "logs"
    if precreate_log_dir_as_file:
        # The run log's path is taken by a directory: the first write fails,
        # and `set -e` ends the run before it reaches any outcome of its own.
        (logs / f"daily_{RUN_DATE}.log").mkdir(parents=True)

    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PYTHON": str(stubs / "python"),
        "LOG_DIR": str(logs),
        "UNIVERSE": "AAPL MSFT",
        "SUBMIT": "0",
        "CALLS_DIR": str(calls_dir),
        "CURL_DIR": str(curl_dir),
        **env_extra,
    }
    if healthcheck is not None:
        env["HEALTHCHECK_URL"] = healthcheck

    proc = subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )

    def read(d: Path) -> list[list[str]]:
        return [f.read_text(encoding="utf-8").splitlines() for f in sorted(d.iterdir())]

    return Run(proc.returncode, proc.stdout + proc.stderr, read(calls_dir), read(curl_dir))


# --- dead-man's switch ------------------------------------------------------


def test_a_clean_run_pings_the_check_once(tmp_path: Path) -> None:
    run = run_daily(tmp_path)
    assert run.rc == 0, run.output
    assert run.ping_urls == [HC]
    assert "scripts.trade" in run.modules()


def test_failed_tickers_ping_fail(tmp_path: Path) -> None:
    # The revoked-key shape: every ticker aborts at its first broker call.
    run = run_daily(tmp_path, FAKE_RC_scripts_trade="1")
    assert run.rc == 1, run.output
    assert run.ping_urls == [f"{HC}/fail"]
    assert run.alerts("daily_run"), run.calls


def test_a_failed_kill_check_pings_fail_and_trades_nothing(tmp_path: Path) -> None:
    run = run_daily(tmp_path, FAKE_RC_scripts_kill_check="1")
    assert run.ping_urls == [f"{HC}/fail"]
    assert "scripts.trade" not in run.modules()
    assert run.alerts("kill_switch")


def test_a_run_that_dies_before_any_outcome_pings_fail(tmp_path: Path) -> None:
    run = run_daily(tmp_path, precreate_log_dir_as_file=True)
    assert run.rc != 0
    assert run.ping_urls == [f"{HC}/fail"]


def test_a_trailing_slash_on_the_url_is_tolerated(tmp_path: Path) -> None:
    run = run_daily(tmp_path, healthcheck=f"{HC}/", FAKE_RC_scripts_trade="1")
    assert run.ping_urls == [f"{HC}/fail"]


def test_a_failed_ping_never_fails_the_run(tmp_path: Path) -> None:
    run = run_daily(tmp_path, FAKE_CURL_RC="7")
    assert run.rc == 0, run.output
    assert "healthcheck ping failed (non-fatal)" in run.output


def test_an_unset_url_pings_nothing_and_says_so(tmp_path: Path) -> None:
    run = run_daily(tmp_path, healthcheck=None)
    assert run.rc == 0, run.output
    assert run.pings == []
    assert "HEALTHCHECK_URL unset" in run.output


# --- naked-book check ---------------------------------------------------------


def test_a_naked_book_reaches_the_alert_channel_and_the_run_carries_on(tmp_path: Path) -> None:
    run = run_daily(
        tmp_path,
        FAKE_RC_scripts_naked_alert="3",
        FAKE_OUT_scripts_naked_alert=(
            "stop coverage: 40/100 naked (40.0%), indeterminate 0, ks=RUN\n"
            "NAKED: 40 of 100 shares (40.0%) have no protective stop: AAPL, META"
        ),
    )
    alerts = run.alerts("naked_book")
    assert len(alerts) == 1, run.calls
    assert "no protective stop" in alerts[0]["--title"]
    assert "40 of 100 shares" in alerts[0]["--body"]
    assert "AAPL, META" in alerts[0]["--body"]
    # Not aborted: the run still completes and reports its own outcome.
    assert run.rc == 0, run.output
    assert "Daily run complete" in run.output
    assert run.ping_urls == [HC]


def test_a_coverage_check_that_could_not_run_is_surfaced_too(tmp_path: Path) -> None:
    run = run_daily(tmp_path, FAKE_RC_scripts_naked_alert="1")
    alerts = run.alerts("naked_book")
    assert len(alerts) == 1, run.calls
    assert "Stop-coverage check failed" in alerts[0]["--title"]
    assert run.rc == 0


def test_a_covered_book_pages_nobody(tmp_path: Path) -> None:
    run = run_daily(tmp_path)
    assert run.alerts("naked_book") == []
    assert "scripts.naked_alert" in run.modules()
