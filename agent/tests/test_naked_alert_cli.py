"""scripts.naked_alert's exit code is the signal daily_run.sh acts on.

It used to exit 0 on every path and ran behind `|| true`, so a book holding
shares with no protective stop was a log line nobody read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import naked_alert as cli
from tradingagents_us.notifications.naked_alert import CoverageFacts
from tradingagents_us.notifications.ops_channel import ChannelResult, Delivery


class Sent:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, title: str, body: str, kind: str = "ops") -> Delivery:
        self.calls.append((title, body, kind))
        return Delivery((ChannelResult("github", self.delivered, "stub"),))


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Sent:
    """Scratch state + kill-switch files, and a recorder in place of the channel."""
    monkeypatch.setenv("NAKED_ALERT_STATE_PATH", str(tmp_path / "naked.state.json"))
    monkeypatch.setenv("KILL_SWITCH_PATH", str(tmp_path / "kill_switch.state"))
    monkeypatch.delenv("KILL_SWITCH_FILE", raising=False)
    sent = Sent()
    monkeypatch.setattr(cli, "send_ops_alert", sent)
    return sent


def _book(monkeypatch: pytest.MonkeyPatch, naked: float, total: float = 100.0, **kw) -> None:
    facts = CoverageFacts(
        total_qty=total,
        naked_qty=naked,
        indeterminate_qty=kw.pop("indet", 0.0),
        naked_symbols=kw.pop("symbols", ()),
        run_date=kw.pop("run_date", "2026-09-23"),
    )
    monkeypatch.setattr(cli, "collect_facts", lambda: facts)


def test_a_naked_book_exits_non_zero_and_names_the_exposure(
    box: Sent, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _book(monkeypatch, naked=40.0, symbols=("AAPL", "META"))
    assert cli.main([]) == cli.EXIT_NAKED
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.startswith("NAKED: 40 of 100 shares")
    assert "AAPL, META" in last
    # The run pages about this; paging here too would page twice per run.
    assert box.calls == []


def test_one_naked_share_is_enough(box: Sent, monkeypatch: pytest.MonkeyPatch) -> None:
    # Below the old 10% paging threshold, which let small exposures pass as
    # clean. At run time, after the stop back-fill, a naked share is a failure.
    _book(monkeypatch, naked=1.0, total=500.0, symbols=("JPM",))
    assert cli.main([]) == cli.EXIT_NAKED


def test_a_covered_book_exits_zero(box: Sent, monkeypatch: pytest.MonkeyPatch) -> None:
    _book(monkeypatch, naked=0.0)
    assert cli.main([]) == cli.EXIT_COVERED
    assert box.calls == []


def test_an_empty_book_exits_zero(box: Sent, monkeypatch: pytest.MonkeyPatch) -> None:
    _book(monkeypatch, naked=0.0, total=0.0)
    assert cli.main([]) == cli.EXIT_COVERED


def test_a_deliberate_flatten_is_not_an_exposure(
    box: Sent, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "kill_switch.state").write_text("FLATTEN_ALL")
    _book(monkeypatch, naked=90.0)
    assert cli.main([]) == cli.EXIT_COVERED


def test_a_check_that_cannot_run_is_not_reported_as_covered(
    box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A refused broker key made this raise every night, and it exited 0.
    def refused() -> CoverageFacts:
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(cli, "collect_facts", refused)
    assert cli.main([]) == cli.EXIT_CHECK_FAILED


def test_the_all_clear_goes_out_once_after_a_naked_run(
    box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    _book(monkeypatch, naked=40.0)
    assert cli.main([]) == cli.EXIT_NAKED

    _book(monkeypatch, naked=0.0)
    assert cli.main([]) == cli.EXIT_COVERED
    assert len(box.calls) == 1
    assert "restored" in box.calls[0][0]
    assert box.calls[0][2] == "naked_book"

    assert cli.main([]) == cli.EXIT_COVERED
    assert len(box.calls) == 1


def test_an_undelivered_all_clear_is_retried(box: Sent, monkeypatch: pytest.MonkeyPatch) -> None:
    _book(monkeypatch, naked=40.0)
    cli.main([])
    box.delivered = False
    _book(monkeypatch, naked=0.0)
    cli.main([])
    box.delivered = True
    cli.main([])
    assert len(box.calls) == 2


def test_an_undelivered_all_clear_is_dropped_after_a_week(
    box: Sent, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The live box has no channel configured: the all-clear for one naked
    # night went to nobody on every run after it, and would have opened the
    # first channel ever configured with a recovery from weeks before.
    _book(monkeypatch, naked=32.0, total=278.0, run_date="2026-10-06")
    assert cli.main([]) == cli.EXIT_NAKED
    box.delivered = False

    _book(monkeypatch, naked=0.0, total=246.0, run_date="2026-10-09")
    assert cli.main([]) == cli.EXIT_COVERED
    assert "Last seen naked on 2026-10-06 (12% of the book, 3 days ago)." in box.calls[-1][1]
    assert "no longer retried" not in capsys.readouterr().out

    _book(monkeypatch, naked=0.0, total=246.0, run_date="2026-10-13")
    assert cli.main([]) == cli.EXIT_COVERED
    assert len(box.calls) == 2
    assert (
        "all-clear for the 2026-10-06 exposure reached no channel in 7 days; no longer retried"
        in capsys.readouterr().out
    )

    _book(monkeypatch, naked=0.0, total=246.0, run_date="2026-10-14")
    assert cli.main([]) == cli.EXIT_COVERED
    assert len(box.calls) == 2


def test_an_expired_all_clear_does_not_mute_the_next_exposure(
    box: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    _book(monkeypatch, naked=32.0, run_date="2026-10-06")
    cli.main([])
    box.delivered = False
    _book(monkeypatch, naked=0.0, run_date="2026-10-20")
    cli.main([])

    _book(monkeypatch, naked=10.0, run_date="2026-10-21")
    assert cli.main([]) == cli.EXIT_NAKED
    box.delivered = True
    _book(monkeypatch, naked=0.0, run_date="2026-10-22")
    assert cli.main([]) == cli.EXIT_COVERED
    assert "Last seen naked on 2026-10-21" in box.calls[-1][1]


def test_dry_run_records_nothing(
    box: Sent, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _book(monkeypatch, naked=40.0)
    assert cli.main(["--dry-run"]) == cli.EXIT_NAKED
    assert not (tmp_path / "naked.state.json").exists()
