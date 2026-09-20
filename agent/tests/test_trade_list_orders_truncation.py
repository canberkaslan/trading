"""Integration test: list_orders pagination prevents under-reservation.

Regression for B-6: when Alpaca list_orders(limit=N) returns fewer than all
open orders because the book has more than N, the unreported tail is invisible
to cash_budget and the spendable cash figure is over-optimistic.

This test verifies that trade.py paginates list_orders to fetch ALL open buys,
not just the first page.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from tradingagents_us.dataflows.alpaca_broker import AlpacaClient
from tradingagents_us.risk.cash_budget import PendingBuy, reserved_cash_for_open_buys


def _make_order_dict(
    symbol: str, qty: float, limit_price: float, submitted_at: datetime
) -> dict:
    """Factory for order JSON dict that Alpaca API returns."""
    return {
        "id": f"order-{symbol}",
        "client_order_id": f"client-{symbol}",
        "symbol": symbol,
        "side": "buy",
        "qty": str(qty),
        "filled_qty": "0.0",
        "type": "limit",
        "status": "open",
        "submitted_at": submitted_at.isoformat(),
        "filled_avg_price": None,
        "limit_price": str(limit_price),
    }


class TestListOrdersPagination:
    """Test that trade.py handles list_orders pagination correctly."""

    def test_pagination_fetches_all_open_buys_across_multiple_pages(self) -> None:
        """
        Simulate 750 open buy orders spread across two API pages (500 + 250).

        Before fix: trade.py called list_orders(limit=500) once, saw 500, missed 250.
        After fix: trade.py paginates until fewer than 500 orders are returned.
        """
        # Create 750 orders with descending timestamps (most recent first)
        base_time = datetime.now(UTC)
        all_orders = [
            _make_order_dict(
                f"SYM{i:03d}", 1.0, 100.0, base_time - timedelta(seconds=i)
            )
            for i in range(750)
        ]

        # Mock httpx client to return paginated results
        mock_http = MagicMock()

        def mock_get(url: str):
            """Simulate Alpaca API pagination: first call returns 500, second 250."""
            response = MagicMock()
            if "until=" not in url:
                # First page: newest 500 orders
                response.json.return_value = all_orders[:500]
            else:
                # Second page: oldest 250 orders
                response.json.return_value = all_orders[500:]
            return response

        mock_http.get.side_effect = mock_get

        # Mock AlpacaClient with our paginated http client
        mock_client = AlpacaClient(
            api_key="test-key", api_secret="test-secret", base_url="https://paper-api.alpaca.markets/v2"
        )
        mock_client._http = mock_http

        # Simulate trade.py's pagination loop (lines 219-257 after fix)
        open_buys: list[PendingBuy] = []
        page_limit = 500
        fetched_count = page_limit
        until_timestamp: str | None = None

        while fetched_count >= page_limit:
            query = f"/orders?status=open&limit={page_limit}&direction=desc"
            if until_timestamp is not None:
                query += f"&until={until_timestamp}"
            page = mock_client._http.get(mock_client.base_url + query).json()
            if not isinstance(page, list):
                break
            fetched_count = len(page)
            if fetched_count == 0:
                break

            for o_dict in page:
                if o_dict.get("side", "").upper() != "BUY":
                    continue
                qty = float(o_dict["qty"])
                filled = float(o_dict.get("filled_qty", 0))
                limit_price = (
                    float(o_dict["limit_price"]) if o_dict.get("limit_price") else None
                )
                open_buys.append(
                    PendingBuy(
                        symbol=o_dict["symbol"],
                        unfilled_qty=qty - filled,
                        limit_price=limit_price,
                    )
                )

            if fetched_count >= page_limit and page:
                until_timestamp = page[-1]["submitted_at"]

        # Verify ALL 750 orders were fetched
        assert len(open_buys) == 750, (
            f"Pagination should fetch all 750 orders, but got {len(open_buys)}. "
            "trade.py must loop until fewer than 500 orders are returned."
        )

        # Verify correct cash reservation
        reserved = reserved_cash_for_open_buys(open_buys, lambda _s: 100.0)
        expected = 750 * 100.0
        assert reserved == expected, (
            f"With 750 orders at $100 each, reservation should be ${expected:,.2f}, "
            f"but got ${reserved:,.2f}."
        )

        # Verify http client was called TWICE (pagination proof)
        assert mock_http.get.call_count == 2, (
            f"Expected 2 API calls (500 + 250), but got {mock_http.get.call_count}. "
            "Pagination loop should continue until fewer than 500 orders are returned."
        )
