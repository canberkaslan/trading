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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.github_fake import FakeGitHub

AGENT = Path(__file__).resolve().parent.parent
SCRIPT = AGENT / "scripts" / "daily_run.sh"
HC = "https://hc-ping.example/uuid-not-real"
RUN_DATE = "2026-09-23"  # a Wednesday: the weekend guard must not end the run

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# Stand-in interpreter. Records `-m <module> <args...>` one arg per line, and
# the alert-channel env it was started with; prints $FAKE_OUT_<module> and
# exits $FAKE_RC_<module> (dots in the module become _).
FAKE_PYTHON = r"""#!/usr/bin/env bash
mod="$2"
n=$(ls "$CALLS_DIR" | wc -l | tr -d ' ')
# The PID keeps names unique when the run starts tickers side by side.
name="$(printf '%04d' "$n")-$$"
printf '%s\n' "${@:2}" > "$CALLS_DIR/$name"
printf '%s\n' "OPS_ALERT_GITHUB_TOKEN=${OPS_ALERT_GITHUB_TOKEN:-}" \
  "OPS_ALERT_GITHUB_REPO=${OPS_ALERT_GITHUB_REPO:-}" \
  "COMMENTATOR_LIVE_AS_OF=${COMMENTATOR_LIVE_AS_OF:-}" \
  "TRADINGAGENTS_RUN_STATE_DIR=${TRADINGAGENTS_RUN_STATE_DIR:-}" > "$ENV_DIR/$name"
key="${mod//./_}"
out="FAKE_OUT_${key}"
rc="FAKE_RC_${key}"
if [[ -n "${!out:-}" ]]; then printf '%s\n' "${!out}"; fi
# A test-supplied stand-in for one trade (sleep, record, fail per ticker).
if [[ "$mod" == "scripts.trade" && -n "${FAKE_TRADE_HOOK:-}" ]]; then
  exec "$FAKE_TRADE_HOOK" "${@:3}"
fi
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
    #: The alert-channel env (and the commentator live anchor) each call in
    #: `calls` saw, index for index.
    envs: list[dict[str, str]]

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
    dotenv: str | None = None,
    during: Callable[[subprocess.Popen[str]], None] | None = None,
    **env_extra: str,
) -> Run:
    real_date = shutil.which("date")
    assert real_date is not None
    agent = tmp_path / "box" / "agent"
    (agent / "scripts").mkdir(parents=True)
    script = agent / "scripts" / "daily_run.sh"
    shutil.copy(SCRIPT, script)
    if dotenv is not None:
        # The scratch tree's own agent/.env, which the script sources. Only ever
        # test-written content: the real one could carry keys and an interpreter.
        (agent / ".env").write_text(dotenv, encoding="utf-8")

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
    env_dir = tmp_path / "envs"
    calls_dir.mkdir()
    curl_dir.mkdir()
    env_dir.mkdir()
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
        "ENV_DIR": str(env_dir),
        # One ticker at a time and no launch stagger unless a test asks: most
        # of these pin the order of the calls the run makes.
        "COUNCIL_PARALLELISM": "1",
        "COUNCIL_STAGGER_S": "0",
        "TMPDIR": str(tmp_path),
        **env_extra,
    }
    if healthcheck is not None:
        env["HEALTHCHECK_URL"] = healthcheck

    with subprocess.Popen(
        ["bash", str(script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    ) as popen:
        if during is not None:
            during(popen)
        out, err = popen.communicate(timeout=120)
    proc = subprocess.CompletedProcess(popen.args, popen.returncode, out, err)

    def read(d: Path) -> list[list[str]]:
        return [f.read_text(encoding="utf-8").splitlines() for f in sorted(d.iterdir())]

    envs = [dict(line.split("=", 1) for line in lines) for lines in read(env_dir)]
    return Run(proc.returncode, proc.stdout + proc.stderr, read(calls_dir), read(curl_dir), envs)


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


# --- position management -------------------------------------------------------
#
# manage_positions exits 3 when a time exit may have left shares with no stop
# (a cancel still on its way, a stop that could not be put back) and 1 on an
# ordinary failure. Both used to become an echo; the stop coverage check at the
# tail still sees a stop whose cancel has not landed yet, so nothing paged.


def test_a_possibly_unprotected_position_pages_and_the_run_carries_on(tmp_path: Path) -> None:
    run = run_daily(
        tmp_path,
        FAKE_RC_scripts_manage_positions="3",
        FAKE_OUT_scripts_manage_positions=(
            "XOM    FAILED time exit: unknown | cancel of stop-xom not confirmed\n"
            "UNCOVERED: shares may have no stop, now or once a pending cancel lands: XOM unknown"
        ),
    )

    (alert,) = run.alerts("position_pass")
    assert "no stop" in alert["--title"]
    assert "XOM unknown" in alert["--body"], "the page names the lot"
    assert "FAILED time exit" in alert["--body"]
    assert "scripts.trade" in run.modules(), "the decisions still run"
    assert run.rc == 0, run.output


def test_a_failed_position_pass_pages_too(tmp_path: Path) -> None:
    run = run_daily(tmp_path, FAKE_RC_scripts_manage_positions="1")

    (alert,) = run.alerts("position_pass")
    assert "rc=1" in alert["--title"]
    assert "scripts.trade" in run.modules()
    assert run.rc == 0, run.output


def test_the_position_pass_brings_the_held_names_bars_up_to_date(tmp_path: Path) -> None:
    # Nothing else in the run writes the bar cache, and a stale one leaves
    # every stop that moved past the mark band refused, night after night.
    run = run_daily(tmp_path)
    (call,) = [c for c in run.calls if c[0] == "scripts.manage_positions"]
    assert "--refresh-bars" in call[1:]


def test_a_clean_position_pass_pages_nobody(tmp_path: Path) -> None:
    run = run_daily(tmp_path)
    assert run.alerts("position_pass") == []
    assert "scripts.manage_positions" in run.modules()


# --- agent/.env against systemd's values ------------------------------------
#
# systemd loads secrets.env, then the script sources agent/.env, and a plain
# `source` overwrites. A blank `HEALTHCHECK_URL=` there (the example shipped one)
# switched the dead-man's switch and the GitHub half off while preflight, which
# never reads agent/.env, reported both as configured.


def _daily_run_alert_env(run: Run) -> dict[str, str]:
    (i,) = [
        n
        for n, c in enumerate(run.calls)
        if c[0] == "scripts.notify_ops" and "daily_run" in c
    ]
    return run.envs[i]


def test_a_blank_alert_key_in_dotenv_cannot_switch_alerting_off(tmp_path: Path) -> None:
    run = run_daily(
        tmp_path,
        dotenv="HEALTHCHECK_URL=\nOPS_ALERT_GITHUB_TOKEN=\nOPS_ALERT_GITHUB_REPO=canberkaslan/trading\n",
        OPS_ALERT_GITHUB_TOKEN="gh-test-token-not-real",
        OPS_ALERT_GITHUB_REPO="someone/private-ops",
        FAKE_RC_scripts_trade="1",
    )
    assert run.ping_urls == [f"{HC}/fail"]
    env = _daily_run_alert_env(run)
    assert env["OPS_ALERT_GITHUB_TOKEN"] == "gh-test-token-not-real"
    assert env["OPS_ALERT_GITHUB_REPO"] == "someone/private-ops"


def test_the_shipped_example_as_dotenv_leaves_alerting_on(tmp_path: Path) -> None:
    example = (AGENT / ".env.example").read_text(encoding="utf-8")
    run = run_daily(tmp_path, dotenv=example, FAKE_RC_scripts_trade="1")
    assert run.ping_urls == [f"{HC}/fail"]


def test_dotenv_still_fills_an_alert_key_the_environment_lacks(tmp_path: Path) -> None:
    # The manual-run fallback it was for.
    run = run_daily(tmp_path, healthcheck=None, dotenv=f"HEALTHCHECK_URL={HC}\n")
    assert run.ping_urls == [HC]


def test_every_other_key_keeps_dotenv_precedence(tmp_path: Path) -> None:
    # Pin, not a fix: which universe, submit flag and keys the run trades with
    # must not move in a change that only concerns alerting.
    run = run_daily(tmp_path, dotenv='UNIVERSE="XOM"\nSUBMIT=1\n')
    trades = [c for c in run.calls if c[0] == "scripts.trade"]
    assert trades == [["scripts.trade", "--ticker", "XOM", "--date", RUN_DATE, "--submit"]]


# --- the handoff to notify_ops ----------------------------------------------
#
# The stub interpreter records any argv, and the wrapper ends in `|| true`. A
# flag renamed or made required on one side only would make notify_ops exit 2
# in argparse, the wrapper would swallow it, and every daily_run, naked_book
# and kill_switch page would vanish while every test above stayed green. So the
# argv the script really built is handed to notify_ops' own entry point, and
# the alert has to come out the other end as an issue of the right kind.


def _deliver(argv: list[str], fake_github: FakeGitHub) -> dict[str, object]:
    from scripts import notify_ops

    before = len(fake_github.opened)
    assert notify_ops.main(argv) == 0
    assert len(fake_github.opened) == before + 1, fake_github.requests
    return fake_github.opened[-1]


@pytest.mark.parametrize(
    ("kind", "scenario"),
    [
        ("daily_run", {"FAKE_RC_scripts_trade": "1"}),
        ("kill_switch", {"FAKE_RC_scripts_kill_check": "1"}),
        (
            "naked_book",
            {
                "FAKE_RC_scripts_naked_alert": "3",
                "FAKE_OUT_scripts_naked_alert": "NAKED: 40 of 100 shares have no stop: AAPL",
            },
        ),
        ("naked_book", {"FAKE_RC_scripts_naked_alert": "1"}),
        ("position_pass", {"FAKE_RC_scripts_manage_positions": "3"}),
        ("position_pass", {"FAKE_RC_scripts_manage_positions": "1"}),
    ],
)
def test_every_page_the_run_raises_is_one_notify_ops_accepts(
    tmp_path: Path, fake_github: FakeGitHub, kind: str, scenario: dict[str, str]
) -> None:
    run = run_daily(tmp_path, **scenario)
    calls = [c for c in run.calls if c[0] == "scripts.notify_ops"]
    assert calls, run.output

    for call in calls:
        issue = _deliver(call[1:], fake_github)
        assert f"<!-- box-alert-kind:{kind} -->" in str(issue["body"])


def test_the_onfailure_unit_raises_a_page_notify_ops_accepts(fake_github: FakeGitHub) -> None:
    # ai-trader-alert.service is the page for a run that died where the script
    # could not report it. Its argv lives in a unit file no other test reads.
    import shlex

    unit = (AGENT.parent / "deploy" / "hetzner" / "ai-trader-alert.service").read_text()
    (exec_start,) = [ln for ln in unit.splitlines() if ln.startswith("ExecStart=")]
    argv = shlex.split(exec_start.removeprefix("ExecStart="))
    argv = argv[argv.index("scripts.notify_ops") + 1 :]

    issue = _deliver(argv, fake_github)
    assert "<!-- box-alert-kind:unit_failed -->" in str(issue["body"])
