"""/v1/me — what the app asks so it stops drawing controls that will 403."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

OPERATOR = "JRpzu78JeXWfOhtuazDYBZ85gq33"


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DEV_API_TOKEN", "t" * 64)
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    monkeypatch.delenv("COGNITO_USER_POOL_ID", raising=False)
    from api.main import app

    return TestClient(app)


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer " + "t" * 64}


def test_requires_authentication(client: TestClient) -> None:
    assert client.get("/v1/me").status_code == 401


def test_reports_the_caller(client: TestClient) -> None:
    body = client.get("/v1/me", headers=_auth()).json()
    assert body["uid"] == "dev-user"


def test_the_shared_bearer_is_not_an_administrator(client: TestClient) -> None:
    assert client.get("/v1/me", headers=_auth()).json()["is_admin"] is False


def test_it_never_returns_the_admin_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A success must not enumerate the privileged accounts any more than a
    # refusal does.
    monkeypatch.setenv("ADMIN_UIDS", f"{OPERATOR},someone-else")
    body = client.get("/v1/me", headers=_auth()).text
    assert OPERATOR not in body
    assert "someone-else" not in body


def test_it_reports_only_two_facts(client: TestClient) -> None:
    # Anything more becomes a directory of who uses the system.
    assert set(client.get("/v1/me", headers=_auth()).json()) == {"uid", "is_admin"}
