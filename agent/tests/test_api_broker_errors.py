"""Broker failures on read-only routes are named, not a bare 5xx.

Cloudflare replaces an origin 502 body with its own page, so during the
Sept 2026 revoked-key window /v1/portfolio/snapshot told the app nothing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from fastapi.testclient import TestClient

from api.broker_errors import broker_http_exception

_REQ = httpx.Request("GET", "https://paper-api.alpaca.markets/v2/account")


def _status_error(status: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError("x", request=_REQ, response=httpx.Response(status, request=_REQ))


@pytest.mark.parametrize(
    ("status", "reason"),
    [(401, "broker_auth_refused"), (403, "broker_auth_refused"), (500, "broker_error")],
)
def test_http_status_is_named_503(status: int, reason: str) -> None:
    exc = broker_http_exception(_status_error(status))
    assert exc.status_code == 503
    assert exc.detail == f"{reason}: alpaca answered {status}"


def test_transport_error_is_unreachable() -> None:
    exc = broker_http_exception(httpx.ConnectError("refused", request=_REQ))
    assert (exc.status_code, exc.detail) == (503, "broker_unreachable")


def test_unknown_error_stays_502_without_echoing_the_message() -> None:
    exc = broker_http_exception(RuntimeError("secret-ish detail https://host/path"))
    assert exc.status_code == 502
    assert exc.detail == "alpaca_error: RuntimeError"


@pytest.mark.parametrize(
    "path", ["/v1/portfolio/snapshot", "/v1/portfolio/concentration", "/v1/risk/stop-coverage"]
)
def test_routes_report_refused_key_as_503(
    path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    from api.deps import get_alpaca
    from api.main import app

    cli = MagicMock()
    cli.account.side_effect = _status_error(401)
    cli.list_positions.side_effect = _status_error(401)
    app.dependency_overrides[get_alpaca] = lambda: cli
    try:
        r = TestClient(app).get(path)
    finally:
        app.dependency_overrides.pop(get_alpaca, None)
    assert r.status_code == 503
    assert r.json()["detail"] == "broker_auth_refused: alpaca answered 401"
    cli.close.assert_called_once()
