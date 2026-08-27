"""AlpacaClient.list_fill_activities — pagination of the execution feed.

The reconciler's correctness rests on reading EVERY fill: a page silently
dropped means an entry lot goes missing and every round trip after it is
mis-paired. These tests pin the paging contract (token = previous page's last
id, walking forward) against a stubbed transport.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient


def _activity(aid: str, symbol: str = "AAPL", side: str = "buy") -> dict:
    return {
        "id": aid,
        "activity_type": "FILL",
        "symbol": symbol,
        "side": side,
        "qty": "10",
        "price": "100.5",
        "transaction_time": "2026-08-01T14:30:00Z",
        "order_id": "ord-1",
    }


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> AlpacaClient:
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    return AlpacaClient(base_url="https://paper-api.alpaca.markets/v2")


def _stub(client: AlpacaClient, pages: list[list[dict]]) -> list[dict]:
    """Serve `pages` in order, recording each request's query params."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        idx = len(seen) - 1
        return httpx.Response(200, json=pages[idx] if idx < len(pages) else [])

    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    return seen


class TestFillActivities:
    def test_parses_a_single_page(self, client: AlpacaClient) -> None:
        _stub(client, [[_activity("a1")]])

        fills = client.list_fill_activities(page_size=100)

        assert len(fills) == 1
        f = fills[0]
        assert (f.id, f.symbol, f.side, f.qty, f.price) == ("a1", "AAPL", "buy", 10.0, 100.5)
        assert f.transaction_time == datetime(2026, 8, 1, 14, 30, tzinfo=UTC)

    def test_pages_forward_using_the_last_id_as_token(self, client: AlpacaClient) -> None:
        seen = _stub(client, [
            [_activity("a1"), _activity("a2")],
            [_activity("a3")],
        ])

        fills = client.list_fill_activities(page_size=2)

        assert [f.id for f in fills] == ["a1", "a2", "a3"]
        assert "page_token" not in seen[0]
        assert seen[1]["page_token"] == "a2"       # last id of page 1, not an offset
        assert seen[0]["direction"] == "asc"       # token only walks forward when ascending

    def test_stops_on_a_short_page(self, client: AlpacaClient) -> None:
        seen = _stub(client, [[_activity("a1")], [_activity("a2")]])

        fills = client.list_fill_activities(page_size=2)

        assert [f.id for f in fills] == ["a1"]
        assert len(seen) == 1  # a short page is the end; no wasted round trip

    def test_empty_feed_returns_empty(self, client: AlpacaClient) -> None:
        _stub(client, [[]])
        assert client.list_fill_activities() == []

    def test_max_pages_bounds_a_runaway_token(self, client: AlpacaClient) -> None:
        # A server that keeps returning full pages (or a token that fails to
        # advance) must not spin forever.
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[_activity("a1"), _activity("a2")])

        client._http = httpx.Client(transport=httpx.MockTransport(handler))

        fills = client.list_fill_activities(page_size=2, max_pages=3)

        assert len(fills) == 6  # 3 pages × 2, then it gives up

    def test_after_is_sent_as_utc_iso(self, client: AlpacaClient) -> None:
        seen = _stub(client, [[]])

        client.list_fill_activities(after=datetime(2026, 7, 1, tzinfo=UTC))

        assert seen[0]["after"] == "2026-07-01T00:00:00Z"
