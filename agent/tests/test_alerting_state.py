"""The preflight record the API serves: what may be read back, and what counts as a failure."""

from __future__ import annotations

from pathlib import Path

from tradingagents_us.monitoring.alerting_state import (
    PreflightRecord,
    alerting_config,
    read_preflight,
    write_preflight,
)


def test_a_record_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    record = PreflightRecord.from_names("2026-09-14T21:45:07+00:00", ["polygon"], ["healthcheck"])
    write_preflight(record, path)
    assert read_preflight(path) == record
    assert read_preflight(path).public()["ok"] is False  # type: ignore[union-attr]


def test_missing_or_corrupt_is_unknown_not_failed(tmp_path: Path) -> None:
    assert read_preflight(tmp_path / "absent.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert read_preflight(bad) is None
    bad.write_text('{"ok": false}')  # no timestamp: cannot be placed in time
    assert read_preflight(bad) is None


def test_a_failure_under_an_odd_name_is_still_a_failure(tmp_path: Path) -> None:
    # Only clean names are published; an entry that is not one must not turn a
    # failed run into a passing one on the way out.
    path = tmp_path / "s.json"
    path.write_text('{"at": "2026-09-14T21:45:07Z", "failed": ["alpaca: 401 AKIA"], '
                    '"alerting_gaps": []}')
    record = read_preflight(path)
    assert record is not None
    assert record.failed == ("unnamed",)
    assert not record.ok


def test_alerting_config_is_presence_and_shape_only() -> None:
    assert alerting_config({}) == {"healthcheck": False, "github": False}
    assert alerting_config(
        {"HEALTHCHECK_URL": "hc-ping.com/x", "OPS_ALERT_GITHUB_TOKEN": "  "}
    ) == {"healthcheck": False, "github": False}
    assert alerting_config(
        {"HEALTHCHECK_URL": "https://hc-ping.com/x", "OPS_ALERT_GITHUB_TOKEN": "t"}
    ) == {"healthcheck": True, "github": True}
