"""AlpacaClient.calendar — the exchange's sessions, as instants in UTC.

`protected_close.missed_exits` decides whether a queued exit met an open by
these instants, so a session must come back at the hour it opened: the
calendar's times are New York's wall clock, on daylight time or not.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient, Session


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> AlpacaClient:
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    return AlpacaClient(base_url="https://paper-api.alpaca.markets/v2")


def _stub(client: AlpacaClient, days: list[dict]) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=days)

    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return seen


def _day(day: str, close: str = "16:00") -> dict:
    return {"date": day, "open": "09:30", "close": close, "session_open": "0400",
            "session_close": "2000", "settlement_date": day}


def test_sessions_come_back_in_utc_daylight_time_or_not(client: AlpacaClient) -> None:
    # 2 October: New York on EDT (UTC-4). 24 December: EST (UTC-5), and an
    # early close.
    seen = _stub(client, [_day("2026-10-02"), _day("2026-12-24", close="13:00")])

    sessions = client.calendar(date(2026, 10, 1), date(2026, 12, 31))

    assert sessions == [
        Session(date(2026, 10, 2), datetime(2026, 10, 2, 13, 30, tzinfo=UTC),
                datetime(2026, 10, 2, 20, 0, tzinfo=UTC)),
        Session(date(2026, 12, 24), datetime(2026, 12, 24, 14, 30, tzinfo=UTC),
                datetime(2026, 12, 24, 18, 0, tzinfo=UTC)),
    ]
    (request,) = seen
    assert request.url.path == "/v2/calendar"
    assert dict(request.url.params) == {"start": "2026-10-01", "end": "2026-12-31"}


def test_an_error_is_raised_not_read_as_no_sessions(client: AlpacaClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    client._http = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        client.calendar(date(2026, 10, 1), date(2026, 10, 2))
