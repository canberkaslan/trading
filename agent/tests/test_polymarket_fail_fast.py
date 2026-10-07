"""Polymarket gives up fast and says so (fork patch in the vendored client).

On 2026-10-05 Gamma calls sat on a 30 s timeout each. The client now uses a
(3 s connect, 5 s read) timeout and at most one retry, and when Gamma cannot be
reached the news analyst is told the data is unavailable, never that there are
no markets. No network: `requests.get` is replaced in every test.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import pytest
import requests
import tradingagents.dataflows.config as config_module
from tradingagents import default_config
from tradingagents.dataflows import interface, polymarket

TOPIC = "Fed rate cut"

_MARKETS = {
    "events": [
        {
            "markets": [
                {
                    "question": "Fed cuts in December?",
                    "outcomes": '["Yes", "No"]',
                    "outcomePrices": '["0.62", "0.38"]',
                    "volumeNum": 1_000_000,
                    "endDate": "2030-12-31T00:00:00Z",
                    "closed": False,
                }
            ]
        }
    ]
}


def _response(status: int, body: bytes = b"{}") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    response.url = f"{polymarket.GAMMA_BASE}/public-search"
    response.reason = "test"
    return response


def _ok(data: dict[str, Any]) -> requests.Response:
    return _response(200, json.dumps(data).encode())


class _FakeGet:
    """Stands in for `requests.get`: plays a script of responses/exceptions."""

    def __init__(self, *script: requests.Response | Exception) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.sleeps: list[float] = []

    def __call__(self, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append({"url": url, **kwargs})
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture
def fake_get(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _FakeGet]:
    def install(*script: requests.Response | Exception) -> _FakeGet:
        fake = _FakeGet(*script)
        monkeypatch.setattr(polymarket.requests, "get", fake)
        monkeypatch.setattr(polymarket.time, "sleep", fake.sleeps.append)
        return fake

    return install


def _assert_unavailable(out: str) -> None:
    assert "unavailable" in out.lower()
    assert "not an absence of markets" in out
    assert "No open prediction markets" not in out
    assert TOPIC in out


def test_timeout_is_three_seconds_connect_five_read(fake_get: Callable[..., _FakeGet]) -> None:
    fake = fake_get(_ok(_MARKETS))

    out = polymarket.get_prediction_markets(TOPIC)

    assert "Fed cuts in December?" in out
    assert [c["timeout"] for c in fake.calls] == [(3, 5)]


def test_connection_error_is_retried_once_then_succeeds(fake_get: Callable[..., _FakeGet]) -> None:
    fake = fake_get(requests.ConnectionError("reset"), _ok(_MARKETS))

    out = polymarket.get_prediction_markets(TOPIC)

    assert "Fed cuts in December?" in out
    assert len(fake.calls) == 2
    assert fake.sleeps == [polymarket.RETRY_PAUSE_S]


@pytest.mark.parametrize(
    "first",
    [requests.ConnectTimeout("connect timed out"), requests.ConnectionError("refused")],
)
def test_two_connection_failures_report_unavailable_after_one_retry(
    fake_get: Callable[..., _FakeGet], first: Exception
) -> None:
    fake = fake_get(first, requests.ConnectionError("refused again"))

    out = polymarket.get_prediction_markets(TOPIC)

    _assert_unavailable(out)
    assert len(fake.calls) == 2


def test_read_timeout_is_not_retried(fake_get: Callable[..., _FakeGet]) -> None:
    fake = fake_get(requests.ReadTimeout("read timed out"), _ok(_MARKETS))

    out = polymarket.get_prediction_markets(TOPIC)

    _assert_unavailable(out)
    assert len(fake.calls) == 1
    assert fake.sleeps == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_status_is_retried_once_then_succeeds(
    fake_get: Callable[..., _FakeGet], status: int
) -> None:
    fake = fake_get(_response(status), _ok(_MARKETS))

    out = polymarket.get_prediction_markets(TOPIC)

    assert "Fed cuts in December?" in out
    assert len(fake.calls) == 2


def test_transient_status_twice_reports_unavailable(fake_get: Callable[..., _FakeGet]) -> None:
    fake = fake_get(_response(503), _response(503), _ok(_MARKETS))

    out = polymarket.get_prediction_markets(TOPIC)

    _assert_unavailable(out)
    assert "503" in out
    assert len(fake.calls) == 2


def test_client_error_is_not_retried(fake_get: Callable[..., _FakeGet]) -> None:
    fake = fake_get(_response(404), _ok(_MARKETS))

    out = polymarket.get_prediction_markets(TOPIC)

    _assert_unavailable(out)
    assert len(fake.calls) == 1


def test_non_json_body_reports_unavailable(fake_get: Callable[..., _FakeGet]) -> None:
    fake_get(_response(200, b"<html>gateway</html>"))

    _assert_unavailable(polymarket.get_prediction_markets(TOPIC))


def test_empty_result_still_reads_as_no_markets(fake_get: Callable[..., _FakeGet]) -> None:
    """A real answer with no markets keeps its own wording, distinct from a failure."""
    fake_get(_ok({"events": []}))

    out = polymarket.get_prediction_markets(TOPIC)

    assert "No open prediction markets" in out
    assert "unavailable" not in out.lower()


def test_analyst_tool_route_reports_unavailable(
    fake_get: Callable[..., _FakeGet], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path the news analyst's tool takes: router -> Polymarket -> unavailable."""
    monkeypatch.setattr(
        config_module, "_config", copy.deepcopy(default_config.DEFAULT_CONFIG)
    )
    fake = fake_get(requests.ConnectTimeout("t1"), requests.ConnectTimeout("t2"))

    out = interface.route_to_vendor("get_prediction_markets", TOPIC, None)

    _assert_unavailable(out)
    assert len(fake.calls) == 2


def test_malformed_payload_reports_unavailable_through_the_router(
    fake_get: Callable[..., _FakeGet], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 that is not the expected object degrades to DATA_UNAVAILABLE, not 'no markets'."""
    monkeypatch.setattr(
        config_module, "_config", copy.deepcopy(default_config.DEFAULT_CONFIG)
    )
    fake_get(_response(200, b"[]"))

    out = interface.route_to_vendor("get_prediction_markets", TOPIC, None)

    assert out.startswith("DATA_UNAVAILABLE")
    assert "No open prediction markets" not in out
