"""Polymarket fails fast: short timeouts, one bounded retry, a run-wide breaker.

HTTP is mocked at `requests.get`; nothing here reaches the network, and no
test sleeps for real.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest
import requests

_VENDOR = Path(__file__).resolve().parent.parent / "vendor" / "tradingagents"
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

from tradingagents.dataflows import polymarket as vendor  # noqa: E402

from tradingagents_us.dataflows import fail_fast, polymarket_guard  # noqa: E402

_MARKETS = {
    "events": [
        {
            "markets": [
                {
                    "question": "Fed cut in December?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.61", "0.39"]',
                    "volumeNum": 1_000_000,
                    "endDate": "2099-12-31T00:00:00Z",
                    "closed": False,
                }
            ]
        }
    ]
}


class _Resp:
    def __init__(self, status: int, body: Any = None, headers: dict[str, str] | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)  # type: ignore[arg-type]

    def json(self) -> Any:
        return self._body


class _Http:
    """Scripted `requests.get`: pops one outcome per call, repeats the last."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, params: dict[str, Any], timeout: Any) -> _Resp:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(polymarket_guard, "_sleep", slept.append)
    return slept


@pytest.fixture(autouse=True)
def _guarded(monkeypatch: pytest.MonkeyPatch):
    polymarket_guard.BREAKER.reset()
    monkeypatch.setattr(vendor, "_request", vendor._request)  # undone after the test
    assert polymarket_guard.install()
    yield
    polymarket_guard.BREAKER.reset()


def _http(monkeypatch: pytest.MonkeyPatch, *outcomes: Any) -> _Http:
    fake = _Http(*outcomes)
    monkeypatch.setattr(polymarket_guard.requests, "get", fake)
    return fake


def test_install_routes_the_vendor_and_is_idempotent() -> None:
    first = vendor._request
    assert polymarket_guard.install()
    assert vendor._request is first
    assert getattr(first, "_fail_fast", False)


def test_the_timeouts_are_short(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    http = _http(monkeypatch, _Resp(200, _MARKETS))
    out = vendor.get_prediction_markets("Fed rate cut")
    assert "Fed cut in December?" in out
    assert http.calls[0]["timeout"] == (
        fail_fast.HTTP_CONNECT_TIMEOUT_S,
        fail_fast.HTTP_READ_TIMEOUT_S,
    )
    assert fail_fast.HTTP_READ_TIMEOUT_S <= 5.0
    assert http.calls[0]["url"] == f"{vendor.GAMMA_BASE}/public-search"
    assert sleeps == []


def test_a_timeout_retries_once_then_reports_unavailable(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    http = _http(monkeypatch, requests.Timeout("read timed out"))
    started = time.monotonic()
    out = vendor.get_prediction_markets("recession")
    assert time.monotonic() - started < 1.0  # no real waiting anywhere
    assert len(http.calls) == 2  # the call and its one retry
    assert len(sleeps) == 1 and sleeps[0] <= fail_fast.RETRY_AFTER_CAP_S
    assert "unavailable" in out.lower()
    assert "Proceed without prediction-market signal" in out


def test_a_retry_that_succeeds_returns_the_markets(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    _http(monkeypatch, requests.ConnectionError("reset"), _Resp(200, _MARKETS))
    out = vendor.get_prediction_markets("Fed")
    assert "61%" in out
    assert not polymarket_guard.BREAKER.is_open()


def test_retry_after_is_honoured_only_up_to_the_cap(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    _http(monkeypatch, _Resp(429, headers={"Retry-After": "120"}), _Resp(200, _MARKETS))
    vendor.get_prediction_markets("Fed")
    assert sleeps == [fail_fast.RETRY_AFTER_CAP_S]


def test_a_429_storm_opens_the_breaker_and_stops_calling(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    http = _http(monkeypatch, _Resp(429, headers={"Retry-After": "60"}))
    outputs = [vendor.get_prediction_markets(t) for t in ("Fed", "recession", "election", "oil")]
    # Two failed calls, each with its one retry, then nothing.
    assert len(http.calls) == 2 * polymarket_guard.FAILURE_LIMIT
    assert polymarket_guard.BREAKER.is_open()
    assert all("unavailable" in o.lower() for o in outputs)
    assert "not an absence of markets" in outputs[-1]
    assert sum(sleeps) <= polymarket_guard.FAILURE_LIMIT * fail_fast.RETRY_AFTER_CAP_S


def test_the_breaker_is_shared_by_the_runs_processes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sleeps: list[float]
) -> None:
    monkeypatch.setenv(fail_fast.RUN_STATE_DIR_ENV, str(tmp_path))
    # Another ticker's process already tripped it.
    other_process = fail_fast.RunBreaker("polymarket", polymarket_guard.FAILURE_LIMIT)
    for _ in range(polymarket_guard.FAILURE_LIMIT):
        other_process.record(success=False)
    http = _http(monkeypatch, _Resp(200, _MARKETS))
    out = vendor.get_prediction_markets("Fed")
    assert http.calls == []
    assert "unavailable" in out.lower()


def test_a_client_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    http = _http(monkeypatch, _Resp(400))
    out = vendor.get_prediction_markets("Fed")
    assert len(http.calls) == 1
    assert sleeps == []
    assert "unavailable" in out.lower()


def test_client_errors_never_open_the_breaker(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    # A 400/404 is our query being refused, not Gamma being down.
    http = _http(monkeypatch, _Resp(404))
    for topic in ("a", "b", "c", "d"):
        vendor.get_prediction_markets(topic)
    assert len(http.calls) == 4
    assert not polymarket_guard.BREAKER.is_open()
