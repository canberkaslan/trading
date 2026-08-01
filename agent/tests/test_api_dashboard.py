"""/dashboard — static web dashboard is served and embeds no secrets."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DEV_API_TOKEN", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    from api.main import app

    return TestClient(app)


def test_dashboard_serves_html(client: TestClient) -> None:
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "TRADER" in r.text  # wordmark
    assert "Portföy" in r.text


def test_dashboard_embeds_no_secrets(client: TestClient) -> None:
    """The page is public — it must contain no token/key material, only the
    client-side token prompt + localStorage flow."""
    body = client.get("/dashboard").text
    for needle in ("DEV_API_TOKEN", "sk-ant", "PKOH", "cfut_", "APCA"):
        assert needle not in body
    assert "localStorage" in body  # token comes from the user, stored locally
