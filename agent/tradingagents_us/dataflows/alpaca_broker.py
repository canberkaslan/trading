"""Alpaca broker adapter — paper + live.

Thin REST wrapper over Alpaca's `/v2/*` endpoints. We deliberately avoid
the `alpaca-py` SDK for now to keep dependency surface small and the
behavior auditable. Switch to the SDK if/when WebSocket order streaming
becomes necessary (Phase 4+).

Default endpoint is paper. To go live, override ALPACA_BASE_URL to
`https://api.alpaca.markets/v2`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx

DEFAULT_BASE = "https://paper-api.alpaca.markets/v2"

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit", "stop", "stop_limit", "trailing_stop"]
TimeInForce = Literal["day", "gtc", "opg", "cls", "ioc", "fok"]


@dataclass(frozen=True)
class Account:
    account_number: str
    status: str
    cash: float
    buying_power: float
    portfolio_value: float
    pattern_day_trader: bool
    trading_blocked: bool
    currency: str


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float
    side: str           # "long" | "short"
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float


@dataclass(frozen=True)
class Order:
    id: str
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    filled_qty: float
    order_type: OrderType
    status: str
    submitted_at: datetime
    filled_avg_price: float | None


@dataclass(frozen=True)
class Clock:
    is_open: bool
    timestamp: str
    next_open: str
    next_close: str


class AlpacaClient:
    """REST client for Alpaca brokerage (paper or live)."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        self.api_key = api_key or os.environ["ALPACA_API_KEY"]
        self.api_secret = api_secret or os.environ["ALPACA_API_SECRET"]
        self.base_url = base_url or os.environ.get("ALPACA_BASE_URL", DEFAULT_BASE)
        if not self.base_url.endswith("/v2"):
            # Tolerate users supplying the bare host
            self.base_url = self.base_url.rstrip("/") + "/v2" if "/v2" not in self.base_url else self.base_url
        self._http = httpx.Client(
            timeout=timeout_s,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "accept": "application/json",
                "content-type": "application/json",
            },
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> AlpacaClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ---------------------- account ----------------------

    def account(self) -> Account:
        d = self._get("/account")
        return Account(
            account_number=d["account_number"],
            status=d["status"],
            cash=float(d["cash"]),
            buying_power=float(d["buying_power"]),
            portfolio_value=float(d["portfolio_value"]),
            pattern_day_trader=bool(d.get("pattern_day_trader", False)),
            trading_blocked=bool(d.get("trading_blocked", False)),
            currency=d.get("currency", "USD"),
        )

    def clock(self) -> Clock:
        d = self._get("/clock")
        return Clock(
            is_open=bool(d["is_open"]),
            timestamp=d["timestamp"],
            next_open=d["next_open"],
            next_close=d["next_close"],
        )

    # ---------------------- positions ----------------------

    def list_positions(self) -> list[Position]:
        return [
            Position(
                symbol=p["symbol"],
                qty=float(p["qty"]),
                side=p["side"],
                avg_entry_price=float(p["avg_entry_price"]),
                market_value=float(p["market_value"]),
                unrealized_pl=float(p["unrealized_pl"]),
                unrealized_plpc=float(p["unrealized_plpc"]),
            )
            for p in self._get("/positions")
        ]

    def close_position(self, symbol: str) -> dict:
        return self._delete(f"/positions/{symbol}")

    def close_all_positions(self, cancel_orders: bool = True) -> list[dict]:
        params = "?cancel_orders=true" if cancel_orders else ""
        return self._delete(f"/positions{params}")

    # ---------------------- orders ----------------------

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: Side,
        order_type: OrderType = "market",
        time_in_force: TimeInForce = "day",
        limit_price: float | None = None,
        stop_price: float | None = None,
        client_order_id: str | None = None,
        extended_hours: bool = False,
    ) -> Order:
        body: dict = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "extended_hours": extended_hours,
        }
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        if stop_price is not None:
            body["stop_price"] = str(stop_price)
        if client_order_id is not None:
            body["client_order_id"] = client_order_id

        d = self._post("/orders", body)
        return _order_from_dict(d)

    def get_order(self, order_id: str) -> Order:
        return _order_from_dict(self._get(f"/orders/{order_id}"))

    def list_orders(self, status: str = "open", limit: int = 50) -> list[Order]:
        return [_order_from_dict(o) for o in self._get(f"/orders?status={status}&limit={limit}")]

    def cancel_order(self, order_id: str) -> dict:
        return self._delete(f"/orders/{order_id}")

    def cancel_all_orders(self) -> list[dict]:
        return self._delete("/orders")

    # ---------------------- http helpers ----------------------

    def _get(self, path: str) -> dict | list:
        r = self._http.get(self.base_url + path)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = self._http.post(self.base_url + path, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"alpaca POST {path} failed {r.status_code}: {r.text}")
        return r.json()

    def _delete(self, path: str) -> dict | list:
        r = self._http.delete(self.base_url + path)
        # DELETE positions returns 207 multi-status; tolerate
        if r.status_code >= 400 and r.status_code != 207:
            raise RuntimeError(f"alpaca DELETE {path} failed {r.status_code}: {r.text}")
        try:
            return r.json()
        except Exception:
            return {}


def _order_from_dict(d: dict) -> Order:
    return Order(
        id=d["id"],
        client_order_id=d.get("client_order_id", ""),
        symbol=d["symbol"],
        side=d["side"],
        qty=float(d["qty"]),
        filled_qty=float(d.get("filled_qty", 0)),
        order_type=d.get("type", d.get("order_type", "market")),
        status=d["status"],
        submitted_at=datetime.fromisoformat(d["submitted_at"].replace("Z", "+00:00")),
        filled_avg_price=float(d["filled_avg_price"]) if d.get("filled_avg_price") else None,
    )
