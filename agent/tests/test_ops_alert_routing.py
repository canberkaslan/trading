"""Every box-side alerter goes through the ops channel, not straight to push.

Tested through each script's own entry point, so what is proven is that the
alert reaches GitHub from where it is raised, not only that the channel works
when called directly.
"""

from __future__ import annotations

import pytest

from tests.github_fake import FakeGitHub


def test_notify_ops_reaches_github_with_no_registered_device(fake_github: FakeGitHub) -> None:
    # It used to print "no registered devices" and return, which is what every
    # daily-run failure, OnFailure= page and naked-book alert did while the app
    # was being rebuilt.
    from scripts import notify_ops

    rc = notify_ops.main(
        ["--kind", "daily_run", "--title", "Daily run: 11 ticker(s) failed", "--body", "SPY AAPL"]
    )

    assert rc == 0
    assert len(fake_github.opened) == 1
    assert "11 ticker(s) failed" in str(fake_github.opened[0]["title"])
    assert "<!-- box-alert-kind:daily_run -->" in str(fake_github.opened[0]["body"])


def test_notify_ops_exits_zero_when_every_channel_fails(fake_github: FakeGitHub) -> None:
    # It is called from `||` chains; a failed alert must not replace the exit
    # code of the failure it was reporting.
    import httpx

    from scripts import notify_ops

    fake_github.error = httpx.ConnectError("down")
    assert notify_ops.main(["--title", "t"]) == 0


def test_inert_alert_counts_a_github_delivery_as_delivered(fake_github: FakeGitHub) -> None:
    # Its state is written only after delivery. With no device, push-only
    # delivery was False forever, so the freeze could never be recorded as told.
    from scripts import inert_alert

    delivered, detail = inert_alert._send("Order flow inert", "3 run days", "inert")
    assert delivered, detail
    assert "[inert] 3 run days" in str(fake_github.opened[0]["body"])


@pytest.mark.parametrize(
    ("module", "call", "kind"),
    [
        ("kill_check", lambda m: m._notify("FLATTEN_ALL PARTIAL", "NVDA open"), "kill_switch"),
        ("backup", lambda m: m._alert("s3: AccessDenied"), "backup"),
    ],
)
def test_other_box_alerters_reach_github(
    fake_github: FakeGitHub, module: str, call, kind: str
) -> None:
    import importlib

    call(importlib.import_module(f"scripts.{module}"))
    assert len(fake_github.opened) == 1
    assert f"<!-- box-alert-kind:{kind} -->" in str(fake_github.opened[0]["body"])
