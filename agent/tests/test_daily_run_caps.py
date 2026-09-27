"""No cap env var reaches scripts.trade: the daily run's argv is today's, whatever the box holds.

MAX_POSITION_PCT and MAX_SECTOR_PCT sat uncommented in .env.example for months
with nothing reading them, so any agent/.env built from it carries them, and
nobody has looked at what values a box actually holds. Forwarding them would
turn a setting that did nothing into one that resizes orders, and a value
trade.py's parser refuses (`10`, `0.10 # note`) into an argparse exit before
every ticker's council, which is a whole day with no orders. Either changes
what reaches the broker, so this tranche does not forward them; wiring a cap to
an env var belongs in a change that means to move order flow, after the box has
been checked.

These run the real script under the recording stubs of test_daily_run_alerting
and pin each ticker's trade invocation to the exact argv origin/main builds.
The flags themselves stay on trade.py (see test_trade_cli), where nothing can
reach them without one being typed.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from tests.test_daily_run_alerting import RUN_DATE, Run, run_daily

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

UNIVERSE = ("AAPL", "MSFT")  # what run_daily sets
CAP_VARS = ("MAX_POSITION_PCT", "MAX_SECTOR_PCT", "MAX_CASH_UTILIZATION")

#: What origin/main's .env.example shipped uncommented, so what a box .env copied
#: from it holds today.
OLD_EXAMPLE_RISK_BLOCK = (
    "# Risk\n"
    "MAX_DAILY_DRAWDOWN=0.03\n"
    "MAX_POSITION_PCT=0.10\n"
    "MAX_SECTOR_PCT=0.30\n"
    "KILL_SWITCH_POLL_INTERVAL_SECONDS=5\n"
)


def _todays_argv(ticker: str, *, submit: bool) -> list[str]:
    """The trade invocation exactly as origin/main's daily_run.sh builds it."""
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


@pytest.mark.parametrize(
    "value",
    [
        "0.10",  # the old example's value: neutral only if parsed, still not sent
        "0.05",  # a hand-tuned value that did nothing until now
        "10",  # "ten percent": trade.py would refuse it and fail every ticker
        "0.10 # single name",  # systemd keeps inline comments in the value
        "",  # blank
    ],
)
def test_a_cap_in_the_environment_never_reaches_trade(tmp_path: Path, value: str) -> None:
    # systemd's EnvironmentFile=/opt/ai-trader/secrets.env is where these land.
    run = run_daily(tmp_path, SUBMIT="1", **dict.fromkeys(CAP_VARS, value))
    assert run.rc == 0, run.output
    assert _trade_argvs(run) == [_todays_argv(t, submit=True) for t in UNIVERSE]


def test_a_dotenv_copied_from_the_old_example_changes_nothing(tmp_path: Path) -> None:
    # agent/.env is untracked, survives install.sh's `git reset --hard`, and is
    # sourced by the script after systemd has loaded secrets.env.
    run = run_daily(tmp_path, SUBMIT="1", dotenv=OLD_EXAMPLE_RISK_BLOCK)
    assert run.rc == 0, run.output
    assert _trade_argvs(run) == [_todays_argv(t, submit=True) for t in UNIVERSE]


def test_a_hand_edited_dotenv_cap_changes_nothing(tmp_path: Path) -> None:
    run = run_daily(tmp_path, dotenv="MAX_POSITION_PCT=0.05\nMAX_CASH_UTILIZATION=0.5\n")
    assert run.rc == 0, run.output
    assert _trade_argvs(run) == [_todays_argv(t, submit=False) for t in UNIVERSE]


def test_what_trade_receives_parses_to_the_default_limits(tmp_path: Path) -> None:
    # Handed to trade.py's own parser, the argv the run really built yields the
    # same caps trade.py used before the flags existed, whatever the env says.
    from scripts.trade import build_parser
    from tradingagents_us.risk.portfolio_limits import PortfolioLimits

    run = run_daily(tmp_path, **dict.fromkeys(CAP_VARS, "0.05"))
    for argv in _trade_argvs(run):
        args = build_parser().parse_args(argv[1:])  # drop the module name
        limits = PortfolioLimits(
            max_position_pct=args.max_position_pct,
            max_sector_pct=args.max_sector_pct,
            max_cash_utilization=args.max_cash_utilization,
        )
        # origin/main built PortfolioLimits(max_position_pct=0.10) from the
        # flag default and left the rest at the dataclass defaults.
        assert limits == PortfolioLimits(max_position_pct=0.10)


def test_env_example_lists_no_risk_control_that_nothing_reads() -> None:
    # MAX_POSITION_PCT, MAX_SECTOR_PCT, MAX_DAILY_DRAWDOWN and
    # KILL_SWITCH_POLL_INTERVAL_SECONDS all sat in the example looking like
    # controls, and a repo-wide grep found no reader for any of them. Every
    # risk-shaped key the example offers, commented or not, must have one.
    agent = Path(__file__).resolve().parent.parent
    example = (agent / ".env.example").read_text(encoding="utf-8")
    key_line = re.compile(r"^#?([A-Z][A-Z0-9_]*)=", re.MULTILINE)
    risk_key = re.compile(r"^MAX_|_PCT$|_UTILIZATION$|DRAWDOWN|POLL_INTERVAL")
    # The filter itself still recognises the old block, commented or not.
    old = key_line.findall(OLD_EXAMPLE_RISK_BLOCK + "#MAX_CASH_UTILIZATION=1.0\n")
    assert [k for k in old if risk_key.search(k)] == [
        "MAX_DAILY_DRAWDOWN",
        "MAX_POSITION_PCT",
        "MAX_SECTOR_PCT",
        "KILL_SWITCH_POLL_INTERVAL_SECONDS",
        "MAX_CASH_UTILIZATION",
    ]

    risk_keys = [k for k in key_line.findall(example) if risk_key.search(k)]
    sources = [agent / "scripts" / "daily_run.sh"]
    for pkg in ("scripts", "tradingagents_us", "api"):
        sources += sorted((agent / pkg).rglob("*.py"))
    code = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    unread = [
        k for k in risk_keys
        if not re.search(rf"\$\{{{k}\b|\${k}\b|[\"']{k}[\"']", code)
    ]
    assert unread == [], f"{unread} look like controls in .env.example and nothing reads them"
