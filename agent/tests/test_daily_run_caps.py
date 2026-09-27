"""daily_run.sh forwards the cap env vars to scripts.trade, and only when set.

MAX_POSITION_PCT sat in .env for months with nothing reading it: the live cap was
trade.py's flag default, and the run never passed the flag. Editing the file
changed nothing, and nothing said so. These tests run the real script (under the
recording stubs of test_daily_run_alerting) and read the argv each ticker's
trade process was actually given, then hand that argv to trade.py's own parser,
so a flag name the two sides spell differently cannot pass.

The unset case is pinned to the exact argv of the run before the vars were
wired: with nothing set, the defaults in trade.py stay the only source of the
caps, and a default changed there cannot be shadowed by a stale copy here.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from tests.test_daily_run_alerting import RUN_DATE, Run, run_daily

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

UNIVERSE = ("AAPL", "MSFT")  # what run_daily sets


def _todays_argv(ticker: str, *, submit: bool) -> list[str]:
    """The trade invocation as daily_run.sh built it before the caps were wired."""
    argv = ["scripts.trade", "--ticker", ticker, "--date", RUN_DATE]
    if submit:
        argv.append("--submit")
    return argv


def _trade_argvs(run: Run) -> list[list[str]]:
    return [c for c in run.calls if c[0] == "scripts.trade"]


@pytest.mark.parametrize("submit", [False, True])
def test_with_the_caps_unset_the_invocation_is_exactly_todays(
    tmp_path: Path, submit: bool
) -> None:
    run = run_daily(tmp_path, SUBMIT="1" if submit else "0")
    assert run.rc == 0, run.output
    assert _trade_argvs(run) == [_todays_argv(t, submit=submit) for t in UNIVERSE]


def test_a_blank_cap_counts_as_unset(tmp_path: Path) -> None:
    # `MAX_POSITION_PCT=` in an env file is a blank, not a value. Forwarded, it
    # would be `--max-position-pct ''` and every ticker would die in argparse.
    run = run_daily(tmp_path, MAX_POSITION_PCT="", MAX_SECTOR_PCT="", MAX_CASH_UTILIZATION="")
    assert run.rc == 0, run.output
    assert _trade_argvs(run) == [_todays_argv(t, submit=False) for t in UNIVERSE]


def test_set_caps_are_forwarded_to_every_ticker(tmp_path: Path) -> None:
    run = run_daily(
        tmp_path,
        SUBMIT="1",
        MAX_POSITION_PCT="0.05",
        MAX_SECTOR_PCT="0.25",
        MAX_CASH_UTILIZATION="0.9",
    )
    assert run.rc == 0, run.output
    caps = [
        "--max-position-pct", "0.05",
        "--max-sector-pct", "0.25",
        "--max-cash-utilization", "0.9",
    ]
    assert _trade_argvs(run) == [_todays_argv(t, submit=True) + caps for t in UNIVERSE]
    # And the run log says which caps were in force, so a tuned value is visible.
    assert "--max-position-pct 0.05" in run.output


@pytest.mark.parametrize(
    ("var", "flag"),
    [
        ("MAX_POSITION_PCT", "--max-position-pct"),
        ("MAX_SECTOR_PCT", "--max-sector-pct"),
        ("MAX_CASH_UTILIZATION", "--max-cash-utilization"),
    ],
)
def test_each_cap_is_forwarded_on_its_own(tmp_path: Path, var: str, flag: str) -> None:
    run = run_daily(tmp_path, **{var: "0.07"})
    assert run.rc == 0, run.output
    assert _trade_argvs(run) == [_todays_argv(t, submit=False) + [flag, "0.07"] for t in UNIVERSE]


def test_a_cap_tuned_in_dotenv_reaches_trade(tmp_path: Path) -> None:
    # The path the dead config was on: someone edits agent/.env, the script
    # sources it, and the value has to come out the other end as a flag.
    run = run_daily(tmp_path, dotenv="MAX_POSITION_PCT=0.08\nMAX_CASH_UTILIZATION=0.5\n")
    assert run.rc == 0, run.output
    for argv in _trade_argvs(run):
        assert argv[-4:] == ["--max-position-pct", "0.08", "--max-cash-utilization", "0.5"]


def test_the_forwarded_flags_are_ones_trade_py_accepts(tmp_path: Path) -> None:
    # The stub interpreter records any argv, so a flag spelled differently here
    # and in trade.py would pass every test above and fail every ticker on the
    # box. Parse what the run really handed over with trade.py's own parser.
    run = run_daily(
        tmp_path,
        MAX_POSITION_PCT="0.05",
        MAX_SECTOR_PCT="0.25",
        MAX_CASH_UTILIZATION="0.9",
    )
    from scripts.trade import build_parser

    argv = _trade_argvs(run)[0][1:]  # drop the module name
    args = build_parser().parse_args(argv)
    assert args.max_position_pct == pytest.approx(0.05)
    assert args.max_sector_pct == pytest.approx(0.25)
    assert args.max_cash_utilization == pytest.approx(0.9)


def test_env_example_lists_no_risk_control_that_nothing_reads() -> None:
    # MAX_POSITION_PCT, MAX_SECTOR_PCT, MAX_DAILY_DRAWDOWN and
    # KILL_SWITCH_POLL_INTERVAL_SECONDS all sat in the example looking like
    # controls, and a repo-wide grep found no reader for any of them. Every
    # risk-shaped key the example offers, commented or not, must have one.
    agent = Path(__file__).resolve().parent.parent
    example = (agent / ".env.example").read_text(encoding="utf-8")
    keys = re.findall(r"^#?([A-Z][A-Z0-9_]*)=", example, re.MULTILINE)
    risk_keys = [
        k for k in keys if re.search(r"^MAX_|_PCT$|_UTILIZATION$|DRAWDOWN|POLL_INTERVAL", k)
    ]
    assert "MAX_POSITION_PCT" in risk_keys  # the filter still sees the caps

    sources = [agent / "scripts" / "daily_run.sh"]
    for pkg in ("scripts", "tradingagents_us", "api"):
        sources += sorted((agent / pkg).rglob("*.py"))
    code = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    unread = [
        k for k in risk_keys
        if not re.search(rf"\$\{{{k}\b|\${k}\b|[\"']{k}[\"']", code)
    ]
    assert unread == [], f"{unread} look like controls in .env.example and nothing reads them"
